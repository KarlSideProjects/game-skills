#!/usr/bin/env python3
"""Build/apply a guarded block delta between two binary files (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import tempfile
import zlib

MAGIC = b"SATURN-BINARY-DELTA-V1\n"
MAX_HEADER = 64 * 1024 * 1024
DEFAULT_BLOCK = 2352
IO_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(IO_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_identity(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def _validate_expected_sha(value: str | None, label: str) -> None:
    if value is not None and (len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value)):
        raise ValueError(f"{label} must be a 64-character SHA-256 hex digest")


def build_delta(source: Path, target: Path, delta: Path, block_size: int = DEFAULT_BLOCK,
                expected_source_sha256: str | None = None,
                expected_target_sha256: str | None = None) -> dict:
    source = source.resolve()
    target = target.resolve()
    delta = delta.resolve()
    if source == target or delta in (source, target):
        raise ValueError("source, target, and delta paths must be distinct")
    if delta.exists():
        raise FileExistsError(f"refusing to overwrite {delta}")
    if block_size < 1 or block_size > 16 * 1024 * 1024:
        raise ValueError("block size must be between 1 byte and 16 MiB")
    _validate_expected_sha(expected_source_sha256, "expected source SHA-256")
    _validate_expected_sha(expected_target_sha256, "expected target SHA-256")
    source_before = _file_identity(source)
    target_before = _file_identity(target)
    source_size, target_size = source_before[0], target_before[0]
    source_hash = hashlib.sha256()
    target_hash = hashlib.sha256()
    spans: list[dict[str, int]] = []
    compressed_hash = hashlib.sha256()
    compressed_size = 0
    delta.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="saturn-delta-", dir=delta.parent) as tempdir:
        payload_path = Path(tempdir) / "payload.zlib"
        compressor = zlib.compressobj(level=9)
        with source.open("rb") as sf, target.open("rb") as tf, payload_path.open("wb") as payload:
            smap = mmap.mmap(sf.fileno(), 0, access=mmap.ACCESS_READ) if source_size else None
            tmap = mmap.mmap(tf.fileno(), 0, access=mmap.ACCESS_READ) if target_size else None
            try:
                for offset in range(0, target_size, block_size):
                    end = min(offset + block_size, target_size)
                    tchunk = memoryview(tmap)[offset:end] if tmap is not None else memoryview(b"")
                    source_end = min(end, source_size)
                    schunk = memoryview(smap)[offset:source_end] if smap is not None and offset < source_size else memoryview(b"")
                    target_hash.update(tchunk)
                    if schunk:
                        source_hash.update(schunk)
                    changed = len(schunk) != len(tchunk) or schunk != tchunk
                    if changed:
                        if spans and spans[-1]["offset"] + spans[-1]["length"] == offset:
                            spans[-1]["length"] += len(tchunk)
                        else:
                            spans.append({"offset": offset, "length": len(tchunk)})
                        encoded = compressor.compress(tchunk)
                        if encoded:
                            payload.write(encoded)
                            compressed_hash.update(encoded)
                            compressed_size += len(encoded)
                    tchunk.release()
                    schunk.release()
                if source_size > target_size:
                    tail = memoryview(smap)[target_size:source_size] if smap is not None else memoryview(b"")
                    source_hash.update(tail)
                    tail.release()
                final_piece = compressor.flush()
                if final_piece:
                    payload.write(final_piece)
                    compressed_hash.update(final_piece)
                    compressed_size += len(final_piece)
                payload.flush()
                os.fsync(payload.fileno())
            finally:
                if tmap is not None:
                    tmap.close()
                if smap is not None:
                    smap.close()

        source_seen = source_hash.hexdigest()
        target_seen = target_hash.hexdigest()
        if expected_source_sha256 and source_seen.lower() != expected_source_sha256.lower():
            raise ValueError("source SHA-256 does not match the expected source")
        if expected_target_sha256 and target_seen.lower() != expected_target_sha256.lower():
            raise ValueError("target SHA-256 does not match the expected target")
        if _file_identity(source) != source_before or _file_identity(target) != target_before:
            raise RuntimeError("source or target changed while building delta")
        if sha256_file(source) != source_seen or sha256_file(target) != target_seen:
            raise RuntimeError("source or target content changed while building delta")

        header = {
            "format": "saturn-binary-delta-v1",
            "source_sha256": source_seen,
            "source_size": source_size,
            "target_sha256": target_seen,
            "target_size": target_size,
            "block_size": block_size,
            "spans": spans,
            "payload_codec": "zlib",
            "payload_sha256": compressed_hash.hexdigest(),
            "payload_size": compressed_size,
        }
        header_bytes = (json.dumps(header, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(header_bytes) > MAX_HEADER:
            raise ValueError("delta metadata exceeds header limit")
        output_created = False
        try:
            fd = os.open(delta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            output_created = True
            with os.fdopen(fd, "wb") as out, payload_path.open("rb") as payload:
                out.write(MAGIC)
                out.write(struct.pack(">I", len(header_bytes)))
                out.write(header_bytes)
                while chunk := payload.read(IO_CHUNK):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except Exception:
            if output_created:
                delta.unlink(missing_ok=True)
            raise
    return header


def _read_header(delta: Path) -> tuple[dict, int]:
    with delta.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise ValueError("invalid delta magic")
        raw_len = f.read(4)
        if len(raw_len) != 4:
            raise ValueError("truncated delta header length")
        header_len = struct.unpack(">I", raw_len)[0]
        if header_len == 0 or header_len > MAX_HEADER:
            raise ValueError("invalid delta header length")
        raw_header = f.read(header_len)
        if len(raw_header) != header_len:
            raise ValueError("truncated delta header")
        header = json.loads(raw_header)
        payload_offset = len(MAGIC) + 4 + header_len
    required = {
        "format", "source_sha256", "source_size", "target_sha256", "target_size",
        "block_size", "spans", "payload_codec", "payload_sha256", "payload_size",
    }
    if not isinstance(header, dict) or not required.issubset(header):
        raise ValueError("incomplete delta metadata")
    if header["format"] != "saturn-binary-delta-v1" or header["payload_codec"] != "zlib":
        raise ValueError("unsupported delta format or codec")
    if not isinstance(header["spans"], list):
        raise ValueError("invalid spans list")
    previous_end = 0
    total = 0
    for span in header["spans"]:
        if not isinstance(span, dict) or set(span) != {"offset", "length"}:
            raise ValueError("invalid span entry")
        offset, length = span["offset"], span["length"]
        if not isinstance(offset, int) or not isinstance(length, int) or offset < previous_end or length <= 0:
            raise ValueError("unordered or invalid patch span")
        if offset + length > header["target_size"]:
            raise ValueError("patch span exceeds target size")
        previous_end = offset + length
        total += length
    if total > header["target_size"] or header["target_size"] < 0 or header["source_size"] < 0:
        raise ValueError("invalid source/target size or patch payload length")
    if not isinstance(header["payload_size"], int) or header["payload_size"] < 0:
        raise ValueError("invalid compressed payload size")
    if delta.stat().st_size != payload_offset + header["payload_size"]:
        raise ValueError("delta payload size does not match metadata")
    return header, payload_offset


def apply_delta(source: Path, delta: Path, output: Path,
                expected_source_sha256: str | None = None,
                expected_target_sha256: str | None = None) -> dict:
    source = source.resolve()
    delta = delta.resolve()
    output = output.resolve()
    if source in (delta, output) or delta == output:
        raise ValueError("source, delta, and output paths must be distinct")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    _validate_expected_sha(expected_source_sha256, "expected source SHA-256")
    _validate_expected_sha(expected_target_sha256, "expected target SHA-256")
    header, payload_offset = _read_header(delta)
    if expected_source_sha256 and header["source_sha256"].lower() != expected_source_sha256.lower():
        raise ValueError("delta source SHA-256 does not match the expected source")
    if expected_target_sha256 and header["target_sha256"].lower() != expected_target_sha256.lower():
        raise ValueError("delta target SHA-256 does not match the expected target")
    if source.stat().st_size != header["source_size"]:
        raise ValueError("source size does not match delta")
    if sha256_file(source) != header["source_sha256"]:
        raise ValueError("source SHA-256 does not match delta")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".saturn-delta-apply-", dir=output.parent)
    temp_path = Path(temp_name)
    expected_uncompressed = sum(span["length"] for span in header["spans"])
    span_index = 0
    span_used = 0
    uncompressed_total = 0
    payload_hash = hashlib.sha256()
    decoder = zlib.decompressobj()
    try:
        with os.fdopen(fd, "w+b") as out, source.open("rb") as src, delta.open("rb") as patch:
            remaining_copy = min(header["source_size"], header["target_size"])
            while remaining_copy:
                chunk = src.read(min(IO_CHUNK, remaining_copy))
                if not chunk:
                    raise ValueError("source ended during copy")
                out.write(chunk)
                remaining_copy -= len(chunk)
            out.truncate(header["target_size"])
            patch.seek(payload_offset)
            remaining_payload = header["payload_size"]
            while remaining_payload:
                compressed = patch.read(min(IO_CHUNK, remaining_payload))
                if not compressed:
                    raise ValueError("truncated delta payload")
                remaining_payload -= len(compressed)
                payload_hash.update(compressed)
                pending = compressed
                while pending:
                    decoded = decoder.decompress(pending, IO_CHUNK)
                    pending = decoder.unconsumed_tail
                    if decoded:
                        uncompressed_total += len(decoded)
                        if uncompressed_total > expected_uncompressed:
                            raise ValueError("delta expands beyond declared span bytes")
                        pos = 0
                        while pos < len(decoded):
                            if span_index >= len(header["spans"]):
                                raise ValueError("delta has extra patch bytes")
                            span = header["spans"][span_index]
                            count = min(len(decoded) - pos, span["length"] - span_used)
                            out.seek(span["offset"] + span_used)
                            out.write(decoded[pos:pos + count])
                            pos += count
                            span_used += count
                            if span_used == span["length"]:
                                span_index += 1
                                span_used = 0
            tail = decoder.flush()
            if tail:
                uncompressed_total += len(tail)
                if uncompressed_total > expected_uncompressed:
                    raise ValueError("delta expands beyond declared span bytes")
                pos = 0
                while pos < len(tail):
                    if span_index >= len(header["spans"]):
                        raise ValueError("delta has extra patch bytes")
                    span = header["spans"][span_index]
                    count = min(len(tail) - pos, span["length"] - span_used)
                    out.seek(span["offset"] + span_used)
                    out.write(tail[pos:pos + count])
                    pos += count
                    span_used += count
                    if span_used == span["length"]:
                        span_index += 1
                        span_used = 0
            if not decoder.eof or decoder.unused_data:
                raise ValueError("invalid or trailing zlib payload data")
            if payload_hash.hexdigest() != header["payload_sha256"]:
                raise ValueError("compressed payload SHA-256 mismatch")
            if uncompressed_total != expected_uncompressed or span_index != len(header["spans"]) or span_used:
                raise ValueError("decompressed bytes do not match declared spans")
            out.flush()
            os.fsync(out.fileno())
        if sha256_file(temp_path) != header["target_sha256"]:
            raise ValueError("reconstructed target SHA-256 mismatch")
        try:
            os.link(temp_path, output)
        except FileExistsError:
            raise FileExistsError(f"refusing to overwrite {output}")
        return header
    finally:
        temp_path.unlink(missing_ok=True)


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="saturn-binary-delta-check-") as tmp:
        root = Path(tmp)
        source = root / "source.bin"
        target = root / "target.bin"
        delta = root / "change.sdelta"
        rebuilt = root / "rebuilt.bin"
        source.write_bytes(bytes(range(64)) + b"old-tail")
        target.write_bytes(bytes(range(64))[:17] + b"patched" + bytes(range(24, 64)) + b"+appended!")
        source_sha, target_sha = sha256_file(source), sha256_file(target)
        built = build_delta(source, target, delta, block_size=8,
                            expected_source_sha256=source_sha, expected_target_sha256=target_sha)
        applied = apply_delta(source, delta, rebuilt,
                              expected_source_sha256=source_sha, expected_target_sha256=target_sha)
        if rebuilt.read_bytes() != target.read_bytes() or built["target_sha256"] != applied["target_sha256"]:
            raise AssertionError("binary delta round-trip failed")
        if built["target_size"] <= built["source_size"]:
            raise AssertionError("append case did not grow target")
        refused_delta = root / "wrong-target.sdelta"
        try:
            build_delta(source, target, refused_delta, expected_target_sha256="0" * 64)
        except ValueError as exc:
            if "expected target" not in str(exc):
                raise
        else:
            raise AssertionError("wrong expected target hash was accepted")
        if refused_delta.exists():
            raise AssertionError("failed build created a delta")
        short_target = root / "short.bin"
        short_target.write_bytes(source.read_bytes()[:31])
        short_delta = root / "short.sdelta"
        short_rebuilt = root / "short-rebuilt.bin"
        build_delta(source, short_target, short_delta, block_size=7)
        apply_delta(source, short_delta, short_rebuilt)
        if short_rebuilt.read_bytes() != short_target.read_bytes():
            raise AssertionError("truncate round-trip failed")
        bad_source = root / "wrong.bin"
        bad_source.write_bytes(b"X" * source.stat().st_size)
        rejected_output = root / "must-not-exist.bin"
        try:
            apply_delta(bad_source, delta, rejected_output)
        except ValueError as exc:
            if "SHA-256" not in str(exc) and "size" not in str(exc):
                raise
        else:
            raise AssertionError("source mismatch was accepted")
        if rejected_output.exists():
            raise AssertionError("failed apply created an output")
    print("PASS: source/target hash-guarded delta, append/truncate, round-trip, mismatch rejection")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="create a compressed, source/target-guarded delta")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--target", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True, help="new delta file; existing paths are refused")
    build.add_argument("--block-size", type=int, default=DEFAULT_BLOCK)
    build.add_argument("--expected-source-sha256", help="optional exact source identity guard")
    build.add_argument("--expected-target-sha256", help="optional approved target identity guard")
    apply = sub.add_parser("apply", help="apply a delta to a matching source into a new file")
    apply.add_argument("--source", type=Path, required=True)
    apply.add_argument("--delta", type=Path, required=True)
    apply.add_argument("--output", type=Path, required=True, help="new target file; existing paths are refused")
    apply.add_argument("--expected-source-sha256", help="optional exact source identity guard")
    apply.add_argument("--expected-target-sha256", help="optional approved target identity guard")
    sub.add_parser("self-check", help="run small synthetic round-trip and guard checks")
    args = parser.parse_args()
    if args.command == "build":
        result = build_delta(args.source, args.target, args.output, args.block_size,
                             args.expected_source_sha256, args.expected_target_sha256)
        print(json.dumps({"status": "built", "output": str(args.output), **result}, indent=2))
    elif args.command == "apply":
        result = apply_delta(args.source, args.delta, args.output,
                             args.expected_source_sha256, args.expected_target_sha256)
        print(json.dumps({"status": "applied", "output": str(args.output), "target_sha256": result["target_sha256"], "target_size": result["target_size"]}, indent=2))
    else:
        self_check()


if __name__ == "__main__":
    main()
