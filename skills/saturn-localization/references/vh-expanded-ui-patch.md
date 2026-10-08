# Vandal Hearts English v1.4 expanded native UI patch

This is a project-stage reference for high-bit menu/label rows and objective strings. It applies only to the English v1.4 source and matching proposal/report files. The native two-byte consumer path was statically confirmed for the command/settings and supplemental UI banks; that establishes encoding compatibility, not final visual acceptance. This reference's builder, extraction helpers, proposal JSON and manifests belong to the external game project and are not included in the public skill package. The shell examples are historical v18e evidence, not runnable commands unless those exact external scripts/data exist. When extending a later candidate, replace `CURRENT` and manifests with that candidate's Track 1 and extract every changed module from that ISO. Never mix older source modules into a newer candidate.

The project builder is `scripts/build_expanded_ui_patch.py`. It accepts the current shared `analysis/full-native-font-map.json`; it does not pin an old map hash. Extract every changed PRG from the exact current source ISO before running it. Do not fill a missing PRG with a whole module from an earlier English extraction: that could erase unrelated changes already integrated in the current image. The builder guards each edited field against its exact source bytes or exact expected target bytes, checks line terminators/NULs separately, and writes only the planned spans back into each extracted module. Objective rows preserve the original `C0` byte at its exact relative offset and retain the final NUL.

The 60-row RESYOPRG/SAKABPRG proposal is accepted only with the explicit include and renderer-confirmation flags. Build a six-file inventory from the current candidate's extent manifest, overlay the current Track 1 component hashes from its latest integrated patch manifest, extract from that exact Track 1, and verify every extracted module against both the sectors and the resolved current hashes before patching:

```bash
PROJECT_ROOT="$PWD"
WORK_DIR="${WORK_DIR:-$PROJECT_ROOT/work}"
mkdir -p "$WORK_DIR"
CURRENT="$PROJECT_ROOT/analysis/hud-layer-probe/extension-trial-v18e-turn-terrain-full-disc"
TRACK="$CURRENT/disc/Vandal Hearts - The Lost Civilization (Japan) [T-En by Paul_met & Stardust Crusaders v1.4] [n] (Track 1).bin"
INTEGRATED="$CURRENT/disc/integrated-patch-manifest.json"
INVENTORY="$WORK_DIR/vh-expanded-ui-six-file-inventory.json"
SOURCE_DIR="$WORK_DIR/vh-expanded-ui-source-from-current-iso"

test "$(sha256sum "$TRACK" | cut -d' ' -f1)" = "d1b2654f57a17c5e6f79415b0b2ddd94f9ea973748a0bc3343b94618b5c26d48"

python3 - "$CURRENT/patch-input/manifest.json" "$INTEGRATED" "$INVENTORY" <<'PY'
import json, sys
from pathlib import Path
rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
integrated = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
selected = {"/BATL1PRG.BIN", "/BATLEPRG.BIN", "/TOWNPRG.BIN", "/WORLDPRG.BIN", "/RESYOPRG.BIN", "/SAKABPRG.BIN"}
source = {r["path"]: r for r in rows if r["path"] in selected}
if set(source) != selected:
    raise SystemExit("current manifest does not contain all six PRGs")
for patch in integrated.get("patches", []):
    if patch["path"] in selected:
        source[patch["path"]]["sha256"] = patch["target_sha256"]
Path(sys.argv[3]).write_text(json.dumps({"file_count": len(source), "files": list(source.values())}, indent=2) + "\n")
PY

python3 "$PROJECT_ROOT/extract_iso.py" "$TRACK" "$INVENTORY" "$SOURCE_DIR"
python3 "$PROJECT_ROOT/verify_extraction.py" "$TRACK" "$SOURCE_DIR" --manifest "$SOURCE_DIR/extract-manifest.json"
python3 - "$INVENTORY" "$SOURCE_DIR/extract-manifest.json" <<'PY'
import json, sys
from pathlib import Path
source = {r["path"]: r for r in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["files"]}
extracted = {r["path"]: r for r in json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))}
for name in ("/BATL1PRG.BIN", "/BATLEPRG.BIN", "/TOWNPRG.BIN", "/WORLDPRG.BIN", "/RESYOPRG.BIN", "/SAKABPRG.BIN"):
    if source[name] != extracted[name]:
        raise SystemExit(f"current ISO source manifest mismatch: {name}")
print("PASS: all six extracted component hashes/extents match the current ISO manifest")
PY
```

Use a fresh, nonexistent output directory. From the project root, run preflight:

```bash
python3 scripts/build_expanded_ui_patch.py \
  --mapping analysis/full-native-font-map.json \
  --source-dir $WORK_DIR/vh-expanded-ui-source-from-current-iso \
  --output-dir $WORK_DIR/vh-expanded-ui-preflight \
  --supplemental-proposals analysis/native-full-runtime-proof/movie-overlay-abi/support-hud-only/UI-audit/proposals.json \
  --supplemental-report analysis/native-full-runtime-proof/movie-overlay-abi/support-hud-only/UI-audit/remaining-ui-text-audit.json \
  --include-supplemental --supplemental-renderer-confirmed --preflight-only
```

A successful preflight reports 102 high-bit menu rows, four objective fields, and 60 supplemental rows; that observed v18e source had 140 edits and 26 fields already equal to their expected encoded targets. The result is only a component patch plan. To materialize the PRGs, choose a fresh output directory and rerun the same command without `--preflight-only`:

```bash
python3 scripts/build_expanded_ui_patch.py \
  --mapping analysis/full-native-font-map.json \
  --source-dir $WORK_DIR/vh-expanded-ui-source-from-current-iso \
  --output-dir $WORK_DIR/vh-expanded-ui-build \
  --supplemental-proposals analysis/native-full-runtime-proof/movie-overlay-abi/support-hud-only/UI-audit/proposals.json \
  --supplemental-report analysis/native-full-runtime-proof/movie-overlay-abi/support-hud-only/UI-audit/remaining-ui-text-audit.json \
  --include-supplemental --supplemental-renderer-confirmed
```

The resulting directory contains only changed modules and `expanded-ui-patch-manifest.json`. This recipe requires the external project's `extract_iso.py`, `verify_extraction.py`, `build_expanded_ui_patch.py`, proposal/report files and candidate ISO; none are bundled here. Integrate only files extracted from that same current ISO, then rerun its final ISO extent/hash verifier and combined BIN/CUE provenance check. Test visible command/settings and supplemental UI rows on the exact newly built image. A successful patch build or renderer-path confirmation does not establish visual correctness, full HUD coverage, or a completed release.

One observed build extracted all six source modules from current v18e Track 1 SHA-256 `d1b2654f57a17c5e6f79415b0b2ddd94f9ea973748a0bc3343b94618b5c26d48`, verified the extraction (`6` files, `722,816` bytes), and wrote six modules with 140 edits and 26 already-patched fields. Its manifest is `$WORK_DIR/vh-expanded-ui-all166-v18e-track-extracted/expanded-ui-patch-manifest.json` (SHA-256 `9db139c7e23785d4f7134035dc915412a641b69b7b296caf3fc212e30ba8d696`). The mapping SHA-256 used for that build was `f4b049e4f5fb6073359d490e64070446323ca9e6245e2e8742a8f7d4a77cc4d8`; treat all these as evidence for that exact input set, not reusable hash pins.

## HardMode title label diagnostic adapter

HardMode is a separate title-screen label path, not one of the 166 high-bit native-text fields. The bounded v28 diagnostic identified the gate byte at `0.BIN` file offset `0x438FE` / CPU `0x060478FE`; `STARTPRG+0x06A0` reads it and branches to the `HARD MODE` label only when it equals `D` (`0x44`). The current unforced byte is `T` (`0x54`). The stock path converts ASCII one byte at a time to tile words and has no CJK-pair handling.

The private project builder is `analysis/native-full-runtime-proof/movie-overlay-abi/support-hud-only/UI-audit/hard-mode/build_hard_mode_8x8_candidate.py`; its guarded v5 components and input/output hashes are in `candidate-components-hardmode-8x8-v5/manifest.json`. It is pinned to exact v27 components and a matching source font, refuses to overwrite its output directory, and is not a portable all-version adapter. Use the v28 proof at `v28-hard-mode-runtime-proof.json` to distinguish its evidence: the candidate-born cold/normal title run had runtime overrides off, while the visible `困難模式` diagnostic used exactly one host-side override `0x060478FE: 0x54→0x44`. It confirms the title label's visible 4-glyph path and ASCII preservation for that capture. It does not confirm the normal game route that changes the gate, natural HardMode unlock, safe tile allocation in every mode, or a persistent gate-byte patch. Never apply the diagnostic override as ROM content. The v29 combat name/class row separately passed without overrides; see [HUD and FMV status](vh-hud-experimental.md).
