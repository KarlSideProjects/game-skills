#!/usr/bin/env python3
"""Check fixed-UI and PRG-menu translations against source field budgets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from vh_native_dialogue_font import normalize_visible_punctuation

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def target_cost(text: str) -> tuple[int, int]:
    """Native event-pool cost: ASCII/special source glyph=1B/8px; CJK=2B/16px."""
    text = normalize_visible_punctuation(text)
    byte_count = pixels = 0
    for char in text:
        if char in "\r\n\t":
            byte_count += 1
        elif ord(char) < 0x80:
            byte_count += 1
            pixels += 8
        else:
            byte_count += 2
            pixels += 16
    return byte_count, pixels


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(translations: list[dict[str, Any]], ui_sources: list[dict[str, Any]],
          metadata: list[dict[str, Any]], extra_sources: list[dict[str, Any]]) -> dict[str, Any]:
    source_by_id = {row["id"]: row for row in [*ui_sources, *extra_sources]}
    metadata_by_id = {row["row_id"]: row for row in metadata}
    failures: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    source_hashes: dict[str, dict[str, Any]] = {}
    for row in translations:
        row_id = row["id"]
        source = source_by_id.get(row_id)
        if source is None:
            failures.append({"id": row_id, "error": "no source inventory row"})
            continue
        target = row["target"]
        bcost, pixels = target_cost(target)
        if row_id in {x["id"] for x in extra_sources}:
            rel = source["source_file"]
            offset = int(source["source_offset"])
            expected = bytes.fromhex(source["source_hex"])
            budget = int(source["byte_budget"])
            pixel_budget = int(source["pixel_budget"])
            file_path = ROOT / "english-extracted" / rel.lstrip("/")
            blob = file_path.read_bytes()
            if blob[offset:offset + len(expected)] != expected:
                failures.append({"id": row_id, "error": "source bytes changed", "file": rel,
                                 "offset": hex(offset)})
                continue
            source_hashes[rel] = {"bytes": len(blob), "sha256": sha(blob)}
        else:
            source_row = source["row_id"]
            meta = metadata_by_id.get(source_row)
            if meta is None:
                failures.append({"id": row_id, "error": "row metadata missing", "row_id": source_row})
                continue
            budget = int(meta.get("text_capacity_bytes", len(bytes.fromhex(meta.get("raw_text_hex", "")))))
            pixel_budget = budget * 8
            offset = int(meta["offset"])
            raw_hex = meta.get("raw_text_region_hex", meta.get("raw_text_hex", ""))
            expected = bytes.fromhex(raw_hex)
            rels = meta["files"]
            source_mismatch = False
            for rel in rels:
                file_path = ROOT / "english-extracted" / rel.lstrip("/")
                blob = file_path.read_bytes()
                if blob[offset:offset + len(expected)] != expected:
                    failures.append({"id": row_id, "error": "source bytes changed", "file": rel,
                                     "offset": hex(offset)})
                    source_mismatch = True
                source_hashes[rel] = {"bytes": len(blob), "sha256": sha(blob)}
            if source_mismatch:
                continue
            rel = ",".join(rels)
        fits = bcost <= budget and pixels <= pixel_budget
        if not fits:
            failures.append({"id": row_id, "error": "capacity overflow", "target_bytes": bcost,
                             "byte_budget": budget, "target_pixels": pixels,
                             "pixel_budget": pixel_budget})
        output.append({"id": row_id, "file": rel, "offset": hex(offset),
                       "source_text": row["source_text"], "target": target,
                       "encoded_byte_cost": bcost, "byte_budget": budget,
                       "pixel_cost": pixels, "pixel_budget": pixel_budget,
                       "byte_slack": budget - bcost, "pixel_slack": pixel_budget - pixels,
                       "status": "PASS" if fits else "OVERFLOW"})
    counts = Counter(row["status"] for row in output)
    return {"format": "vh-fixed-ui-capacity-v1", "status": "PASS" if not failures else "FAIL",
            "translation_rows": len(translations), "checked_rows": len(output),
            "passed_rows": counts["PASS"], "overflow_rows": counts["OVERFLOW"],
            "failures": failures, "source_files": source_hashes,
            "cost_model": "event-pool bytes: ASCII 1, custom CJK 2; pixels: ASCII 8, custom CJK 16; controls retain one byte",
            "rows": output}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--translations", type=Path, default=ROOT / "translations/ui-fixed-translations.draft.jsonl")
    ap.add_argument("--ui-sources", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/ui-translation-drafts.jsonl")
    ap.add_argument("--metadata", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/row-byte-metadata.jsonl")
    ap.add_argument("--extra-sources", type=Path, default=ROOT / "analysis/text-coverage/additional-menu-candidate-spans.jsonl")
    ap.add_argument("--output", type=Path, default=ROOT / "analysis/text-coverage/fixed-ui-capacity-audit.json")
    args = ap.parse_args()
    report = audit(read_jsonl(args.translations), read_jsonl(args.ui_sources),
                   read_jsonl(args.metadata), read_jsonl(args.extra_sources))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "translation_rows", "checked_rows",
                                                   "passed_rows", "overflow_rows", "failures")},
                     ensure_ascii=False))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
