#!/usr/bin/env python3
"""Encode translated DAT NUL blocks in place, preserving lengths, controls, and source extents."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

from vh_event_text import decode_nodes
from vh_native_dialogue_font import encode_text
from check_translation_capacity import (encoded_bytes, estimated_pixels,
                                        newline_text_groups, record_overflow_segments)

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def action57_peek_lf(raw: bytes) -> list[dict]:
    """Record the parser-visible, non-consuming LF peek after every 0x57."""
    return [{"offset": int(node["start"]),
             "next_byte_is_lf": node["start"] + 1 < len(raw) and raw[node["start"] + 1] == 0x0A}
            for node in decode_nodes(raw)
            if node.get("kind") == "opaque_action" and node.get("opcode") == 0x57]


def verify_action57_peek_lf(source: bytes, rebuilt: bytes) -> list[dict]:
    before = action57_peek_lf(source)
    after = action57_peek_lf(rebuilt)
    if before != after:
        raise ValueError(f"0x57 peek-LF contract changed: {before!r} -> {after!r}")
    return before


def self_check() -> None:
    lf = bytes.fromhex("57 0a a1")
    no_lf = bytes.fromhex("57 a1")
    assert verify_action57_peek_lf(lf, lf) == [{"offset": 0, "next_byte_is_lf": True}]
    assert verify_action57_peek_lf(no_lf, no_lf) == [{"offset": 0, "next_byte_is_lf": False}]
    try:
        verify_action57_peek_lf(no_lf, bytes.fromhex("57 0a"))
    except ValueError:
        pass
    else:
        raise AssertionError("0x57 non-LF peek changed to LF without rejection")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", type=Path, default=ROOT / "translations/candidate-records.jsonl")
    ap.add_argument("--translations", type=Path, default=ROOT / "translations/candidate-record-translation-cache.jsonl")
    ap.add_argument("--mapping", type=Path, required=True, help="approved Unicode-to-event-slot mapping JSON")
    ap.add_argument("--source-dir", type=Path, default=ROOT / "english-extracted")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--require-all", action="store_true", help="require one accepted translation for every input record")
    args = ap.parse_args()

    records = read_jsonl(args.records)
    translated = read_jsonl(args.translations)
    by_id: dict[str, dict] = {}
    for row in translated:
        rid = row.get("record_id")
        if not rid or rid in by_id:
            raise SystemExit(f"missing or duplicate translated record id: {rid}")
        by_id[rid] = row
    mapping_obj = json.loads(args.mapping.read_text(encoding="utf-8"))
    mapping = mapping_obj.get("unicode_to_slot", mapping_obj)
    if len(mapping) != len(set(mapping.values())):
        raise SystemExit("glyph mapping reuses an event slot")

    source_manifest = {row["path"].upper(): row for row in json.loads(
        (args.source_dir / "extract-manifest.json").read_text(encoding="utf-8"))}
    by_file: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    pending, overflow = [], []
    for record in records:
        target = by_id.get(record["id"])
        if target is None:
            pending.append(record["id"])
            continue
        if target.get("cache_key") != record["cache_key"] or target.get("raw_hex") != record["raw_hex"]:
            raise SystemExit(f"stale translation cache row: {record['id']}")
        actual_overflow = record_overflow_segments(record, target.get("segments", {}))
        if actual_overflow:
            overflow.append({"record_id": record["id"], "segment_ids": actual_overflow})
        by_file[record["file"].upper()].append((record, target))
    if args.require_all and pending:
        raise SystemExit(f"{len(pending)} records lack translations; sample: {pending[:5]}")
    if overflow:
        raise SystemExit(f"{len(overflow)} records exceed text capacities; sample: {overflow[:3]}")
    if args.output_dir.exists():
        raise SystemExit(f"output already exists: {args.output_dir}")

    args.output_dir.mkdir(parents=True)
    file_evidence = []
    total_segments = 0
    for filename, work in sorted(by_file.items()):
        manifest_entry = source_manifest.get(filename)
        if manifest_entry is None:
            raise SystemExit(f"source file not pinned in extraction manifest: {filename}")
        rel = filename.lstrip("/")
        original = (args.source_dir / rel).read_bytes()
        if len(original) != manifest_entry["size"] or sha(original) != manifest_entry["sha256"]:
            raise SystemExit(f"pinned source hash/size mismatch: {filename}")
        patched = bytearray(original)
        block_evidence = []
        for record, target in sorted(work, key=lambda pair: int(pair[0]["offset"])):
            offset = int(record["offset"])
            raw = bytes.fromhex(record["raw_hex"])
            if original[offset:offset + len(raw)] != raw:
                raise SystemExit(f"source record identity mismatch: {filename}@{offset:#x}")
            if offset + len(raw) >= len(original) or original[offset + len(raw)] != 0:
                raise SystemExit(f"source NUL delimiter mismatch: {filename}@{offset:#x}")
            if target.get("glossary_sha256") != record.get("glossary_sha256"):
                raise SystemExit(f"glossary identity mismatch: {filename}@{offset:#x}")
            segments = {seg["id"]: seg for seg in record["segments"]}
            targets = target.get("segments", {})
            layout_source = b"".join(bytes.fromhex(node.get("source_hex", node.get("raw_hex", "")))
                                      for node in record["layout"])
            if layout_source != raw:
                raise SystemExit(f"layout/source identity mismatch: {filename}@{offset:#x}")
            layout_text_ids = {node["id"] for node in record["layout"] if node["kind"] == "text"}
            if layout_text_ids != set(segments) or set(targets) != set(segments):
                raise SystemExit(f"text segment identity mismatch: {filename}@{offset:#x}")
            rebuilt = bytearray()
            source_offsets: list[int] = []
            result_offsets: list[int] = []
            linebreak_source_offsets: list[int] = []
            linebreak_result_offsets: list[int] = []
            source_cursor = result_cursor = 0
            groups = newline_text_groups(record["layout"])
            group_ends = {id(group["nodes"][-1]): group for group in groups}
            group_costs = {}
            group_evidence = []
            for group in groups:
                ids = group["text_ids"]
                used = sum(encoded_bytes(targets[sid]) for sid in ids)
                if used > group["byte_budget"]:
                    raise SystemExit(f"newline-group overflow {filename}@{int(record['offset']):#x}: {used}>{group['byte_budget']}")
                if any(estimated_pixels(targets[sid]) > 256 for sid in ids):
                    raise SystemExit(f"dialogue line exceeds 256px {filename}@{int(record['offset']):#x}")
                group_costs[id(group["nodes"][-1])] = group["byte_budget"] - used
                group_evidence.append({
                    "text_ids_in_order": ids,
                    "source_text_bytes": group["byte_budget"],
                    "target_text_bytes": used,
                    "linebreak_count": group["linebreak_count"],
                    "line_pixels": [estimated_pixels(targets[sid]) for sid in ids],
                    "capacity_pass": True,
                })
            for node in record["layout"]:
                source_offsets.append(source_cursor)
                result_offsets.append(result_cursor)
                if node["kind"] == "text":
                    text = targets.get(node["id"])
                    if not isinstance(text, str) or not text:
                        raise SystemExit(f"missing translated text {filename}@{offset:#x}/{node['id']}")
                    encoded = encode_text(text, mapping)
                    rebuilt.extend(encoded)
                    total_segments += 1
                else:
                    raw_node = bytes.fromhex(node["raw_hex"])
                    if node["kind"] == "linebreak":
                        linebreak_source_offsets.append(source_cursor)
                        linebreak_result_offsets.append(result_cursor)
                    rebuilt.extend(raw_node)
                source_cursor += len(bytes.fromhex(node.get("source_hex", node.get("raw_hex", ""))))
                result_cursor = len(rebuilt)
                if id(node) in group_costs:
                    padding = group_costs[id(node)]
                    rebuilt.extend(bytes([0xA0]) * padding)
                    result_cursor = len(rebuilt)
            if len(rebuilt) != len(raw):
                raise SystemExit(f"record length changed: {filename}@{offset:#x}")
            content_kinds = {"text", "custom_glyph"}
            source_controls = [(n["kind"], n["raw_hex"]) for n in decode_nodes(raw)
                               if n["kind"] not in content_kinds]
            result_controls = [(n["kind"], n["raw_hex"]) for n in decode_nodes(bytes(rebuilt))
                               if n["kind"] not in content_kinds]
            if source_controls != result_controls:
                raise SystemExit(f"opaque/control sequence changed: {filename}@{offset:#x}")
            try:
                action57_evidence = verify_action57_peek_lf(raw, bytes(rebuilt))
            except ValueError as exc:
                raise SystemExit(f"{filename}@{offset:#x}: {exc}") from exc
            source_linebreaks = [node["raw_hex"] for node in record["layout"]
                                 if node["kind"] == "linebreak"]
            target_linebreaks = [node["raw_hex"] for node in record["layout"]
                                 if node["kind"] == "linebreak"]
            if source_linebreaks != target_linebreaks:
                raise SystemExit(f"linebreak count/order changed: {filename}@{offset:#x}")
            # Newlines are cursor-render commands and may shift inside a group.
            # Opaque/action/parameter/glyph bytes stay at their exact offsets.
            immutable_kinds = {"opaque_action", "parameter_control", "opaque_byte",
                               "opaque_control", "custom_glyph"}
            old_immutable = []
            new_immutable = []
            old_offset = new_offset = 0
            for node in record["layout"]:
                size = len(bytes.fromhex(node.get("source_hex", node.get("raw_hex", ""))))
                if node["kind"] in immutable_kinds:
                    old_immutable.append((node["kind"], old_offset,
                                          bytes.fromhex(node.get("source_hex", node.get("raw_hex", "")))))
                    new_immutable.append((node["kind"], new_offset,
                                          bytes.fromhex(node.get("source_hex", node.get("raw_hex", "")))))
                old_offset += size
                new_offset += size if node["kind"] != "text" else len(encode_text(targets[node["id"]], mapping))
                if id(node) in group_costs:
                    new_offset += group_costs[id(node)]
            if old_immutable != new_immutable:
                raise SystemExit(f"opaque/control byte offsets moved: {filename}@{offset:#x}")
            patched[offset:offset + len(raw)] = rebuilt
            block_evidence.append({"id": record["id"], "offset": offset,
                                   "source_sha256": sha(raw), "target_sha256": sha(bytes(rebuilt)),
                                   "bytes": len(raw), "controls_preserved": True,
                                   "nul_offset_preserved": offset + len(rebuilt),
                                   "text_group_count": len(group_evidence),
                                   "newline_group_capacity_and_width": group_evidence,
                                   "control_sequence_sha256": sha(json.dumps(source_controls,
                                                                              separators=(",", ":")).encode()),
                                   "linebreak_count_and_order_preserved": True,
                                   "newline_count": len(linebreak_source_offsets),
                                   "newline_offsets_moved": linebreak_source_offsets != linebreak_result_offsets,
                                   "action57_peek_lf_preserved": True,
                                   "action57_peek_lf": action57_evidence,
                                   "opaque_action_parameter_offsets_preserved": True})
        outpath = args.output_dir / rel
        outpath.parent.mkdir(parents=True, exist_ok=True)
        outpath.write_bytes(patched)
        file_evidence.append({"file": filename, "source_sha256": sha(original),
                              "target_sha256": sha(bytes(patched)), "bytes": len(original),
                              "record_count": len(block_evidence), "records": block_evidence})

    if args.require_all and set(by_id) != {record["id"] for record in records}:
        raise SystemExit("translation cache contains records outside the declared input set")
    report = {"format": "vh-in-place-translated-dat-pool-v1", "status": "PASS",
              "mapping_sha256": sha(args.mapping.read_bytes()), "record_input_count": len(records),
              "translated_record_count": sum(len(v) for v in by_file.values()),
              "pending_record_count": len(pending), "translated_text_segment_count": total_segments,
              "source_extract_manifest_sha256": sha((args.source_dir / "extract-manifest.json").read_bytes()),
              "files": file_evidence}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "files": len(file_evidence),
                      "translated_records": report["translated_record_count"],
                      "pending": len(pending), "segments": total_segments,
                      "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    if "--self-check" in __import__("sys").argv:
        self_check()
        print("PASS: 0x57 peek-LF behavior preserved")
        raise SystemExit(0)
    main()
