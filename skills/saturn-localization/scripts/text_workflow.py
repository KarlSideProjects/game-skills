"""Stdlib JSONL translation jobs and checked, fixed-slot file extraction/reinsertion."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

TOKENS = re.compile(r'⟦[A-Za-z0-9_]+⟧')
ZH_HANT = 'zh-Hant'
CODECS = {'ascii', 'latin-1', 'shift_jis', 'cp932', 'utf-8'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(path, require_source=True):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [r['id'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate record IDs')
    for row in rows:
        if not isinstance(row['id'], str) or (require_source and not isinstance(row['source'], str)):
            raise ValueError('IDs and source text must be strings')
    return rows


def write_rows(path, rows):
    # Exclusive creation keeps existing work intact on accidental repeated commands.
    with Path(path).open('x') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def validate_namespace(namespace, rows=()):
    # Keep historical <source>-zh-Hant:<model>:<glossary> cache keys stable.
    if not isinstance(namespace, str) or not re.match(r'^[^:]+-zh-Hant:[^:]+:[^:]+$', namespace):
        raise ValueError('Translation namespace must be <source>-zh-Hant:<model>:<glossary>')
    for row in rows:
        declared = row.get('target_language')
        if declared is not None and declared != ZH_HANT:
            raise ValueError('Only Traditional Chinese (zh-Hant) targets are accepted')


def validate_translation_rows(rows):
    for row in rows:
        locale = row.get('target_language')
        namespace = row.get('namespace', '')
        if namespace:
            validate_namespace(namespace, [row])
            namespace_locale = ZH_HANT
        else:
            namespace_locale = None
        if locale is None:
            locale = namespace_locale
        if locale != ZH_HANT:
            raise ValueError('Translation record must declare zh-Hant or use a zh-Hant namespace')

def job_key(row, namespace):
    data = [namespace, row['source'], row.get('context', {})]
    return digest(json.dumps(data, ensure_ascii=False, sort_keys=True).encode())


def verify_target(source, target):
    if not isinstance(target, str) or (source.strip() and not target.strip()):
        raise ValueError('Missing or empty translation')
    if TOKENS.findall(source) != TOKENS.findall(target):
        raise ValueError('Control placeholders changed or reordered')


def translation_segments(source):
    """Return text segments around control tokens; tokens keep exact positions."""
    parts, cursor = [], 0
    for match in TOKENS.finditer(source):
        parts.append(dict(kind='text', source=source[cursor:match.start()]))
        parts.append(dict(kind='control', source=match.group(0)))
        cursor = match.end()
    parts.append(dict(kind='text', source=source[cursor:]))
    return parts

def rebuild_segments(source, translated_texts):
    parts = translation_segments(source)
    expected = sum(part['kind'] == 'text' for part in parts)
    if len(translated_texts) != expected or any(not isinstance(value, str) for value in translated_texts):
        raise ValueError('Translated text segment count mismatch')
    out, index = [], 0
    for part in parts:
        if part['kind'] == 'control':
            out.append(part['source'])
        else:
            out.append(translated_texts[index])
            index += 1
    return ''.join(out)

def plan(records, namespace, translations=()):
    validate_namespace(namespace, list(records) + list(translations))
    cached = {}
    for row in translations:
        key = row['id']
        if row.get('namespace') != namespace or key != job_key(row, namespace):
            raise ValueError('Translation cache namespace or content mismatch')
        verify_target(row['source'], row['target'])
        if key in cached and cached[key]['target'] != row['target']:
            raise ValueError('Conflicting translations')
        cached[key] = row
    jobs = {}
    for row in records:
        key = job_key(row, namespace)
        if key not in cached:
            jobs[key] = dict(id=key, source=row['source'], context=row.get('context', {}), namespace=namespace,
                             target_language=ZH_HANT)
    return list(jobs.values()), cached


def accept(jobs, answers):
    if jobs:
        validate_namespace(jobs[0]['namespace'], jobs)
    by_id = {job['id']: job for job in jobs}
    if len(by_id) != len(jobs) or len({r['id'] for r in answers}) != len(answers):
        raise ValueError('Duplicate job or answer IDs')
    accepted = []
    for answer in answers:
        job = by_id.get(answer['id'])
        if job is None or job['id'] != job_key(job, job['namespace']):
            raise ValueError('Unknown or invalid translation job')
        verify_target(job['source'], answer.get('target'))
        accepted.append(dict(job, target=answer['target']))
    return accepted


def merge(records, translations, namespace):
    validate_namespace(namespace, list(records) + list(translations))
    missing, cached = plan(records, namespace, translations)
    if missing:
        raise ValueError(f'{len(missing)} translation jobs missing')
    return [dict(row, target=cached[job_key(row, namespace)]['target'], namespace=namespace,
                 target_language=ZH_HANT) for row in records]


def file_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root) or path == root:
        raise ValueError('File path must stay inside extracted-file directory')
    return path


def encoding(profile):
    if 'table' not in profile:
        codec = profile['codec']
        if codec not in CODECS:
            raise ValueError('Unsupported codec; use a custom adapter')
        return lambda raw: raw.decode(codec), lambda text: text.encode(codec)
    table = [(bytes.fromhex(k), v) for k, v in profile['table'].items()]
    if not table or any(not k or b'\0' in k or not isinstance(v, str) or not v for k, v in table):
        raise ValueError('Invalid table entries or embedded NUL')
    if len({k for k, _ in table}) != len(table) or len({v for _, v in table}) != len(table):
        raise ValueError('Table must be reversible')
    for i, (key, _) in enumerate(table):
        if any(other.startswith(key) for j, (other, _) in enumerate(table) if j != i):
            raise ValueError('Byte table must be prefix-free')
    def convert(value, pairs):
        result, offset = [], 0
        while offset < len(value):
            for source, target in pairs:
                if value.startswith(source, offset):
                    result.append(target)
                    offset += len(source)
                    break
            else:
                raise ValueError(f'Unmapped symbol at offset {offset}')
        return (b'' if isinstance(value, str) else '').join(result)
    inverse = sorted(((v, k) for k, v in table), key=lambda pair: len(pair[0]), reverse=True)
    return lambda raw: convert(raw, table), lambda text: convert(text, inverse)


def extract(root, profile):
    decode, encode = encoding(profile)
    rows, extents, files = [], {}, {}
    if not profile['slots']:
        raise ValueError('Profile has no slots')
    for slot in profile['slots']:
        name, start, capacity = slot['file'], slot['offset'], slot['capacity']
        if not isinstance(start, int) or not isinstance(capacity, int) or start < 0 or capacity < 1:
            raise ValueError('Invalid slot extent')
        if name not in files:
            files[name] = file_path(root, name).read_bytes()
            if digest(files[name]) != profile['files'][name]:
                raise ValueError(f'Input hash mismatch: {name}')
        data, end = files[name], start + capacity
        if end > len(data) or any(start < b and a < end for a, b in extents.get(name, [])):
            raise ValueError('Slot outside file or overlapping another slot')
        extents.setdefault(name, []).append((start, end))
        raw = data[start:end]
        body, sep, padding = raw.partition(b'\0')
        if not sep or any(padding):
            raise ValueError('Expected NUL terminator and zero padding; use a custom adapter')
        source = decode(body)
        if encode(source) != body:
            raise ValueError('Unmodified text does not round-trip')
        rows.append(dict(id=slot['id'], source=source, context=slot.get('context', {}), file=name,
                         offset=start, capacity=capacity, raw_hex=raw.hex()))
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate slot IDs')
    return rows


def patch(root, profile, records, output):
    validate_translation_rows(records)
    baseline = extract(root, profile)
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate translated IDs')
    by_id = {r['id']: r for r in records}
    if set(by_id) != {r['id'] for r in baseline}:
        raise ValueError('Translated record set differs from profile')
    _, encode = encoding(profile)
    changed = {}
    for original in baseline:
        row = by_id[original['id']]
        if any(row.get(key) != value for key, value in original.items()):
            raise ValueError('Source text or location metadata changed')
        target = row.get('target')
        verify_target(original['source'], target)
        encoded = encode(target)
        if b'\0' in encoded or len(encoded) + 1 > original['capacity']:
            raise ValueError(f"Embedded NUL or slot overflow: {row['id']}")
        name = original['file']
        if name not in changed:
            changed[name] = bytearray(file_path(root, name).read_bytes())
        start, capacity = original['offset'], original['capacity']
        changed[name][start:start + capacity] = encoded + bytes(capacity - len(encoded))
    # Validate every slot before creating output; only extracted files are written.
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in changed.items():
        target = file_path(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return len(changed)


def self_check():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        data = b'HEADhello\0\0\0TAIL'
        (root / 'DATA.BIN').write_bytes(data)
        profile = dict(codec='ascii', files={'DATA.BIN': digest(data)}, slots=[dict(id='a', file='DATA.BIN', offset=4, capacity=8)])
        rows = extract(root, profile)
        jobs, _ = plan(rows + [dict(rows[0], id='b')], 'en-zh-Hant:test:v1')
        assert len(jobs) == 1
        cached = accept(jobs, [dict(id=jobs[0]['id'], target='hi')])
        records = merge(rows, cached, 'en-zh-Hant:test:v1')
        assert plan(rows, 'en-zh-Hant:test:v1', cached)[0] == []
        patch(root, profile, [dict(rows[0], target=rows[0]['source'], namespace='en-zh-Hant:test:v1')], root / 'identity')
        assert (root / 'identity/DATA.BIN').read_bytes() == data
        patch(root, profile, records, root / 'out')
        assert (root / 'out/DATA.BIN').read_bytes() == b'HEADhi\0\0\0\0\0\0TAIL'
        assert (root / 'DATA.BIN').read_bytes() == data
        for operation in [lambda: patch(root, profile, [dict(records[0], target='too long')], root / 'bad'),
                          lambda: patch(root, profile, [dict(rows[0], target='x')], root / 'no-locale'),
                          lambda: verify_target('x⟦WAIT⟧⟦END⟧', 'x⟦END⟧⟦WAIT⟧'),
                          lambda: merge(rows, [], 'en-zh-Hant:test:v1'),
                          lambda: merge(rows, [dict(jobs[0], target='hi')], 'en-zh-Hant:test:v2'),
                          lambda: plan(rows, 'en-ko:test:v1'),
                          lambda: plan(rows, 'en-zh-Hans:test:v1'),
                          lambda: plan([dict(rows[0], target_language='ko')], 'en-zh-Hant:test:v1'),
                          lambda: validate_translation_rows([dict(records[0], target_language='zh-Hant', namespace='en-ko:test:v1')]),
                          lambda: verify_target('x⟦WAIT⟧y', 'xy'),
                          lambda: rebuild_segments('a⟦WAIT⟧b', ['甲']),
                          lambda: file_path(root, '../escape'),
                          lambda: accept(jobs, [dict(id='unknown', target='hi')]),
                          lambda: extract(root, dict(profile, files={'DATA.BIN': 'wrong'})),
                          lambda: extract(root, dict(profile, slots=profile['slots'] * 2))]:
            try:
                operation()
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid operation must fail')
        table_profile = dict(table={'a1': 'A', 'a0': ' ', 'fa': '⟦WAIT⟧'})
        decode, encode = encoding(table_profile)
        assert decode(b'\xa1\xa0\xfa') == 'A ⟦WAIT⟧'
        assert encode('A ⟦WAIT⟧') == b'\xa1\xa0\xfa'
        assert rebuild_segments('a⟦WAIT⟧b⟦END⟧', ['甲', '乙', '']) == '甲⟦WAIT⟧乙⟦END⟧'
        print('self-check passed: zh-Hant namespace, strict controls, segment rebuild, round-trip, dedup, cache, bounds, hashes, source preservation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('self-check')
    command = sub.add_parser('accept')
    command.add_argument('--jobs', required=True)
    command.add_argument('--answers', required=True)
    command.add_argument('--output', required=True)
    for name in ['extract', 'patch']:
        command = sub.add_parser(name)
        command.add_argument('--root', required=True)
        command.add_argument('--profile', required=True)
        command.add_argument('--output', required=True)
        if name == 'patch':
            command.add_argument('--records', required=True)
    for name in ['plan', 'merge']:
        command = sub.add_parser(name)
        command.add_argument('--records', required=True)
        command.add_argument('--namespace', required=True)
        command.add_argument('--translations')
        command.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'self-check':
        self_check()
    elif args.command == 'accept':
        rows = accept(read_rows(args.jobs), read_rows(args.answers, require_source=False))
        write_rows(args.output, rows)
        print(json.dumps(dict(accepted=len(rows))))
    elif args.command in ['extract', 'patch']:
        profile = json.loads(Path(args.profile).read_text())
        if args.command == 'extract':
            rows = extract(args.root, profile)
            write_rows(args.output, rows)
            print(json.dumps(dict(records=len(rows))))
        else:
            print(json.dumps(dict(files_written=patch(args.root, profile, read_rows(args.records), args.output))))
    else:
        records = read_rows(args.records)
        translations = read_rows(args.translations) if args.translations else []
        if args.command == 'plan':
            jobs, _ = plan(records, args.namespace, translations)
            write_rows(args.output, jobs)
            print(json.dumps(dict(records=len(records), pending_unique_jobs=len(jobs))))
        else:
            write_rows(args.output, merge(records, translations, args.namespace))
            print(json.dumps(dict(records=len(records))))


if __name__ == '__main__':
    main()
