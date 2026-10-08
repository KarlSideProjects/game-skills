#!/usr/bin/env python3
"""Encode and rasterize custom Traditional Chinese event glyphs for Vandal Hearts.

The event parser accepts a two-byte Shift-JIS-shaped input and converts it to
the 94x94 row/cell grid used by the native 16x16 1bpp font. Unicode assignment
is deliberately explicit: ordinary Unicode Shift-JIS lookup is not involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TABLE_BASE = 0x68C0
TABLE_SLOTS = 94 * 94
# Event input dispatch admits pair leads 0x81..0x9F and 0xE0..0xEA.
# The final ten JIS rows (0x75..0x7E) would require 0xEB..0xEF and are
# intercepted by the parser's single-byte extended-glyph trampoline.
EVENT_PAIR_ROWS = 0x74 - 0x21 + 1
EVENT_PAIR_SLOTS = EVENT_PAIR_ROWS * 94
SLOT_BYTES = 32
DEFAULT_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def slot_for_code(code: int) -> int:
    row, cell = code >> 8, code & 0xFF
    if not (0x21 <= row <= 0x7E and 0x21 <= cell <= 0x7E):
        raise ValueError(f"not a valid 94x94 code: {code:04X}")
    return (row - 0x21) * 94 + cell - 0x21


def pair_for_slot(slot: int) -> bytes:
    if not 0 <= slot < EVENT_PAIR_SLOTS:
        raise ValueError(f"slot out of range: {slot}")
    row = 0x21 + slot // 94
    cell = 0x21 + slot % 94
    lead = 0x81 + (row - 0x21) // 2
    if lead >= 0xA0:
        lead += 0x40
    trail = cell + (0x1F if row & 1 else 0x7E)
    if row & 1 and trail >= 0x7F:
        trail += 1
    return bytes((lead, trail))


def slot_from_pair(pair: bytes) -> int:
    if len(pair) != 2:
        raise ValueError("event glyph requires exactly two bytes")
    lead, trail = pair
    if not (0x81 <= lead <= 0x9F or 0xE0 <= lead <= 0xEA):
        raise ValueError(f"invalid event lead byte: {lead:02X}")
    if not (0x40 <= trail <= 0xFC and trail != 0x7F):
        raise ValueError(f"invalid event trail byte: {trail:02X}")
    row = (lead - (0x81 if lead <= 0x9F else 0xC1)) * 2 + 0x21
    trail1 = trail - 1 if trail > 0x7F else trail
    if trail1 > 0x9D:
        row += 1
        cell = trail1 - 0x7D
    else:
        cell = trail1 - 0x1F
    slot = slot_for_code((row << 8) | cell)
    if slot >= EVENT_PAIR_SLOTS:
        raise ValueError("pair addresses a row intercepted by the one-byte parser path")
    return slot


def encode_text(text: str, mapping: dict[str, int]) -> bytes:
    """Encode confirmed plain spans; controls must be handled by the caller."""
    # The source codec has an ASCII slash token but no verified fullwidth-slash
    # bitmap. Treat this visible punctuation variant as its one-byte equivalent.
    text = normalize_visible_punctuation(text)
    out = bytearray()
    for char in text:
        if "A" <= char <= "Z":
            out.append(ord(char) + 0x60)
        elif "a" <= char <= "z":
            out.append(ord(char) + 0x60)
        elif char == " ":
            out.append(0xA0)
        elif char == "\n":
            out.append(0x0A)
        elif char == "!":
            out.append(0xBB)
        elif char == '"':
            out.append(0xBC)
        elif char == "#":
            out.append(0xBD)
        elif char == "=":
            out.append(0xC0)
        elif char == "'":
            out.append(0xDC)
        elif char == "(":
            out.append(0xDD)
        elif char == ")":
            out.append(0xDE)
        elif char == ",":
            out.append(0xEC)
        elif char == "-":
            out.append(0xED)
        elif char == ".":
            out.append(0xEE)
        elif char == "/":
            out.append(0xEF)
        elif char in "01235678":
            code = {"0": 0xF0, "1": 0xF1, "2": 0xF2, "3": 0xF3,
                    "5": 0xF5, "6": 0xF6, "7": 0xF7, "8": 0xF8}[char]
            out.append(code)
        elif char == ":":
            out.append(0xFA)
        elif char == "?":
            out.append(0xFB)
        elif char in mapping:
            out.extend(pair_for_slot(mapping[char]))
        else:
            raise ValueError(f"no confirmed event encoding for U+{ord(char):04X} {char!r}")
    return bytes(out)


def normalize_visible_punctuation(text: str) -> str:
    """Normalize visually equivalent punctuation with a verified source token."""
    return (text.replace("／", "/").replace("：", ":").replace("！", "!")
            .replace("？", "?").replace("，", ",").replace("。", "."))


def raster(char: str, font_path: Path = DEFAULT_FONT, face: int = 3) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), 14, index=face)
    image = Image.new("L", (16, 16))
    box = font.getbbox(char)
    ImageDraw.Draw(image).text((-box[0], -box[1]), char, font=font, fill=255)
    if image.getbbox() is None:
        raise ValueError(f"empty glyph raster: {char}")
    return b"".join(
        sum(1 << (15 - x) for x in range(16) if image.getpixel((x, y)) >= 128).to_bytes(2, "big")
        for y in range(16)
    )


def patch_font(source: bytes, mapping: dict[str, int], font_path: Path = DEFAULT_FONT,
               replace_slots: set[int] | None = None) -> tuple[bytes, list[dict[str, object]]]:
    replace_slots = replace_slots or set()
    if len(source) < TABLE_BASE + TABLE_SLOTS * SLOT_BYTES:
        raise ValueError("0.BIN is shorter than the native 94x94 glyph table")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("two characters map to the same glyph slot")
    result = bytearray(source)
    evidence = []
    for char, slot in sorted(mapping.items(), key=lambda item: item[1]):
        if len(char) != 1 or not 0 <= slot < TABLE_SLOTS:
            raise ValueError(f"invalid mapping {char!r} -> {slot}")
        offset = TABLE_BASE + slot * SLOT_BYTES
        previous = source[offset:offset + SLOT_BYTES]
        if any(previous) and slot not in replace_slots:
            raise ValueError(f"slot {slot} ({offset:#x}) is nonblank and not approved for replacement")
        bitmap = raster(char, font_path)
        result[offset:offset + SLOT_BYTES] = bitmap
        evidence.append({"character": char, "slot": slot, "event_pair_hex": pair_for_slot(slot).hex(),
                         "file_offset": hex(offset), "previous_slot_sha256": hashlib.sha256(previous).hexdigest(),
                         "bitmap_sha256": hashlib.sha256(bitmap).hexdigest()})
    return bytes(result), evidence


def self_check() -> None:
    for slot in range(EVENT_PAIR_SLOTS):
        pair = pair_for_slot(slot)
        if slot_from_pair(pair) != slot:
            raise AssertionError(f"round-trip failed for slot {slot}: {pair.hex()}")
    assert pair_for_slot(97) == bytes.fromhex("81a2")
    assert pair_for_slot(134) == bytes.fromhex("81c7")
    assert slot_from_pair(bytes.fromhex("81a2")) == 97
    assert slot_from_pair(bytes.fromhex("81c7")) == 134
    try:
        pair_for_slot(EVENT_PAIR_SLOTS)
    except ValueError:
        pass
    else:
        raise AssertionError("unreachable event pair row was accepted")
    assert encode_text("Soldier: What?", {}) == bytes.fromhex(
        "b3cfccc4c9c5d2faa0b7c8c1d4fb")
    assert encode_text("#=(,)-./1235678", {}) == bytes.fromhex(
        "bdc0ddecdeedeeeff1f2f3f5f6f7f8")
    assert encode_text("01235678", {}) == bytes.fromhex("f0f1f2f3f5f6f7f8")
    assert encode_text('Magnus: "Ah...a hidden room."!', {}) == bytes((
        0xAD, 0xC1, 0xC7, 0xCE, 0xD5, 0xD3, 0xFA, 0xA0, 0xBC, 0xA1, 0xC8,
        0xEE, 0xEE, 0xEE, 0xC1, 0xA0, 0xC8, 0xC9, 0xC4, 0xC4, 0xC5, 0xCE,
        0xA0, 0xD2, 0xCF, 0xCF, 0xCD, 0xEE, 0xBC, 0xBB))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="verify every slot's parser round-trip")
    parser.add_argument("--mapping", type=Path, help="JSON object mapping one-character strings to slot integers")
    parser.add_argument("--input-font", type=Path, help="source 0.BIN")
    parser.add_argument("--output-font", type=Path, help="patched 0.BIN output")
    parser.add_argument("--replace-slots", type=Path, help="JSON array of explicitly approved occupied slots")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print(f"PASS: all {EVENT_PAIR_SLOTS} parser-reachable event slots round-trip; "
              f"81A2->slot97, 81C7->slot134; rows 75-7E are not pair-addressable")
    if args.mapping or args.input_font or args.output_font:
        if not (args.mapping and args.input_font and args.output_font):
            parser.error("--mapping, --input-font, and --output-font must be supplied together")
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        replacements = set(json.loads(args.replace_slots.read_text())) if args.replace_slots else set()
        patched, evidence = patch_font(args.input_font.read_bytes(), mapping, replace_slots=replacements)
        args.output_font.write_bytes(patched)
        print(json.dumps({"output": str(args.output_font), "glyphs": evidence}, ensure_ascii=False))
    if not args.self_check and not args.mapping:
        parser.error("choose --self-check or provide a mapping and font paths")


if __name__ == "__main__":
    main()
