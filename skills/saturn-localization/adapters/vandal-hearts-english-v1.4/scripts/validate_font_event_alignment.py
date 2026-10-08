#!/usr/bin/env python3
"""Validate that translated event glyph pairs address the bank cells we built.

The event pool and bitmap bank must share one codec-index mapping. This check
rejects the historical failure mode where the DAT used row 0x4D while custom
bitmaps were accidentally written to slots 0..N.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from vh_native_dialogue_font import (
    EVENT_PAIR_SLOTS,
    SLOT_BYTES,
    pair_for_slot,
    raster,
    slot_from_pair,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_mapping(path: Path) -> dict[str, int]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if "unicode_to_slot" in value:
        value = value["unicode_to_slot"]
    if not isinstance(value, dict) or not value:
        raise ValueError("mapping must be a non-empty character-to-codec-index object")
    mapping: dict[str, int] = {}
    for char, slot in value.items():
        if not isinstance(char, str) or len(char) != 1 or isinstance(slot, bool) or not isinstance(slot, int):
            raise ValueError(f"invalid mapping entry: {char!r}: {slot!r}")
        if not 0 <= slot < EVENT_PAIR_SLOTS:
            raise ValueError(f"slot outside parser-reachable pair range: {char!r}: {slot}")
        mapping[char] = slot
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("two characters map to one codec index")
    return mapping


def validate(mapping_path: Path, asset_path: Path, helper_path: Path,
             event_path: Path, font_path: Path) -> dict[str, object]:
    mapping = load_mapping(mapping_path)
    asset = asset_path.read_bytes()
    helper_file = helper_path.read_bytes()
    event = event_path.read_bytes()
    manifest = json.loads(font_path.read_text(encoding="utf-8"))
    if len(asset) != 0x40000:
        raise ValueError(f"font bank must be 256 KiB, got {len(asset)}")
    if manifest.get("asset_sha256") != sha(asset):
        raise ValueError("font manifest asset hash mismatch")
    if manifest.get("glyph_payload_bytes") != EVENT_PAIR_SLOTS * SLOT_BYTES:
        raise ValueError("manifest does not describe the full reachable glyph region")

    slots = sorted(mapping.values())
    if slots != list(range(slots[0], slots[-1] + 1)) or slots[0] % 94 != 0:
        raise ValueError("width helper requires contiguous slots beginning at cell 0x21")
    first_row = 0x21 + slots[0] // 94
    last_row = 0x21 + slots[-1] // 94
    row_span = last_row - first_row
    if first_row > 0x80 or row_span > 0x7F:
        raise ValueError("helper immediate bounds exceed its signed/unsigned byte contract")
    last_cell = 0x21 + slots[-1] % 94

    manifest_glyphs = {row["character"]: row for row in manifest.get("glyphs", [])}
    glyph_proofs = []
    for char, slot in sorted(mapping.items(), key=lambda item: item[1]):
        pair = pair_for_slot(slot)
        if slot_from_pair(pair) != slot:
            raise ValueError(f"event codec round trip failed for {char!r}, slot={slot}")
        offset = slot * SLOT_BYTES
        bitmap = asset[offset:offset + SLOT_BYTES]
        if len(bitmap) != SLOT_BYTES or not any(bitmap):
            raise ValueError(f"missing bitmap for {char!r}, slot={slot}, offset={offset:#x}")
        if bitmap != raster(char):
            raise ValueError(f"bitmap does not match raster for {char!r}, slot={slot}")
        glyph = manifest_glyphs.get(char)
        if (glyph is None or glyph.get("slot") != slot or
                glyph.get("pair_hex") != pair.hex() or glyph.get("bank_offset") != hex(offset)):
            raise ValueError(f"font manifest disagrees with the shared mapping for {char!r}")
        count = event.count(pair)
        if count == 0:
            raise ValueError(f"event DAT never encodes mapped character {char!r} as {pair.hex()}")
        glyph_proofs.append({"character": char, "slot": slot, "pair_hex": pair.hex(),
                             "event_pair_occurrences": count, "bank_offset": hex(offset),
                             "bitmap_sha256": sha(bitmap)})

    helper = helper_file[0x74E0:0x7520]
    if len(helper) != 64:
        raise ValueError("0.BIN does not contain the full 64-byte helper")
    words = [int.from_bytes(helper[i:i + 2], "big") for i in range(0, len(helper), 2)]
    expected_predicate = [0x7000 | ((-first_row) & 0xFF), 0xE100 | row_span,
                          0x3016, 0x8907, 0x8800 | row_span, 0x8B02,
                          0xE100 | last_cell, 0x3216, 0x8902, 0xE510,
                          0xA002, 0x0009]
    if words[10:22] != expected_predicate:
        raise ValueError("helper's multirow contiguous-range predicate does not match mapping")
    if words[20:21] != [0xA002] or words[22:25] != [0xC500, 0x6503, 0xC501]:
        raise ValueError("helper custom/normal width branches differ from the verified contract")
    if words[0:3] != [0x2F06, 0x2F16, 0x2F26] or words[28:32] != [0x62F6, 0x61F6, 0x000B, 0x60F6]:
        raise ValueError("helper register save/restore contract is not balanced")

    # Exercise every parser-reachable cell against the generated predicate.
    # The runtime arithmetic operates on the row byte and cell byte directly.
    for slot in range(EVENT_PAIR_SLOTS):
        row = 0x21 + slot // 94
        cell = 0x21 + slot % 94
        offset = (row - first_row) & 0xFFFFFFFF
        selected = offset <= row_span and (offset != row_span or cell <= last_cell)
        if selected != (slot in mapping.values()):
            raise ValueError(f"helper predicate mismatch at slot {slot}")

    return {
        "status": "PASS",
        "locale": "zh-Hant-TW",
        "glyph_count": len(mapping),
        "slot_range": [slots[0], slots[-1]],
        "row_range": [first_row, last_row],
        "advance_contract": {"custom_step": 16, "custom_branch": "A002",
                              "normal_width": "C500/6503"},
        "mapping_sha256": sha(mapping_path.read_bytes()),
        "asset_sha256": sha(asset),
        "helper_sha256": sha(helper),
        "event_sha256": sha(event),
        "glyphs": glyph_proofs,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mapping", type=Path, required=True)
    ap.add_argument("--asset", type=Path, required=True)
    ap.add_argument("--patched-0bin", type=Path, required=True)
    ap.add_argument("--event", type=Path, required=True)
    ap.add_argument("--font-manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    proof = validate(args.mapping, args.asset, args.patched_0bin, args.event, args.font_manifest)
    rendered = json.dumps(proof, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
