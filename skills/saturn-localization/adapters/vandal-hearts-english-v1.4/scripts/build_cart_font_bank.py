#!/usr/bin/env python3
"""Build a source-guarded 256 KiB DRAM-cart font bank for Vandal Hearts.

The bank mirrors the reachable 0x21..0x74 glyph cells from the English v1.4
0.BIN address-formula region, then replaces explicitly mapped cells with
Traditional Chinese rasters. It emits a separately patched 0.BIN that points
the renderer at the bank and redirects the existing advance helper to a small
bank-resident routine. This does not install the bank in an ISO or load it;
those steps require a separately verified bootstrap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from vh_native_dialogue_font import (
    EVENT_PAIR_SLOTS,
    SLOT_BYTES,
    normalize_visible_punctuation,
    TABLE_BASE,
    pair_for_slot,
    raster,
    slot_from_pair,
)

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()
SOURCE_SHA = "232cecf4ed1683709fa778dba886e69fe6987f2b1fe259fb47d7ac96e1005b40"
SOURCE_LEN = 350212
BANK_BASE = 0x027C0000
BANK_SIZE = 0x40000
BANK_BYTES = EVENT_PAIR_SLOTS * SLOT_BYTES
FONT_BASE_LITERAL = 0xE5FC
ADVANCE_HELPER = 0xE1F4
ADVANCE_STUB_LEN = 12
ADVANCE_HELPER_RAM_OFFSET = 0x74E0


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def routine_for(slots: list[int]) -> bytes:
    if not slots:
        return b""
    rows = {slot // 94 for slot in slots}
    if slots != list(range(min(slots), max(slots) + 1)) or slots[0] % 94 != 0:
        raise ValueError("multirow advance hook requires one contiguous custom-code run starting at cell 0x21")
    first_row = 0x21 + slots[0] // 94
    last_row = 0x21 + slots[-1] // 94
    row_span = last_row - first_row
    if first_row > 0x80 or row_span > 0x7F:
        raise ValueError("multirow advance hook immediate bounds exceed its signed/unsigned byte contract")
    last_cell = 0x21 + slots[-1] % 94
    # The decoded row is reduced by first_row. The contiguous allocation starts
    # at column 0x21, so only the final row needs a column bound. This covers
    # arbitrary-length contiguous runs without treating adjacent noncustom
    # source cells as 16px glyphs.
    words = [0x2F06, 0x2F16, 0x2F26, 0x2338, 0x8D14, 0xE500,
             0x6203, 0x6103, 0x4119, 0x6013, 0x7000 | ((-first_row) & 0xFF),
             0xE100 | row_span, 0x3016, 0x8907, 0x8800 | row_span,
             0x8B02, 0xE100 | last_cell, 0x3216, 0x8902, 0xE510,
             0xA002, 0x0009, 0xC500, 0x6503, 0xC501, 0x350C,
             0x6053, 0xC101, 0x62F6, 0x61F6, 0x000B, 0x60F6]
    if len(words) != 32:
        raise ValueError(f"multirow advance routine must remain 64 bytes, got {len(words) * 2}")
    return b"".join(word.to_bytes(2, "big") for word in words)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-0bin", type=Path, default=ROOT / "english-extracted/0.BIN")
    ap.add_argument("--abs-txt", type=Path, default=ROOT / "english-extracted/ABS.TXT",
                    help="source ABS.TXT to preserve in the generated font-bank tail")
    ap.add_argument("--mapping", type=Path, required=True,
                    help="JSON object mapping one-character strings to reachable cell indexes; also accepts {unicode_to_slot:{...}}")
    ap.add_argument("--event-dat", type=Path,
                    help="optional translated DAT whose encoded glyph pairs must match this mapping")
    ap.add_argument("--glyph-audit", type=Path,
                    help="source/UI glyph-usage audit JSON; final builds require a PASS audit for this mapping")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--font", type=Path, default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
    ap.add_argument("--renderer-trial", action="store_true",
                    help="also emit an explicitly unsafe renderer-only 0.BIN; font bootstrap is not included")
    args = ap.parse_args()

    source = args.source_0bin.read_bytes()
    if len(source) != SOURCE_LEN or sha(source) != SOURCE_SHA:
        raise SystemExit("source 0.BIN does not match the pinned English v1.4 image")
    mapping_obj = json.loads(args.mapping.read_text(encoding="utf-8"))
    if isinstance(mapping_obj, dict) and "unicode_to_slot" in mapping_obj:
        mapping_obj = mapping_obj["unicode_to_slot"]
    if not isinstance(mapping_obj, dict) or not mapping_obj:
        raise SystemExit("mapping must be a non-empty JSON object")
    mapping: dict[str, int] = {}
    for char, slot in mapping_obj.items():
        if not isinstance(char, str) or len(char) != 1 or isinstance(slot, bool) or not isinstance(slot, int):
            raise SystemExit(f"invalid mapping entry: {char!r}: {slot!r}")
        if not 0 <= slot < EVENT_PAIR_SLOTS:
            raise SystemExit(f"cell index outside parser-reachable pair range: {slot}")
        if slot_from_pair(pair_for_slot(slot)) != slot:
            raise SystemExit(f"event pair codec round trip failed for {char!r}: {slot}")
        normalized_char = normalize_visible_punctuation(char)
        if normalized_char != char:
            raise SystemExit(f"stale mapping contains punctuation normalized to a native glyph: {char!r}; regenerate it")
        char = normalized_char
        if char in mapping:
            raise SystemExit(f"mapping collides after visible-punctuation normalization: {char!r}")
        mapping[char] = slot
    if len(set(mapping.values())) != len(mapping):
        raise SystemExit("mapping assigns multiple characters to one cell")
    glyph_audit = None
    if args.glyph_audit:
        glyph_audit = json.loads(args.glyph_audit.read_text(encoding="utf-8"))
        if glyph_audit.get("status") != "PASS":
            raise SystemExit("glyph-usage audit is not PASS; refusing final font bank")
        audit_capacity = glyph_audit.get("translation_capacity", {})
        if audit_capacity.get("mapping_sha256") != sha(args.mapping.read_bytes()):
            raise SystemExit("glyph-usage audit mapping hash does not match requested mapping")
        if audit_capacity.get("mapped_characters_missing_from_mapping"):
            raise SystemExit("glyph-usage audit reports missing characters")
        if audit_capacity.get("unsupported_or_unmapped_event_characters"):
            raise SystemExit("glyph-usage audit reports event characters that cannot be encoded")
    event_alignment = None
    if args.event_dat:
        event = args.event_dat.read_bytes()
        missing = [char for char, slot in mapping.items()
                   if pair_for_slot(slot) not in event]
        if missing:
            raise SystemExit("translated event DAT is missing mapped glyph pairs: " + "".join(sorted(missing)))
        event_alignment = {"event_dat_sha256": sha(event),
                           "mapped_pairs_present": len(mapping),
                           "mapping_sha256": sha(args.mapping.read_bytes())}
    slots = sorted(mapping.values())
    advance = routine_for(slots)
    if ADVANCE_HELPER_RAM_OFFSET + len(advance) > SOURCE_LEN:
        raise SystemExit("advance helper exceeds its source-font cell reservation")
    if args.output_dir.exists():
        raise SystemExit(f"output already exists: {args.output_dir}")

    bank = bytearray(BANK_SIZE)
    bank[:BANK_BYTES] = source[TABLE_BASE:TABLE_BASE + BANK_BYTES]
    glyph_rows = []
    for char, slot in sorted(mapping.items(), key=lambda item: item[1]):
        bitmap = raster(char, args.font)
        if not any(bitmap):
            raise SystemExit(f"font face produced a blank raster for mapped glyph {char!r}")
        start = slot * SLOT_BYTES
        original = bytes(bank[start:start + SLOT_BYTES])
        bank[start:start + SLOT_BYTES] = bitmap
        glyph_rows.append({"character": char, "unicode": f"U+{ord(char):04X}",
                           "slot": slot, "pair_hex": pair_for_slot(slot).hex(),
                           "bank_offset": hex(start), "source_cell_sha256": sha(original),
                           "bitmap_sha256": sha(bitmap)})
    patched = None
    if args.renderer_trial:
        patched = bytearray(source)
        if patched[FONT_BASE_LITERAL:FONT_BASE_LITERAL + 4] != bytes.fromhex("0600a8c0"):
            raise SystemExit("native glyph base literal differs from source-backed value 0x0600A8C0")
        patched[FONT_BASE_LITERAL:FONT_BASE_LITERAL + 4] = BANK_BASE.to_bytes(4, "big")
        expected_helper = bytes.fromhex("2f0623388d04e500c5006503c501350c6053c101000b60f6")
        if patched[ADVANCE_HELPER:ADVANCE_HELPER + len(expected_helper)] != expected_helper:
            raise SystemExit("source advance helper bytes differ from the verified helper")
        if advance:
            # CS0 DRAM is data-readable but not executable in the supported
            # Yabause cartridge profile. The source C/D glyph cells at +0x74E0
            # are retired when all glyph reads are retargeted to the full cart
            # mirror, so place code in that verified loaded 0.BIN region.
            patched[ADVANCE_HELPER_RAM_OFFSET:ADVANCE_HELPER_RAM_OFFSET + len(advance)] = advance
            target = 0x06004000 + ADVANCE_HELPER_RAM_OFFSET
            stub = bytes.fromhex("2f16d101412b61f6") + target.to_bytes(4, "big")
            patched[ADVANCE_HELPER:ADVANCE_HELPER + ADVANCE_STUB_LEN] = stub

    args.output_dir.mkdir(parents=True)
    # The boot reader's root-table name field is limited to eight characters
    # including the dot. Keep the original ABS.TXT payload in an unused font
    # bank tail slot; the ISO builder preserves its original extent and path.
    metadata = args.abs_txt.read_bytes()
    metadata_offset = BANK_BYTES
    if metadata_offset + len(metadata) > BANK_SIZE:
        raise SystemExit("font bank has no room to preserve the original ABS.TXT metadata")
    bank[metadata_offset:metadata_offset + len(metadata)] = metadata
    (args.output_dir / "FONT.BIN").write_bytes(bank)
    if patched is not None:
        (args.output_dir / "0.BIN.renderer-trial").write_bytes(patched)
    manifest = {
        "format": "vh-dram32m-font-bank-v1",
        "profile": "dram32m",
        "requires_hardware": "32 Mbit DRAM cartridge (4 MiB)",
        "source_0bin_sha256": SOURCE_SHA,
        "source_0bin_length": SOURCE_LEN,
        "bank_base": hex(BANK_BASE), "bank_size": BANK_SIZE,
        "mirrored_glyph_cells": EVENT_PAIR_SLOTS,
        "glyph_payload_bytes": BANK_BYTES,
        "event_alignment": event_alignment,
        "advance_helper_ram_address": hex(0x06004000 + ADVANCE_HELPER_RAM_OFFSET) if advance else None,
        "advance_helper_0bin_offset": hex(ADVANCE_HELPER_RAM_OFFSET) if advance else None,
        "advance_helper_bytes": len(advance),
        "advance_width_contract": {
            "custom_branch_0bin_offset": hex(ADVANCE_HELPER_RAM_OFFSET + 0x28) if advance else None,
            "custom_branch_opcode": "A002" if advance else None,
            "normal_width_move_opcode": "6503" if advance else None,
            "custom_step": 16 if advance else None,
            "normal_width_source": "source width table (C500/6503)" if advance else None,
            "predicate": "contiguous slots beginning at cell 0x21; row offset bounded, final row cell bounded" if advance else None,
            "first_custom_row": (0x21 + slots[0] // 94) if advance else None,
            "last_custom_row": (0x21 + slots[-1] // 94) if advance else None,
            "last_custom_cell": (0x21 + slots[-1] % 94) if advance else None,
        },
        "preserved_original_abs_txt": {
            "source_sha256": sha(metadata), "bank_offset": hex(metadata_offset),
            "bytes": len(metadata), "original_iso_entry_retained": True,
        },
        "0bin_font_literal_offset": hex(FONT_BASE_LITERAL),
        "0bin_advance_hook_offset": hex(ADVANCE_HELPER) if advance else None,
        "glyphs": glyph_rows,
        "asset_sha256": sha(bytes(bank)),
        "patched_0bin_sha256": sha(bytes(patched)) if patched is not None else None,
        "renderer_trial_enabled": args.renderer_trial,
        "glyph_usage_audit": glyph_audit,
        "bootstrap_installed": False,
    }
    (args.output_dir / "font-bank-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "asset_sha256": manifest["asset_sha256"],
                      "patched_0bin_sha256": manifest["patched_0bin_sha256"],
                      "mapped_glyphs": len(glyph_rows), "helper_bytes": len(advance)}))


if __name__ == "__main__":
    main()
