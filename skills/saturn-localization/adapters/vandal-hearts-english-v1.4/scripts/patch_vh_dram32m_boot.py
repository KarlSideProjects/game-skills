#!/usr/bin/env python3
"""Create the source-guarded Vandal Hearts DRAM-cart boot-profile patch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()
DEFAULT_EXTRACTED = ROOT / "english-extracted"
TRACK_SHA = "6bae252e67460fa7b616adb6d5da9c3a4a92589fa35742a8874b6628f02c2f07"
BOOT_SHA = "bb4494022251966ffdad930e4a42d9b6b32c364615cb2710c8663eedb386db6e"
PATCH_OFFSET = 0x18
EXPECTED = bytes.fromhex("885c")
PATCHED = bytes.fromhex("885d")
FONT_NAME = b"FONT.BIN\0"
FONT_BANK = 0x027C0000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cart-profile", required=True, choices=("dram32m",),
                    help="the only verified profile: a 32 Mbit (4 MiB) DRAM cartridge")
    ap.add_argument("--extracted", type=Path, default=DEFAULT_EXTRACTED,
                    help="source-hash-guarded English v1.4 extracted files")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--font-bootstrap", action="store_true",
                    help="load /FONT.BIN to cart tail via the boot CD reader before loading 0.BIN")
    a = ap.parse_args()

    source = a.extracted / "0."
    data = source.read_bytes()
    if sha(data) != BOOT_SHA:
        raise SystemExit(f"source hash mismatch for {source}: {sha(data)}")
    if data[PATCH_OFFSET:PATCH_OFFSET + len(EXPECTED)] != EXPECTED:
        raise SystemExit("0. cart-ID compare differs from the verified 0x885C instruction")
    if a.output_dir.exists():
        raise SystemExit(f"output already exists: {a.output_dir}")

    patched = bytearray(data)
    patched[PATCH_OFFSET:PATCH_OFFSET + len(PATCHED)] = PATCHED
    if a.font_bootstrap:
        # The modified 0x5D comparison makes the source 0x5C DRAM cart enter
        # the no-cart path; its former cart-only block is used for this helper.
        # The low-level boot reader at runtime 0x002021C8 takes R4=path, R5=dst.
        if data[0x26A:0x26E] != bytes.fromhex("bf220009"):
            raise SystemExit("no-cart startup call differs from the verified CD-reader call site")
        helper = bytes.fromhex("4f22b0460009d412d512b0cd00094f26000b0009")
        if len(helper) != 0x14:
            raise AssertionError("unexpected bootstrap helper length")
        if len(FONT_NAME) > 0x84 - 0x78:
            raise AssertionError("font path does not fit the retired cart-only branch")
        patched[0x20:0x70] = bytes(0x50)
        patched[0x20:0x20 + len(helper)] = helper
        patched[0x70:0x74] = (0x00202078).to_bytes(4, "big")
        patched[0x74:0x78] = FONT_BANK.to_bytes(4, "big")
        patched[0x78:0x78 + len(FONT_NAME)] = FONT_NAME
        # BSR from the no-cart setup to the helper (source offset 0x20).
        patched[0x26A:0x26C] = bytes.fromhex("bed9")
    patched = bytes(patched)
    a.output_dir.mkdir(parents=True)
    (a.output_dir / "0.").write_bytes(patched)
    patch_plan = {
        "source_track_sha256": TRACK_SHA,
        "patches": [{"path": "/0.", "sha256": sha(patched)}],
    }
    (a.output_dir / "patch-plan.json").write_text(
        json.dumps(patch_plan, indent=2) + "\n", encoding="utf-8")
    profile = {
        "profile": a.cart_profile,
        "requires_hardware": "32 Mbit DRAM cartridge (4 MiB)",
        "cartridge_ram_mapping": "SH-2 CS0 0x02400000-0x027FFFFF",
        "font_bank_candidate": "0x027C0000-0x027FFFFF (not yet load/lifetime verified)",
        "source_track1_sha256": TRACK_SHA,
        "source_file": "/0.",
        "source_file_sha256": BOOT_SHA,
        "patch_offset": hex(PATCH_OFFSET),
        "source_instruction": EXPECTED.hex(" "),
        "patched_instruction": PATCHED.hex(" "),
        "effect": "route a real cart ID 0x5C through the no-cart startup branch while preserving CS0 DRAM mapping",
        "font_bootstrap": a.font_bootstrap,
        "font_bootstrap_contract": ("boot reader /FONT.BIN -> 0x027C0000 before the original 0.BIN load"
                                    if a.font_bootstrap else None),
        "emulator_cart_id_override": "none",
    }
    (a.output_dir / "cart-profile.json").write_text(
        json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"profile": a.cart_profile,
                      "patched_0_sha256": sha(patched),
                      "patch_offset": hex(PATCH_OFFSET),
                      "output_dir": str(a.output_dir)}))


if __name__ == "__main__":
    main()
