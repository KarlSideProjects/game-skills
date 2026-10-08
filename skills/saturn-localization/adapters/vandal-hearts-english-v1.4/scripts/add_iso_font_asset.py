#!/usr/bin/env python3
"""Install a cart font asset through the existing ABS.TXT root-file slot."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shlex
import shutil
import os
from pathlib import Path

ROOT = Path(os.environ.get("SATURN_PROJECT_ROOT", Path.cwd())).resolve()
TRACK_SHA = "6bae252e67460fa7b616adb6d5da9c3a4a92589fa35742a8874b6628f02c2f07"
SECTOR = 2352
USER = 16
ASSET_NAME = b"FONT.BIN;1"
PRESERVED_NAME = b"ABS.TXT;1"
PATCHER = Path.home() / ".codex/skills/saturn-localization/scripts/saturn_disc_patch.py"
BOOT_SHA = "bb4494022251966ffdad930e4a42d9b6b32c364615cb2710c8663eedb386db6e"
BIN_SHA = "232cecf4ed1683709fa778dba886e69fe6987f2b1fe259fb47d7ac96e1005b40"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def both16(value: int) -> bytes:
    return value.to_bytes(2, "little") + value.to_bytes(2, "big")


def both32(value: int) -> bytes:
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def read_sector(stream, lba: int) -> bytearray:
    stream.seek(lba * SECTOR)
    data = bytearray(stream.read(SECTOR))
    if len(data) != SECTOR:
        raise ValueError(f"short sector at LBA {lba}")
    return data


def make_directory_record(name: bytes, extent: int, size: int, template: bytes) -> bytes:
    record_len = 33 + len(name) + (1 if len(name) % 2 == 0 else 0)
    record = bytearray(record_len)
    record[0] = record_len
    record[1] = template[1]
    record[2:10] = both32(extent)
    record[10:18] = both32(size)
    record[18:32] = template[18:32]
    record[32] = len(name)
    record[33:33 + len(name)] = name
    return bytes(record)


def read_root_records(track, root_lba: int, root_size: int) -> tuple[bytearray, list[bytes]]:
    pos = 0
    root_bytes = bytearray()
    for lba in range(root_lba, root_lba + (root_size + 2047) // 2048):
        sec = read_sector(track, lba)
        if sec[:12] != b"\x00" + b"\xff" * 10 + b"\x00" or sec[15] != 1:
            raise ValueError(f"invalid MODE1 sector in root directory at LBA {lba}")
        root_bytes.extend(sec[USER:USER + 2048])
    records = []
    while pos < root_size:
        off = pos % 2048
        rec_len = root_bytes[pos]
        if rec_len == 0:
            pos = (pos // 2048 + 1) * 2048
            continue
        if pos + rec_len > root_size or off + rec_len > 2048:
            raise ValueError(f"invalid root directory record at byte offset {pos:#x}")
        records.append(bytes(root_bytes[pos:pos + rec_len]))
        pos += rec_len
    return root_bytes, records


def record_name(record: bytes) -> bytes:
    name_len = record[32]
    if 33 + name_len > len(record):
        raise ValueError("directory record filename exceeds its record")
    return record[33:33 + name_len]


def pack_root(records: list[bytes], root_size: int) -> bytearray:
    packed = bytearray(root_size)
    pos = 0
    for record in records:
        if pos % 2048 + len(record) > 2048:
            pos = ((pos // 2048) + 1) * 2048
        if pos + len(record) > root_size:
            raise ValueError("root directory has no room for the font and preserved ABS.TXT records")
        packed[pos:pos + len(record)] = record
        pos += len(record)
    return packed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cue", type=Path, required=True)
    ap.add_argument("--inventory", type=Path, default=ROOT / "english-extracted/extract-manifest.json")
    ap.add_argument("--asset", type=Path, required=True)
    ap.add_argument("--patched-boot", type=Path,
                    help="source-guarded replacement for /0.")
    ap.add_argument("--patched-0bin", type=Path,
                    help="source-guarded replacement for /0.BIN")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--source-track-sha256", default=TRACK_SHA)
    ap.add_argument("--input-track-sha256",
                    help="exact Track 1 SHA-256 of an already fixed-size-patched base; inventory extents must still match")
    args = ap.parse_args()
    if args.source_track_sha256 != TRACK_SHA:
        raise SystemExit("only the pinned English v1.4 Track 1 is supported")
    if args.output_dir.exists():
        raise SystemExit(f"output already exists: {args.output_dir}")
    if not PATCHER.is_file():
        raise SystemExit(f"MODE1 patcher missing: {PATCHER}")
    spec = importlib.util.spec_from_file_location("vh_disc_patcher", PATCHER)
    disc = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(disc)

    cue = args.cue.resolve(strict=True)
    names = disc.cue_tracks(cue)
    source_track, source_audio = cue.parent / names[0], cue.parent / names[1]
    input_track_sha = args.input_track_sha256 or TRACK_SHA
    if disc.sha256(source_track) != input_track_sha:
        raise SystemExit("input Track 1 SHA-256 mismatch")
    asset = args.asset.read_bytes()
    if len(asset) != 0x40000:
        raise SystemExit(f"font asset must be exactly 256 KiB, got {len(asset)}")
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    occupied = set()
    for row in inventory:
        first = row["lba"]
        count = (row["size"] + 2047) // 2048
        occupied.update(range(first, first + count))
    pvd = read_sector(source_track.open("rb"), 16)
    pvd_user = pvd[USER:USER + 2048]
    if pvd_user[1:6] != b"CD001" or pvd_user[6] != 1:
        raise SystemExit("primary volume descriptor missing at LBA 16")
    volume_sectors = int.from_bytes(pvd_user[80:84], "little")
    root_len = pvd_user[156]
    root = pvd_user[156:156 + root_len]
    root_lba = int.from_bytes(root[2:6], "little")
    root_size = int.from_bytes(root[10:14], "little")
    with source_track.open("rb") as stream:
        root_bytes, source_records = read_root_records(stream, root_lba, root_size)
    if any(record_name(record) == ASSET_NAME for record in source_records):
        raise SystemExit("FONT.BIN;1 already exists in the source root directory")
    source_abs = next((record for record in source_records if record_name(record) == PRESERVED_NAME), None)
    if source_abs is None:
        raise SystemExit("source root directory does not contain ABS.TXT;1")

    track_sectors = source_track.stat().st_size // SECTOR
    if volume_sectors != track_sectors:
        raise SystemExit(f"volume space {volume_sectors} does not match Track 1 sector count {track_sectors}")
    rows_by_path = {row["path"]: row for row in inventory}
    replacements = {}
    for path, expected in ((args.patched_boot, ("/0.", BOOT_SHA)),
                           (args.patched_0bin, ("/0.BIN", BIN_SHA))):
        if path is None:
            continue
        logical_path, source_hash = expected
        row = rows_by_path.get(logical_path)
        if row is None:
            raise SystemExit(f"manifest omits required boot file {logical_path}")
        replacement = path.read_bytes()
        if len(replacement) != row["size"]:
            raise SystemExit(f"replacement {logical_path} changes file size")
        if sha(replacement) == row["sha256"]:
            raise SystemExit(f"replacement {logical_path} is unchanged")
        if row["sha256"] != source_hash:
            raise SystemExit(f"source hash mismatch for {logical_path}")
        replacements[logical_path] = replacement
    occupied.update(range(0, 24))  # volume descriptors, boot area and system area
    occupied.update(range(root_lba, root_lba + (root_size + 2047) // 2048))
    asset_sectors = len(asset) // 2048
    extent = volume_sectors
    new_volume_sectors = volume_sectors + asset_sectors
    if new_volume_sectors + 150 >= 100 * 60 * 75:
        raise SystemExit("extended volume would exceed the MODE1 MSF address range")
    font_record = make_directory_record(ASSET_NAME, extent, len(asset), source_abs)
    # Keep the low-level reader's pathname in an existing root slot; the game
    # does not enumerate an additional file entry from this directory.
    root_records = [font_record if record_name(record) == PRESERVED_NAME else record
                    for record in source_records]
    root_records.append(source_abs)
    patched_root = pack_root(root_records, root_size)
    preserved_abs = next(record for record in root_records if record_name(record) == PRESERVED_NAME)
    abs_row = rows_by_path["/ABS.TXT"]
    if (int.from_bytes(preserved_abs[2:6], "little") != abs_row["lba"]
            or int.from_bytes(preserved_abs[10:14], "little") != abs_row["size"]):
        raise SystemExit("preserved ABS.TXT directory entry does not match the source inventory")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = args.output_dir.with_name(args.output_dir.name + ".partial")
    if stage.exists():
        raise SystemExit(f"staging path already exists: {stage}")
    stage.mkdir()
    try:
        out_track = stage / names[0]
        shutil.copyfile(source_track, out_track)
        shutil.copyfile(source_audio, stage / names[1])
        shutil.copyfile(cue, stage / cue.name)
        with out_track.open("r+b") as stream, source_track.open("rb") as source:
            for logical_path, payload in replacements.items():
                row = rows_by_path[logical_path]
                remaining = len(payload)
                offset = 0
                for lba in range(row["lba"], row["lba"] + (remaining + 2047) // 2048):
                    sector = read_sector(source, lba)
                    used = min(remaining, 2048)
                    sector[USER:USER + used] = payload[offset:offset + used]
                    edc, p, q = disc.mode1_checks(sector)
                    sector[2064:2068] = edc
                    sector[2068:2076] = bytes(8)
                    sector[2076:2248] = p
                    sector[2248:2352] = q
                    stream.seek(lba * SECTOR)
                    stream.write(sector)
                    remaining -= used
                    offset += used
            for i in range(asset_sectors):
                lba = extent + i
                sector = bytearray(SECTOR)
                sector[:12] = b"\x00" + b"\xff" * 10 + b"\x00"
                sector[12:16] = disc.expected_header(lba)
                sector[USER:USER + 2048] = asset[i * 2048:(i + 1) * 2048]
                edc, p, q = disc.mode1_checks(sector)
                sector[2064:2068] = edc
                sector[2068:2076] = bytes(8)
                sector[2076:2248] = p
                sector[2248:2352] = q
                stream.seek(lba * SECTOR)
                stream.write(sector)
            root_changed = 0
            for i in range((root_size + 2047) // 2048):
                lba = root_lba + i
                sector = read_sector(source, lba)
                payload = patched_root[i * 2048:(i + 1) * 2048]
                if sector[USER:USER + 2048] == payload:
                    continue
                sector[USER:USER + 2048] = payload
                edc, p, q = disc.mode1_checks(sector)
                sector[2064:2068] = edc
                sector[2068:2076] = bytes(8)
                sector[2076:2248] = p
                sector[2248:2352] = q
                stream.seek(lba * SECTOR)
                stream.write(sector)
                root_changed += 1
            pvd_sector = read_sector(source, 16)
            pvd_sector[USER + 80:USER + 84] = new_volume_sectors.to_bytes(4, "little")
            pvd_sector[USER + 84:USER + 88] = new_volume_sectors.to_bytes(4, "big")
            edc, p, q = disc.mode1_checks(pvd_sector)
            pvd_sector[2064:2068] = edc
            pvd_sector[2068:2076] = bytes(8)
            pvd_sector[2076:2248] = p
            pvd_sector[2248:2352] = q
            stream.seek(16 * SECTOR)
            stream.write(pvd_sector)
        manifest = {"format": "vh-iso-font-asset-v1", "source_track1_sha256": TRACK_SHA,
                    "input_track1_sha256": input_track_sha,
                    "output_track1_sha256": disc.sha256(out_track), "root_directory_lba": root_lba,
                    "root_directory_bytes": root_size, "root_entries_changed": root_changed,
                    "asset_path": "/FONT.BIN", "asset_lba": extent,
                    "asset_size": len(asset), "asset_sha256": sha(asset),
                    "preserved_original_file": {"path": "/ABS.TXT", "lba": abs_row["lba"],
                                                 "size": abs_row["size"], "sha256": abs_row["sha256"]},
                    "patched_files": {name: sha(data) for name, data in replacements.items()},
                    "changed_sector_count": asset_sectors + root_changed + 1 + sum(
                        (len(payload) + 2047) // 2048 for payload in replacements.values()),
                    "volume_space_sectors": [volume_sectors, new_volume_sectors],
                    "track1_sectors": [track_sectors, new_volume_sectors]}
        (stage / "font-asset-install.json").write_text(json.dumps(manifest, indent=2) + "\n")
        stage.rename(args.output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps({"status": "PASS", "output_dir": str(args.output_dir),
                      "asset_lba": extent, "asset_sha256": sha(asset),
                      "track1_sha256": manifest["output_track1_sha256"]}))


if __name__ == "__main__":
    main()
