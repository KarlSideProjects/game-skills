#!/usr/bin/env python3
"""Patch fixed-size files in a validated Vandal Hearts MODE1/2352 CUE set."""

import argparse
import hashlib
import json
import re
import shlex
import shutil
import tempfile
from pathlib import Path, PurePosixPath

SECTOR = 2352
USER_START, USER_END = 16, 2064
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
REFERENCE_EDC = "c513682b"
REFERENCE_ECC_SHA256 = "619e335e55204ce597079fd54f8df335793665dc098a41cb0c59f36185705394"
PATTERN_EDC = "747c5662"
PATTERN_ECC_SHA256 = "c5389a11553647b2f756d2ff63d0ae7daca9828a9d3ceb099656fd66bb0133c5"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def edc_table():
    table = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ (0xD8018001 if value & 1 else 0)
        table.append(value)
    return table


EDC_TABLE = edc_table()


def ecc_tables():
    forward, backward = [], [0] * 256
    for value in range(256):
        doubled = value << 1
        if doubled & 0x100:
            doubled ^= 0x11D
        forward.append(doubled)
        backward[value ^ doubled] = value
    return forward, backward


ECC_FORWARD, ECC_BACKWARD = ecc_tables()


def ecc_block(data, major_count, minor_count, major_mult, minor_inc):
    size = major_count * minor_count
    result = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            value = data[index]
            index += minor_inc
            if index >= size:
                index -= size
            a ^= value
            b ^= value
            a = ECC_FORWARD[a]
        a = ECC_BACKWARD[ECC_FORWARD[a] ^ b]
        result[major] = a
        result[major + major_count] = a ^ b
    return result


def mode1_checks(sector):
    """Return little-endian EDC, P and Q for a full 2352-byte Mode 1 sector."""
    edc = 0
    for value in sector[:2076 - 12]:
        edc = EDC_TABLE[(edc ^ value) & 0xFF] ^ (edc >> 8)
    encoded = bytearray(sector)
    encoded[2064:2068] = edc.to_bytes(4, "little")
    encoded[2068:2076] = bytes(8)
    p = ecc_block(encoded[12:2076], 86, 24, 2, 86)
    encoded[2076:2248] = p
    q = ecc_block(encoded[12:2248], 52, 43, 86, 88)
    return edc.to_bytes(4, "little"), bytes(p), bytes(q)


def bcd(value):
    return ((value // 10) << 4) | (value % 10)


def expected_header(lba):
    frame = lba + 150
    minute, frame = divmod(frame, 60 * 75)
    second, frame = divmod(frame, 75)
    if minute > 99:
        raise ValueError(f"LBA cannot be represented in a CD MSF header: {lba}")
    return bytes((bcd(minute), bcd(second), bcd(frame), 1))


def verify_sector(sector, lba):
    if len(sector) != SECTOR or sector[:12] != SYNC:
        raise ValueError(f"Invalid MODE1/2352 sync/header at LBA {lba}")
    if sector[12:16] != expected_header(lba):
        raise ValueError(f"MODE1 header address/mode mismatch at LBA {lba}")


def cue_tracks(cue):
    raw = Path(cue).read_bytes()
    text = raw.decode("utf-8")
    rows = []
    for line in text.splitlines():
        if line.strip().upper().startswith("FILE "):
            parts = shlex.split(line.strip())
            if len(parts) != 3 or parts[2].upper() != "BINARY":
                raise ValueError(f"Unsupported CUE FILE line: {line}")
            rows.append(parts[1])
    if len(rows) != 2:
        raise ValueError("Expected exactly two binary tracks in CUE")
    if not re.search(r"TRACK\s+01\s+MODE1/2352", text, re.I) or not re.search(r"TRACK\s+02\s+AUDIO", text, re.I):
        raise ValueError("Expected Track 1 MODE1/2352 followed by Track 2 AUDIO")
    for name in rows:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise ValueError(f"CUE track must be a sibling filename: {name}")
    return rows


def safe_payload(root, manifest_path):
    rel = PurePosixPath(manifest_path.lstrip("/"))
    if not rel.parts or rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError(f"Unsafe manifest path: {manifest_path}")
    root = Path(root).resolve(strict=True)
    payload = (root / Path(*rel.parts)).resolve(strict=True)
    if payload == root or root not in payload.parents or not payload.is_file():
        raise ValueError(f"Payload escapes extracted directory or is not a file: {manifest_path}")
    return payload


def parse_inputs(cue, extracted, manifest_path, plan_path, expected_sha):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("Extraction manifest must be a non-empty list")
    if not isinstance(plan, dict) or plan.get("source_track_sha256") != expected_sha:
        raise ValueError("Patch plan source_track_sha256 does not match --source-sha256")
    patches = plan.get("patches")
    if not isinstance(patches, list) or not patches:
        raise ValueError("Patch plan must contain at least one patch")
    if len(expected_sha) != 64 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("--source-sha256 must be a lowercase SHA-256 hex digest")

    rows, by_path, occupied = [], {}, set()
    for row in manifest:
        path, lba, size, digest = row.get("path"), row.get("lba"), row.get("size"), row.get("sha256")
        if not isinstance(path, str) or not isinstance(lba, int) or isinstance(lba, bool) or not isinstance(size, int) or isinstance(size, bool) or lba < 0 or size < 0:
            raise ValueError(f"Malformed manifest row: {row!r}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid source file SHA-256 for {path}")
        if path in by_path:
            raise ValueError(f"Duplicate manifest path: {path}")
        row = {"path": path, "lba": lba, "size": size, "sha256": digest}
        by_path[path] = row
        rows.append(row)
        count = (size + 2047) // 2048
        for sector_lba in range(lba, lba + count):
            if sector_lba in occupied:
                raise ValueError(f"Overlapping manifest extents at LBA {sector_lba}")
            occupied.add(sector_lba)

    target_by_path = {}
    for patch in patches:
        path, digest = patch.get("path"), patch.get("sha256")
        if path not in by_path or path in target_by_path:
            raise ValueError(f"Unknown or duplicate patch path: {path}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid target SHA-256 for {path}")
        payload = safe_payload(extracted, path)
        row = by_path[path]
        data = payload.read_bytes()
        if len(data) != row["size"]:
            raise ValueError(f"Patch changes file capacity/size for {path}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Patch target SHA-256 mismatch for {path}")
        if digest == row["sha256"]:
            raise ValueError(f"Patch plan contains an unchanged file: {path}")
        target_by_path[path] = data
    return rows, target_by_path


def validate_source(track, expected_sha, rows):
    if track.stat().st_size % SECTOR:
        raise ValueError("Track 1 size is not divisible by 2352")
    if sha256(track) != expected_sha:
        raise ValueError("Track 1 source SHA-256 mismatch")
    sector_count = track.stat().st_size // SECTOR
    seen = set()
    with Path(track).open("rb") as src:
        for row in rows:
            size, lba = row["size"], row["lba"]
            count = (size + 2047) // 2048
            if lba + count > sector_count:
                raise ValueError(f"File extent exceeds Track 1: {row['path']}")
            remaining = size
            payload = hashlib.sha256()
            for sector_lba in range(lba, lba + count):
                if sector_lba in seen:
                    raise ValueError(f"Overlapping manifest extents at LBA {sector_lba}")
                seen.add(sector_lba)
                src.seek(sector_lba * SECTOR)
                sector = src.read(SECTOR)
                verify_sector(sector, sector_lba)
                used = min(remaining, 2048)
                payload.update(sector[USER_START:USER_START + used])
                remaining -= used
            if payload.hexdigest() != row["sha256"]:
                raise ValueError(f"Source extent hash does not match manifest: {row['path']}")


def patch_track(source, output, rows, targets):
    changed = []
    row_by_path = {row["path"]: row for row in rows}
    with Path(source).open("rb") as src, Path(output).open("r+b") as dst:
        for path, payload in targets.items():
            row = row_by_path[path]
            remaining, pos = row["size"], 0
            for lba in range(row["lba"], row["lba"] + (row["size"] + 2047) // 2048):
                src.seek(lba * SECTOR)
                src_sector = src.read(SECTOR)
                sector = bytearray(src_sector)
                verify_sector(sector, lba)
                used = min(remaining, 2048)
                segment = payload[pos:pos + used]
                if sector[USER_START:USER_START + used] != segment:
                    sector[USER_START:USER_START + used] = segment
                    edc, p, q = mode1_checks(sector)
                    sector[2064:2068] = edc
                    sector[2068:2076] = bytes(8)
                    sector[2076:2248] = p
                    sector[2248:2352] = q
                    dst.seek(lba * SECTOR)
                    dst.write(sector)
                    changed.append({"lba": lba, "source_sector_sha256": hashlib.sha256(src_sector).hexdigest(),
                                    "output_sector_sha256": hashlib.sha256(sector).hexdigest()})
                remaining -= used
                pos += used
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="run the pinned independent Mode 1 vector")
    parser.add_argument("--cue", type=Path)
    parser.add_argument("--extracted", type=Path, help="directory containing modified extracted files")
    parser.add_argument("--manifest", type=Path, help="original extract-manifest.json")
    parser.add_argument("--patch-plan", type=Path, help="JSON listing changed paths and target hashes")
    parser.add_argument("--source-sha256", help="expected source Track 1 SHA-256")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if any(value is None for value in (args.cue, args.extracted, args.manifest, args.patch_plan, args.source_sha256, args.output_dir)):
        parser.error("patch mode requires --cue, --extracted, --manifest, --patch-plan, --source-sha256 and --output-dir")

    cue = args.cue.resolve(strict=True)
    cue_names = cue_tracks(cue)
    source_track, source_audio = cue.parent / cue_names[0], cue.parent / cue_names[1]
    rows, targets = parse_inputs(cue, args.extracted, args.manifest, args.patch_plan, args.source_sha256)
    if not source_track.is_file() or not source_audio.is_file():
        raise ValueError("CUE-referenced track file is missing")
    validate_source(source_track, args.source_sha256, rows)
    if args.output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {args.output_dir}")
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_output = args.output_dir.with_name(args.output_dir.name + ".partial")
    if stage_output.exists():
        raise FileExistsError(f"Staging path already exists: {stage_output}")

    cue_sha, audio_sha = sha256(cue), sha256(source_audio)
    with tempfile.TemporaryDirectory(prefix=".saturn-disc-patch-", dir=args.output_dir.parent) as tmp:
        stage = Path(tmp)
        out_track, out_audio, out_cue = stage / cue_names[0], stage / cue_names[1], stage / cue.name
        shutil.copyfile(source_track, out_track)
        shutil.copyfile(source_audio, out_audio)
        shutil.copyfile(cue, out_cue)
        if sha256(out_track) != args.source_sha256:
            raise ValueError("Track 1 changed while it was copied")
        if sha256(out_audio) != audio_sha or sha256(out_cue) != cue_sha:
            raise ValueError("Audio track or CUE changed while it was copied")
        changes = patch_track(out_track, out_track, rows, targets)
        if not changes:
            raise ValueError("Patch plan produced no changed sectors")
        patch_manifest = {
            "format": "saturn-mode1-2352-patch-v1",
            "source_track1_sha256": args.source_sha256,
            "output_track1_sha256": sha256(out_track),
            "source_audio_sha256": audio_sha,
            "output_audio_sha256": sha256(out_audio),
            "source_cue_sha256": cue_sha,
            "output_cue_sha256": sha256(out_cue),
            "patches": [{"path": path, "lba": next(r["lba"] for r in rows if r["path"] == path),
                         "size": len(data), "source_sha256": next(r["sha256"] for r in rows if r["path"] == path),
                         "target_sha256": hashlib.sha256(data).hexdigest()}
                        for path, data in targets.items()],
            "changed_sectors": changes,
            "changed_sector_count": len(changes),
        }
        (stage / "patch-manifest.json").write_text(json.dumps(patch_manifest, indent=2) + "\n", encoding="utf-8")
        stage_output.mkdir()
        try:
            for child in stage.iterdir():
                shutil.move(str(child), stage_output / child.name)
            stage_output.rename(args.output_dir)
        except Exception:
            shutil.rmtree(stage_output, ignore_errors=True)
            raise
    print(json.dumps({"status": "PASS", "output_dir": str(args.output_dir),
                      "changed_sector_count": len(changes),
                      "source_track1_sha256": args.source_sha256,
                      "output_track1_sha256": patch_manifest["output_track1_sha256"]}, ensure_ascii=False))


def self_check():
    sector = bytearray(SECTOR)
    sector[:16] = SYNC + bytes((0, 2, 0, 1))
    edc, p, q = mode1_checks(sector)
    assert edc.hex() == REFERENCE_EDC
    assert hashlib.sha256(p + q).hexdigest() == REFERENCE_ECC_SHA256
    assert len(sector[:12]) == 12 and len(sector[12:16]) == 4
    assert len(sector[16:2064]) == 2048 and len(edc) == 4
    assert len(sector[2068:2076]) == 8 and len(p) == 172 and len(q) == 104

    patterned = bytearray(SECTOR)
    patterned[:12] = SYNC
    patterned[12:16] = expected_header(12345)
    patterned[16:2064] = bytes(range(256)) * 8
    edc, p, q = mode1_checks(patterned)
    assert edc.hex() == PATTERN_EDC
    assert hashlib.sha256(p + q).hexdigest() == PATTERN_ECC_SHA256
    assert patterned[12:16] == bytes.fromhex("02464501")
    print("PASS: independent getECC zero and patterned Mode 1 vectors; sector layout")


if __name__ == "__main__":
    main()
