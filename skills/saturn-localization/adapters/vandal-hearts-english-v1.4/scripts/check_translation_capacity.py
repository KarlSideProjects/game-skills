#!/usr/bin/env python3
"""Dry-run byte-capacity and estimated renderer-width checks for cached drafts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from vh_native_dialogue_font import normalize_visible_punctuation

ROOT = Path(os.environ.get('SATURN_PROJECT_ROOT', Path.cwd())).resolve()
SINGLE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz !\"#='(),-./01235678:?") | {"0"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def encoded_bytes(text: str) -> int:
    text = normalize_visible_punctuation(text)
    return sum(1 if char in SINGLE else 2 for char in text if char not in "\r\n\t") + text.count("\n")


def estimated_pixels(text: str) -> int:
    # Runtime proof: original ASCII path uses an 8px advance; custom pair path uses 16px.
    text = normalize_visible_punctuation(text)
    return sum(8 if char in SINGLE else 16 for char in text if char not in "\r\n\t")


def newline_text_groups(layout: list[dict]) -> list[dict]:
    """Return text runs connected only by verified 0A render-newline nodes."""
    groups: list[dict] = []
    current: list[dict] = []
    for node in layout:
        if node.get("kind") in {"text", "linebreak"}:
            current.append(node)
            continue
        if any(part.get("kind") == "text" for part in current):
            groups.append(_finish_newline_group(current))
        current = []
    if any(part.get("kind") == "text" for part in current):
        groups.append(_finish_newline_group(current))
    return groups


def _finish_newline_group(nodes: list[dict]) -> dict:
    text_ids = [node["id"] for node in nodes if node.get("kind") == "text"]
    capacity = sum(len(bytes.fromhex(node["source_hex"]))
                   for node in nodes if node.get("kind") == "text")
    return {"text_ids": text_ids, "byte_budget": capacity,
            "linebreak_count": sum(node.get("kind") == "linebreak" for node in nodes),
            "nodes": nodes}


def record_overflow_segments(record: dict, targets: dict[str, str],
                             dialogue_pixel_limit: int = 256) -> list[str]:
    """Check aggregate text budget per newline-only group and actual line width."""
    overflow: set[str] = set()
    segments = {row["id"]: row for row in record["segments"]}
    for group in newline_text_groups(record["layout"]):
        if any(segment_id not in targets for segment_id in group["text_ids"]):
            overflow.update(group["text_ids"])
            continue
        used = sum(encoded_bytes(targets[segment_id]) for segment_id in group["text_ids"])
        if used > group["byte_budget"]:
            overflow.update(group["text_ids"])
        for segment_id in group["text_ids"]:
            if estimated_pixels(targets[segment_id]) > dialogue_pixel_limit:
                overflow.add(segment_id)
    # An input segment should always be represented exactly once in the decoded layout.
    if set(segments) != {node["id"] for node in record["layout"] if node.get("kind") == "text"}:
        raise ValueError(f"record layout/segment identity mismatch: {record.get('id')}")
    return [segment["id"] for segment in record["segments"] if segment["id"] in overflow]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spans", type=Path, default=ROOT / "translations/candidate-text-spans.jsonl")
    ap.add_argument("--ui-spans", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/ui-translation-drafts.jsonl")
    ap.add_argument("--ui-metadata", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/row-byte-metadata.jsonl")
    ap.add_argument("--cache", type=Path, default=ROOT / "translations/candidate-translation-cache.jsonl")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--dialogue-pixel-limit", type=int, default=256,
                    help="diagnostic only; default is a conservative 256px text-line budget")
    args = ap.parse_args()

    cache = {row["source_sha256"]: row["target"] for row in read_jsonl(args.cache)}
    spans = read_jsonl(args.spans)
    done = []
    for row in spans:
        target = cache.get(hashlib.sha256(row["source_text"].encode("utf-8")).hexdigest())
        if target is None:
            continue
        capacity = len(bytes.fromhex(row["source_hex"]))
        used = encoded_bytes(target)
        done.append({"id": row["id"], "file": row["file"], "offset": row["source_offset"],
                     "source_capacity_bytes": capacity, "target_bytes": used,
                     "byte_overflow": max(0, used - capacity),
                     "estimated_target_pixels": estimated_pixels(target), "target": target})
    ui_rows = read_jsonl(args.ui_spans)
    ui_meta = {row["row_id"]: row for row in read_jsonl(args.ui_metadata)}
    by_ui: dict[str, list[dict]] = defaultdict(list)
    for row in ui_rows:
        target = cache.get(hashlib.sha256(row["source"].encode("utf-8")).hexdigest())
        if target is not None:
            by_ui[row["row_id"]].append({"target": target, "span_index": row["span_index"]})
    ui_done = []
    for row_id, parts in by_ui.items():
        meta = ui_meta[row_id]
        body = "\n".join(part["target"] for part in sorted(parts, key=lambda x: x["span_index"]))
        capacity = int(meta["text_capacity_bytes"])
        used = encoded_bytes(body)
        ui_done.append({"row_id": row_id, "table": meta["table"], "offset": meta["offset"],
                        "capacity_bytes": capacity, "target_bytes": used,
                        "byte_overflow": max(0, used - capacity),
                        "estimated_target_pixels": estimated_pixels(body),
                        "estimated_pixel_capacity": capacity * 8, "target": body})
    over = [row for row in done if row["byte_overflow"]]
    width_over = [row for row in done if row["estimated_target_pixels"] > args.dialogue_pixel_limit]
    ui_over = [row for row in ui_done if row["byte_overflow"]]
    ui_width_over = [row for row in ui_done if row["estimated_target_pixels"] > row["estimated_pixel_capacity"]]
    files = Counter(row["file"] for row in over)
    report = {
        "format": "vh-translation-capacity-dry-run-v1",
        "translation_cache_rows": len(cache),
        "candidate_span_total": len(spans), "candidate_span_translated": len(done),
        "candidate_span_byte_overflow_count": len(over),
        "candidate_span_byte_overflow_positions": len({row["id"] for row in over}),
        "candidate_span_overflow_by_file": dict(files.most_common()),
        "candidate_span_estimated_width_limit_px": args.dialogue_pixel_limit,
        "candidate_span_estimated_width_overflow_count": len(width_over),
        "candidate_span_width_overflow_sample": sorted(width_over, key=lambda x: x["estimated_target_pixels"], reverse=True)[:30],
        "candidate_span_byte_overflow_sample": sorted(over, key=lambda x: x["byte_overflow"], reverse=True)[:50],
        "fixed_ui_span_rows": len(ui_rows), "fixed_ui_rows_translated": len(ui_done),
        "fixed_ui_byte_overflow_count": len(ui_over),
        "fixed_ui_estimated_width_overflow_count": len(ui_width_over),
        "fixed_ui_overflow_sample": sorted(ui_over, key=lambda x: x["byte_overflow"], reverse=True)[:50],
        "width_model": {"ascii_px": 8, "custom_pair_px": 16,
                        "evidence": "renderer proof shows normal C500/6503 width and custom helper E510=16; UI pixel caps remain inferred from fixed byte field capacities"},
        "policy": "dry-run only; never truncate. Oversized source/target rows need a Luna revision tied to exact byte and pixel budgets, with controls and NUL boundaries preserved.",
        "status": "DRY_RUN",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cache_unique": len(cache),
                      "translated_positions": len(done), "dialogue_overflow": len(over),
                      "dialogue_width_over": len(width_over), "fixed_ui_rows": len(ui_done),
                      "fixed_ui_overflow": len(ui_over), "fixed_ui_width_over": len(ui_width_over)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
