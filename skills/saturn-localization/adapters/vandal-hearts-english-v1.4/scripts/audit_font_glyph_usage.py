#!/usr/bin/env python3
"""Audit protected source glyph codes and translated glyph capacity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()
from vh_native_dialogue_font import EVENT_PAIR_SLOTS, normalize_visible_punctuation, slot_from_pair


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pairs_from_hex(value: str, counts: Counter[int]) -> None:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return
    index = 0
    while index + 1 < len(raw):
        lead = raw[index]
        if 0x81 <= lead <= 0x9F or 0xE0 <= lead <= 0xEA:
            try:
                slot = slot_from_pair(raw[index:index + 2])
            except ValueError:
                index += 1
                continue
            counts[slot] += 1
            index += 2
        else:
            index += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spans", type=Path, default=ROOT / "translations/candidate-text-spans.jsonl")
    ap.add_argument("--ui-metadata", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/row-byte-metadata.jsonl")
    ap.add_argument("--ui-spans", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/ui-translation-drafts.jsonl")
    ap.add_argument("--candidate-blocks", type=Path, default=ROOT / "analysis/text-coverage/candidate-unverified.jsonl")
    ap.add_argument("--translations", type=Path, default=ROOT / "translations/candidate-translation-cache.jsonl")
    ap.add_argument("--summary", type=Path, default=ROOT / "analysis/text-coverage/summary.json")
    ap.add_argument("--unclassified-files", type=Path, default=ROOT / "analysis/text-coverage/unclassified-files.jsonl")
    ap.add_argument("--mapping", type=Path, help="optional final Unicode-to-slot JSON for collision checks")
    ap.add_argument("--generate-mapping", type=Path,
                    help="write a deterministic draft map for every translated non-ASCII/special character")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    spans = read_jsonl(args.spans)
    ui_spans = read_jsonl(args.ui_spans)
    ui_metadata = read_jsonl(args.ui_metadata)
    candidate_blocks = read_jsonl(args.candidate_blocks)
    protected_pairs: Counter[int] = Counter()
    source_ascii: set[str] = set()
    for row in spans:
        pairs_from_hex(str(row.get("source_hex", "")), protected_pairs)
        source_ascii.update(char for char in str(row.get("source_text", ""))
                            if 0x20 <= ord(char) <= 0x7E)
    for row in ui_metadata:
        pairs_from_hex(str(row.get("raw_text_region_hex", "")), protected_pairs)
        source_ascii.update(char for char in str(row.get("decoded_candidate", ""))
                            if 0x20 <= ord(char) <= 0x7E)
    for row in ui_spans:
        source_ascii.update(char for char in str(row.get("source", ""))
                            if 0x20 <= ord(char) <= 0x7E)

    # The source's visible ASCII path is confirmed at row 0x22. Reserve the
    # complete printable row so future target punctuation/digits cannot collide.
    protected_ascii = list(range(94, 188))
    protected_prefix = list(range(188))  # preserve the full symbol and ASCII rows conservatively
    cache = read_jsonl(args.translations) if args.translations.exists() else []
    targets = {normalize_visible_punctuation(row["target"])
               for row in cache if isinstance(row.get("target"), str)}
    supported_event_single = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz !\"#='(),-./01235678:?") | set("09")
    custom_chars = sorted({char for target in targets for char in target
                           if char not in supported_event_single and char not in "\r\n\t"})

    mapping = None
    if args.mapping:
        mapping_obj = json.loads(args.mapping.read_text(encoding="utf-8"))
        mapping = mapping_obj.get("unicode_to_slot", mapping_obj)
        if not isinstance(mapping, dict):
            raise SystemExit("mapping must be a JSON object")
        slots = list(mapping.values())
        if len(slots) != len(set(slots)):
            raise SystemExit("mapping contains duplicate slots")
        protected = set(protected_prefix) | set(protected_pairs)
        collisions = sorted((char, slot) for char, slot in mapping.items() if slot in protected)
        if collisions:
            raise SystemExit(f"mapping collides with protected source glyphs: {collisions[:10]}")
        absent = sorted(set(custom_chars) - set(mapping))
    else:
        absent = custom_chars
    unsupported = absent
    generated_mapping_path = None
    if args.generate_mapping:
        free_slots = [slot for slot in range(EVENT_PAIR_SLOTS)
                      if slot not in set(protected_prefix) | set(protected_pairs)]
        if len(custom_chars) > len(free_slots):
            raise SystemExit(f"translation needs {len(custom_chars)} slots; only {len(free_slots)} are free")
        generated = {char: slot for char, slot in zip(custom_chars, free_slots)}
        args.generate_mapping.parent.mkdir(parents=True, exist_ok=True)
        args.generate_mapping.write_text(json.dumps({"unicode_to_slot": generated},
                                                     ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        generated_mapping_path = str(args.generate_mapping)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    unclassified = read_jsonl(args.unclassified_files)
    unclassified_extensions = Counter(str(row.get("extension", "unknown")) for row in unclassified)
    observed_ascii_slots = sorted(94 + ord(char) - 0x21 for char in source_ascii if 0x21 <= ord(char) <= 0x7E)
    report = {
        "format": "vh-source-glyph-usage-audit-v1",
        "source_scan": {
            "candidate_text_spans": len(spans),
            "candidate_text_files": summary["source"]["candidate_files"],
            "candidate_nul_blocks": len(candidate_blocks),
            "fixed_ui_translation_spans": len(ui_spans),
            "fixed_ui_slot_rows_with_bytes": len(ui_metadata),
            "unclassified_manifest_files": summary["source"]["unclassified_files"],
            "unclassified_extensions": dict(sorted(unclassified_extensions.items())),
            "candidate_unverified_only": True,
            "unclassified_text_and_image_assets": "not glyph-scanned; protected as pending coverage",
            "bitmap_icons_and_8x8_HUD": "not part of 16x16 dialogue glyph map; separate audit pending",
        },
        "protected_source_glyphs": {
            "ascii_row": "0x22",
            "ascii_slots": protected_ascii,
            "protected_prefix_slots": protected_prefix,
            "protected_prefix_policy": "preserve row 0x21 symbols plus row 0x22 printable ASCII until all binary-only UI/icon readers are classified",
            "observed_source_ascii_characters": "".join(sorted(source_ascii)),
            "observed_source_ascii_slots": observed_ascii_slots,
            "ascii_policy": "preserve full printable ASCII row (94 slots), including punctuation, digits, fixed UI labels and Latin names",
            "source_pair_slots_observed": sorted(protected_pairs),
            "source_pair_occurrences": sum(protected_pairs.values()),
            "pair_source_scopes": ["candidate text spans", "fixed UI raw text regions"],
        },
        "translation_capacity": {
            "reachable_event_pair_slots": EVENT_PAIR_SLOTS,
            "reserved_ascii_slots": len(protected_ascii),
            "reserved_source_pair_slots": len(set(protected_pairs) - set(protected_ascii)),
            "available_custom_slots": EVENT_PAIR_SLOTS - len(set(protected_prefix) | set(protected_pairs)),
            "translated_unique_texts_in_cache": len(cache),
            "translated_unique_non_ascii_characters": len(custom_chars),
            "unsupported_or_unmapped_event_characters": unsupported,
            "mapping_path": str(args.mapping) if args.mapping else None,
            "generated_mapping_path": generated_mapping_path,
            "mapped_characters_missing_from_mapping": absent,
            "mapping_sha256": hashlib.sha256(args.mapping.read_bytes()).hexdigest() if args.mapping else None,
        },
        "status": "PASS" if mapping is not None and not absent and not unsupported else "AUDIT_ONLY",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": report["status"],
                      "protected_ascii": len(protected_ascii),
                      "protected_pairs": len(protected_pairs),
                      "translated_custom_chars": len(custom_chars),
                      "capacity": report["translation_capacity"]["available_custom_slots"],
                      "unmapped_char_count": len(unsupported),
                      "unmapped_sample": unsupported[:20]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
