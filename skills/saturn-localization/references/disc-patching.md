# MODE1/2352 extent patching

Use `scripts/saturn_disc_patch.py` only for this validated Vandal Hearts CUE profile: exactly two sibling BINARY files, Track 01 `MODE1/2352`, Track 02 `AUDIO`. The extractor manifest is the baseline; it maps ISO paths to LBA, byte size, and source-file SHA-256. The source Track 1 SHA-256 is mandatory and must identify the exact input image.

```sh
python3 scripts/saturn_disc_patch.py \
  --cue SOURCE.cue \
  --extracted MODIFIED_FILES \
  --manifest extract-manifest.json \
  --patch-plan patch-plan.json \
  --source-sha256 TRACK1_SHA256 \
  --output-dir NEW_OUTPUT_DIR
```

`MODIFIED_FILES` contains only target files named by the plan. The plan is a JSON object:

```json
{
  "source_track_sha256": "same value as --source-sha256",
  "patches": [{"path": "/0.BIN", "sha256": "target file SHA-256"}]
}
```

The writer verifies the full raw Track 1 hash, every manifest extent's original bytes/hash, each MODE1 sync/mode/MSF header against its LBA, non-overlapping in-range extents, target file hashes and unchanged sizes, then patches only sectors whose user bytes differ. It writes a complete new BIN/CUE set and `patch-manifest.json` under a previously unused output directory. It preserves each sector's 12-byte sync and 4-byte address/mode header, and copies audio and CUE bytes unchanged.

Raw MODE1/2352 layout: sync `[0:12]`; BCD MSF address and mode `[12:16]`; 2048 user bytes `[16:2064]`; little-endian EDC `[2064:2068]`; eight reserved zero bytes `[2068:2076]`; P parity `[2076:2248]`; Q parity `[2248:2352]`. MSF is relative to the track with the standard 150-frame offset. EDC covers `[0:2064]`; P is generated from `[12:2076]`; Q is generated after P from `[12:2248]`.

Run the pinned independent vectors/layout check with:

```sh
python3 scripts/saturn_disc_patch.py --self-check
```

The expected vectors were generated with `dantecsm/getECC` at commit [`60862b5974ffe82334b7f36a31927892c22f7901`](https://github.com/dantecsm/getECC/tree/60862b5974ffe82334b7f36a31927892c22f7901), an MIT-licensed Node.js implementation whose README documents Sega Saturn/Mode 1 support. For a zero-filled sector with header `00:02:00`, EDC is `c513682b` and the 276 parity bytes hash to `619e335e55204ce597079fd54f8df335793665dc098a41cb0c59f36185705394`. For a 2048-byte `00..FF` repeated pattern at LBA 12345 (header `02:46:45`), EDC is `747c5662` and parity hashes to `c5389a11553647b2f756d2ff63d0ae7daca9828a9d3ceb099656fd66bb0133c5`. The script embeds only expected EDC values and parity hashes; no upstream code is included.
