#!/usr/bin/env python3
"""Make an isolated single-FILE CUE copy for Yabause 0.9.15 legacy parser."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--source-dir', type=Path, required=True, help='directory containing exactly one paired Track 1/Track 2 BIN set')
p.add_argument('--output-dir', type=Path, required=True)
p.add_argument('--expected-track1-sha256')
a = p.parse_args()
source = a.source_dir
tracks = sorted(source.glob('* (Track 1).bin'))
if len(tracks) != 1:
    raise SystemExit(f'expected exactly one Track 1 BIN; found {len(tracks)}')
t1 = tracks[0]
t2 = source / t1.name.replace('(Track 1)', '(Track 2)')
if not t2.is_file():
    raise SystemExit(f'missing paired audio track: {t2}')
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def sha256_region(path: Path, offset: int, length: int) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        f.seek(offset)
        remaining = length
        while remaining:
            chunk = f.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                raise SystemExit(f'unexpected EOF hashing {path} at offset {offset}')
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()

track1_sha256 = sha256(t1)
track2_sha256 = sha256(t2)
if a.expected_track1_sha256 and track1_sha256.lower() != a.expected_track1_sha256.lower():
    raise SystemExit(f'Track 1 SHA256 mismatch: expected {a.expected_track1_sha256}, got {track1_sha256}')
a.output_dir.mkdir(parents=True, exist_ok=True)
bin_path = a.output_dir / 'vandal-hearts-yabause-combined.bin'
cue_path = a.output_dir / 'vandal-hearts-yabause-combined.cue'
with bin_path.open('wb') as out:
    shutil.copyfileobj(t1.open('rb'), out, 8 * 1024 * 1024)
    shutil.copyfileobj(t2.open('rb'), out, 8 * 1024 * 1024)
combined_track1_sha256 = sha256_region(bin_path, 0, t1.stat().st_size)
combined_track2_sha256 = sha256_region(bin_path, t1.stat().st_size, t2.stat().st_size)
if combined_track1_sha256 != track1_sha256 or combined_track2_sha256 != track2_sha256:
    raise SystemExit('combined CUE image track SHA256 differs from its source track(s)')
def msf(frames: int) -> str:
    minute, frames = divmod(frames, 60 * 75)
    second, frame = divmod(frames, 75)
    if minute > 99:
        raise SystemExit('combined CUE track offset exceeds MSF range')
    return f'{minute:02d}:{second:02d}:{frame:02d}'

# Track 2 follows the current Track 1 extent; derive its MSF after ISO growth.
track1_sectors = t1.stat().st_size // 2352
if t1.stat().st_size % 2352:
    raise SystemExit('Track 1 size is not a whole number of sectors')
cue_path.write_text('FILE "vandal-hearts-yabause-combined.bin" BINARY\n'
                    '  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                    f'  TRACK 02 AUDIO\n    INDEX 00 {msf(track1_sectors)}\n'
                    f'    INDEX 01 {msf(track1_sectors + 150)}\n')
manifest = {'track1': str(t1.resolve()), 'track2': str(t2.resolve()),
                  'track1_sha256': track1_sha256, 'track2_sha256': track2_sha256,
                  'combined_track1_sha256': combined_track1_sha256,
                  'combined_track2_sha256': combined_track2_sha256,
                  'expected_track1_sha256': a.expected_track1_sha256,
                  'track1_bytes': t1.stat().st_size,
                  'track2_bytes': t2.stat().st_size, 'combined_bytes': bin_path.stat().st_size,
                  'combined_cue': str(cue_path.resolve()), 'source_files_modified': False}
(a.output_dir / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, ensure_ascii=False))
