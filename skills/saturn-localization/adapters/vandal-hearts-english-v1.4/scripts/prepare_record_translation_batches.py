#!/usr/bin/env python3
"""Compile NUL-block candidates into context-aware, control-preserving inputs."""
from __future__ import annotations

import hashlib
import json
import argparse
import os
from pathlib import Path

from vh_event_text import decode_nodes

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()


def render(nodes: list[dict]) -> str:
    text = []
    for node in nodes:
        kind = node["kind"]
        if kind == "text":
            text.append(str(node["text"]))
        elif kind == "linebreak":
            text.append("\n")
        elif kind == "parameter_control":
            text.append(f"<PARAM_{int(node['opcode']):02X}:{node.get('parameter_hex', '')}>")
        elif kind == "opaque_action":
            text.append(f"<ACTION_{int(node['opcode']):02X}>")
        elif kind == "custom_glyph":
            text.append(f"<GLYPH_{str(node['raw_hex']).upper()}>")
        else:
            text.append(f"<BYTE_{bytes.fromhex(str(node['raw_hex']))[0]:02X}>")
    return "".join(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, default=ROOT / "analysis/text-coverage/candidate-unverified.jsonl")
    parser.add_argument("--glossary", type=Path, default=ROOT / "analysis/text-coverage/translation-drafts/glossary-zh-Hant.json")
    parser.add_argument("--output", type=Path, default=ROOT / "translations/candidate-records.jsonl")
    parser.add_argument("--old-spans", type=Path, help="optional prior flat-span JSONL for segmentation drift review")
    parser.add_argument("--drift-report", type=Path, help="optional output path for the segmentation drift report")
    args = parser.parse_args()
    if args.output.exists() or (args.drift_report and args.drift_report.exists()):
        parser.error("output path already exists; choose a new path to preserve prior work")
    glossary_hash = hashlib.sha256(args.glossary.read_bytes()).hexdigest()
    output = []
    for line in args.records.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        source = json.loads(line)
        raw = bytes.fromhex(source["raw_hex"])
        nodes = decode_nodes(raw)
        segments = []
        layout = []
        for index, node in enumerate(nodes):
            if node["kind"] == "text":
                segment_id = f"text{index:03d}"
                budget = len(bytes.fromhex(str(node["raw_hex"])))
                text = str(node["text"])
                segments.append({"id": segment_id, "source": text,
                                 "byte_budget": budget,
                                 "pixel_budget": min(256, budget * 8),
                                 "speaker_label": text.strip().endswith(":")})
                layout.append({"kind": "text", "id": segment_id,
                               "source_hex": node["raw_hex"]})
            else:
                layout.append({"kind": node["kind"], "raw_hex": node["raw_hex"]})
        if b"".join(bytes.fromhex(row["source_hex"] if row["kind"] == "text" else row["raw_hex"])
                     for row in layout) != raw:
            raise ValueError(f"{source['id']}: decoded layout does not reconstruct source bytes")
        identity = {"source": source["id"], "raw_hex": source["raw_hex"],
                    "layout": layout, "segments": segments, "glossary_sha256": glossary_hash}
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
        output.append({"id": "r_" + key, "cache_key": key, "source_id": source["id"],
                       "file": source["file"], "offset": source["offset"],
                       "raw_hex": source["raw_hex"], "record_bytes": len(raw),
                       "preview_context": render(nodes), "segments": segments,
                       "layout": layout, "glossary_sha256": glossary_hash,
                       "status": "candidate-unverified; translation only"})
    old_counts: dict[str, int] = {}
    for line in (args.old_spans.read_text(encoding="utf-8").splitlines()
                 if args.old_spans else []):
        if line.strip():
            old = json.loads(line)
            old_counts[old["source_id"]] = old_counts.get(old["source_id"], 0) + 1
    drift = []
    for row in output:
        old_count = old_counts.get(row["source_id"], 0)
        current_count = len(row["segments"])
        if old_count != current_count:
            drift.append({"source_id": row["source_id"], "file": row["file"],
                          "offset": row["offset"], "raw_sha256": hashlib.sha256(
                              bytes.fromhex(row["raw_hex"])).hexdigest(),
                          "old_flat_span_count": old_count, "current_record_text_run_count": current_count,
                          "source_preview": row["preview_context"],
                          "current_segments": row["segments"]})
    with OUTPUT.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.drift_report:
        args.drift_report.parent.mkdir(parents=True, exist_ok=True)
        args.drift_report.write_text(json.dumps({"status": "REVIEW", "old_flat_span_total": sum(old_counts.values()),
            "record_text_run_total": sum(len(row["segments"]) for row in output),
            "changed_block_count": len(drift), "blocks": drift,
            "cause_scope": "raw NUL-block decode under current vh_event_text SINGLE map; source bytes unchanged"},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"status": "PASS", "records": len(output),
                      "text_segments": sum(len(row["segments"]) for row in output),
                      "segmentation_drift_blocks": len(drift),
                      "glossary_sha256": glossary_hash, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
