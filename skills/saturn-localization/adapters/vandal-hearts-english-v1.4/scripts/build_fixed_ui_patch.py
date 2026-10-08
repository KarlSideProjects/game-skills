#!/usr/bin/env python3
"""Build guarded in-place fixed-UI and main-menu text files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from vh_native_dialogue_font import encode_text, normalize_visible_punctuation

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()
DEFAULT_TRANSLATIONS = ROOT / "translations/ui-fixed-translations.draft.jsonl"
DEFAULT_SOURCES = ROOT / "analysis/text-coverage/translation-drafts/ui-translation-drafts.jsonl"
DEFAULT_METADATA = ROOT / "analysis/text-coverage/translation-drafts/row-byte-metadata.jsonl"
DEFAULT_EXTRA = ROOT / "analysis/text-coverage/additional-menu-candidate-spans.jsonl"
MANIFEST = ROOT / "english-extracted/extract-manifest.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(mapping_path: Path, output_dir: Path, translations_path: Path = DEFAULT_TRANSLATIONS,
          sources_path: Path = DEFAULT_SOURCES, metadata_path: Path = DEFAULT_METADATA,
          extra_path: Path = DEFAULT_EXTRA) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    mapping_obj = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping = mapping_obj.get("unicode_to_slot", mapping_obj)
    if not isinstance(mapping, dict):
        raise ValueError("mapping must be a Unicode-to-codec-slot object")
    translations = read_jsonl(translations_path)
    sources = {row["id"]: row for row in read_jsonl(sources_path)}
    extra = {row["id"]: row for row in read_jsonl(extra_path)}
    metadata = {row["row_id"]: row for row in read_jsonl(metadata_path)}
    manifest = {row["path"]: row for row in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    working: dict[str, bytearray] = {}
    source_hashes: dict[str, str] = {}
    edits: list[dict[str, Any]] = []

    def source_blob(rel: str) -> bytearray:
        if rel not in working:
            path = ROOT / "english-extracted" / rel.lstrip("/")
            original = path.read_bytes()
            pinned = manifest.get(rel)
            if pinned is None or pinned.get("size") != len(original) or pinned.get("sha256") != sha(original):
                raise ValueError(f"extracted source does not match pinned manifest: {rel}")
            working[rel] = bytearray(original)
            source_hashes[rel] = sha(original)
        return working[rel]

    seen_ranges: dict[str, list[tuple[int, int]]] = {}
    for row in translations:
        row_id = row["id"]
        if row_id in extra:
            source = extra[row_id]
            rels = [source["source_file"]]
            offset = int(source["source_offset"])
            expected = bytes.fromhex(source["source_hex"])
            capacity = int(source["byte_budget"])
        else:
            source = sources.get(row_id)
            if source is None:
                raise ValueError(f"translation has no source inventory row: {row_id}")
            meta = metadata[source["row_id"]]
            rel = meta["files"][0]
            offset = int(meta["offset"]) + int(source["byte_start"])
            span_size = int(source["byte_end_exclusive"]) - int(source["byte_start"])
            if "text_capacity_bytes" in meta:
                capacity = int(meta["text_capacity_bytes"])
                expected = bytes.fromhex(meta["raw_text_region_hex"])[int(source["byte_start"]):
                                                                         int(source["byte_start"]) + span_size]
                if int(source["span_index"]) != 0 or len(meta.get("span_ids", [])) > 1:
                    # Segmented NUL strings have per-span capacity only; fixed slots are single-span.
                    capacity = span_size
            else:
                capacity = span_size
                expected = bytes.fromhex(meta["raw_text_hex"])[int(source["byte_start"]):
                                                               int(source["byte_start"]) + span_size]
            rels = list(meta["files"])
        encoded = encode_text(row["target"], mapping)
        if len(encoded) > capacity:
            raise ValueError(f"capacity overflow: {row_id}: {len(encoded)}>{capacity}")
        pad = b"\xA0" * (capacity - len(encoded))
        replacement = encoded + pad
        # Fixed UI tables are mirrored in both 0.BIN and 1.BIN. PRG menu spans
        # have one source file. For segmented NUL strings, write only the
        # visible span and leave the 0A/NUL nodes at their original offsets.
        for rel in rels:
            blob = source_blob(rel)
            if offset < 0 or offset + len(expected) > len(blob) or bytes(blob[offset:offset + len(expected)]) != expected:
                raise ValueError(f"source span bytes differ from pinned candidate: {rel}@{offset:#x} {row_id}")
            end = offset + capacity
            for old_start, old_end in seen_ranges.setdefault(rel, []):
                if max(old_start, offset) < min(old_end, end):
                    raise ValueError(f"overlapping UI edits in {rel}: {row_id}")
            seen_ranges[rel].append((offset, end))
            blob[offset:end] = replacement
            edits.append({"id": row_id, "file": rel, "offset": hex(offset),
                          "source_bytes": len(expected), "capacity_bytes": capacity,
                          "target_bytes": len(encoded), "pad_bytes": len(pad),
                          "source_text": row["source_text"], "target": row["target"],
                          "encoded_target": normalize_visible_punctuation(row["target"])})

    output_dir.mkdir(parents=True)
    outputs = {}
    for rel, data in working.items():
        dest = output_dir / rel.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        outputs[rel] = {"bytes": len(data), "source_sha256": source_hashes[rel],
                        "output_sha256": sha(bytes(data))}
    result = {"format": "vh-fixed-ui-patch-v1", "status": "PASS",
              "translation_count": len(translations), "edit_count": len(edits),
              "mapping_sha256": sha(mapping_path.read_bytes()), "files": outputs, "edits": edits}
    (output_dir / "fixed-ui-patch-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    ap.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    ap.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    ap.add_argument("--extra-sources", type=Path, default=DEFAULT_EXTRA)
    args = ap.parse_args()
    report = build(args.mapping, args.output_dir, args.translations, args.sources,
                   args.metadata, args.extra_sources)
    print(json.dumps({key: report[key] for key in ("status", "translation_count", "edit_count", "files")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
