"""Read-only MODE1/2352 ISO9660 inventory; this does not decode game scripts."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import struct


def record(data, offset):
    length = data[offset]
    if length < 34 or offset + length > len(data):
        raise ValueError('Invalid ISO directory record')
    name_length = data[offset + 32]
    if 33 + name_length > length:
        raise ValueError('Invalid ISO filename length')
    lba, size = struct.unpack_from('<I', data, offset + 2)[0], struct.unpack_from('<I', data, offset + 10)[0]
    return lba, size, data[offset + 25], data[offset + 33:offset + 33 + name_length]


def inspect(image):
    with image.open('rb') as handle:
        sectors = image.stat().st_size // 2352
        def read(lba, size):
            if lba < 0 or lba + (size + 2047) // 2048 > sectors:
                raise ValueError('Extent outside image')
            out = bytearray()
            while len(out) < size:
                handle.seek(lba * 2352)
                sector = handle.read(2352)
                if len(sector) != 2352 or sector[:12] != b'\x00' + b'\xff' * 10 + b'\x00' or sector[15] != 1:
                    raise ValueError('Expected MODE1/2352 sectors')
                out.extend(sector[16:2064])
                lba += 1
            return bytes(out[:size])
        pvd = read(16, 2048)
        if pvd[:7] != b'\x01CD001\x01':
            raise ValueError('Expected ISO9660 primary volume descriptor')
        root = record(pvd, 156)
        files, visited = [], set()
        def walk(lba, size, parent=''):
            if (lba, size) in visited:
                raise ValueError('Repeated directory extent')
            visited.add((lba, size))
            data, offset = read(lba, size), 0
            while offset < len(data):
                length = data[offset]
                if not length:
                    offset = (offset // 2048 + 1) * 2048
                    continue
                start, size, flags, name = record(data, offset)
                offset += length
                if name in (b'\x00', b'\x01'):
                    continue
                path = parent + '/' + name.decode('ascii').split(';')[0]
                if flags & 2:
                    walk(start, size, path)
                else:
                    row = dict(path=path, lba=start, size=size)
                    if 'TXT' in name.decode('ascii') or 'FONT' in name.decode('ascii'):
                        raw = read(start, size)
                        row.update(sha256=hashlib.sha256(raw).hexdigest(), head_hex=raw[:128].hex(), common_bytes=collections.Counter(raw).most_common(12))
                    files.append(row)
        walk(root[0], root[1])
        return dict(image=str(image.resolve()), volume=pvd[40:72].decode('ascii').strip(), file_count=len(files), files=files)


def self_check():
    data = bytearray(34)
    data[0], data[32], data[33] = 34, 1, 65
    struct.pack_into('<I', data, 2, 44)
    struct.pack_into('<I', data, 10, 821)
    assert record(data, 0) == (44, 821, 0, b'A')
    try:
        record(b'\x22', 0)
    except ValueError:
        pass
    else:
        raise AssertionError('Truncated records must fail')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, nargs='?')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print('self-check passed')
    elif args.image and args.output:
        result = inspect(args.image)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(f"{result['file_count']} files; wrote {args.output}")
    else:
        parser.error('Supply image and --output, or --self-check')
