#!/usr/bin/env python3
"""Preflight a HUD trial disc against its exact compiled 0/1.BIN and FONT."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SECTOR_SIZE = 2352
USER_DATA = 16
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_extent(track: Path, lba: int, size: int) -> bytes:
    result = bytearray()
    with track.open("rb") as stream:
        for sector_lba in range(lba, lba + (size + 2047) // 2048):
            stream.seek(sector_lba * SECTOR_SIZE)
            sector = stream.read(SECTOR_SIZE)
            if len(sector) != SECTOR_SIZE or sector[:12] != SYNC or sector[15] != 1:
                raise ValueError(f"invalid MODE1/2352 sector at LBA {sector_lba}")
            result.extend(sector[USER_DATA:USER_DATA + 2048])
    return bytes(result[:size])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--track1", type=Path, required=True)
    ap.add_argument("--patch-manifest", type=Path, required=True)
    ap.add_argument("--expected-source-track-sha256", required=True)
    ap.add_argument("--expected-track1-sha256", required=True)
    ap.add_argument("--expected-0bin", type=Path, required=True)
    ap.add_argument("--expected-1bin", type=Path, required=True)
    ap.add_argument("--expected-font", type=Path, required=True)
    ns = ap.parse_args()

    manifest = json.loads(ns.patch_manifest.read_text(encoding="utf-8"))
    actual_track_sha = hashlib.sha256(ns.track1.read_bytes()).hexdigest()
    if manifest.get("source_track1_sha256") != ns.expected_source_track_sha256:
        raise SystemExit("FAIL: patch manifest is based on the wrong prepared source Track 1")
    if manifest.get("output_track1_sha256") != ns.expected_track1_sha256 or actual_track_sha != ns.expected_track1_sha256:
        raise SystemExit(f"FAIL: final Track 1 SHA mismatch: actual={actual_track_sha}")

    expected = {
        "/0.BIN": ns.expected_0bin,
        "/1.BIN": ns.expected_1bin,
        "/FONT.BIN": ns.expected_font,
    }
    rows = {row["path"]: row for row in manifest.get("patches", [])}
    for path, candidate in expected.items():
        if path not in rows:
            raise SystemExit(f"FAIL: final patch manifest omits {path}")
        row = rows[path]
        target = candidate.read_bytes()
        candidate_sha = digest(target)
        if len(target) != row["size"] or candidate_sha != row["target_sha256"]:
            raise SystemExit(f"FAIL: {path} manifest target differs from compiled candidate")
        actual = extract_extent(ns.track1, row["lba"], row["size"])
        actual_sha = digest(actual)
        if actual_sha != candidate_sha:
            raise SystemExit(f"FAIL: extracted {path} SHA {actual_sha} != compiled candidate {candidate_sha}")
        if path == "/0.BIN":
            for offset, expected_bytes, label in (
                (0x7520, bytes.fromhex("6174621ce0003d00"), "v16 converter helper"),
                (0xB856, bytes.fromhex("d040400b00098d11"), "converter hook"),
                (0xD2F6, bytes.fromhex("d01a4f22400b0009"), "renderer init hook"),
                (0xD32C, bytes.fromhex("d00f400b00098fee"), "per-token renderer hook"),
            ):
                if actual[offset:offset + len(expected_bytes)] != expected_bytes:
                    raise SystemExit(f"FAIL: /0.BIN readback does not contain {label} at file+{offset:#x}")

    print(json.dumps({
        "status": "HUD_TRIAL_DISC_PREFLIGHT_PASS",
        "source_track1_sha256": manifest["source_track1_sha256"],
        "track1_sha256": actual_track_sha,
        "compiled_0bin_sha256": digest(ns.expected_0bin.read_bytes()),
        "compiled_1bin_sha256": digest(ns.expected_1bin.read_bytes()),
        "compiled_font_sha256": digest(ns.expected_font.read_bytes()),
        "paths_checked": list(expected),
        "readback_hooks": "converter/helper/init/token exact bytes",
    }, indent=2))


if __name__ == "__main__":
    main()
