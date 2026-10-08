# Vandal Hearts v25 candidate rebuild

> Historical candidate recipe. The current integrated line is v29; no-override cold title/menu, opening dialogue, and first-battle name/class visual QA passed. This v25 builder and its pinned v18e inputs do not rebuild v29 and must not be presented as the current package or a source-to-final workflow. The builder, candidate components and manifests are external game-project files, not included in this public skill. Use only when those dependencies exist and hashes match. The output package README and candidate manifest are authoritative; verify Track 1 hash against `vh-hud-experimental.md` before treating a package as the runtime-verified v29 candidate.

Use this only with the matching English v1.4 project tree and pinned candidate inputs. **This rebuild starts from the v18e intermediate Track 1 (`d1b2654f…`), not the user's original Track 1 (`6bae252e…`). It packages v25 from that prepared base; it is not an original-to-v25 full rebuild.** The project-owned builder is `analysis/native-full-runtime-proof/movie-overlay-abi/rebuild_v25_candidate.py`; keep it in that directory because it derives the project root from its own location. It uses only Python's standard library and the skill's `saturn_disc_patch.py`.

The guarded inputs are the source CUE and Track 1/2 (SHA-256 `d1b2654f57a17c5e6f79415b0b2ddd94f9ea973748a0bc3343b94618b5c26d48` and `245f3f846b483bb2028f2ce86277b4fea5c98f2f5412450f97b6e3b198ac4915`), the four candidate components `0.BIN`, `1.BIN`, `MOVIEPRG.BIN`, and `FONT.BIN`, the v18e integrated base manifest, and `english-extracted/extract-manifest.json` for ISO file extents. The script checks component and timeline hashes, refuses to replace an existing output directory, and verifies the resulting Track 1 hash (`93f46fe2b25d9598e3f047ec2c764718b5e3ca51d860368a3e8827eed7ac8209`) plus unchanged Track 2/CUE. Mismatched inputs stop the build; do not bypass those guards or substitute a similarly named image.

From the matching project tree, set the installed skill and project paths, then preflight before building:

```bash
SKILL_DIR=/path/to/installed/saturn-localization
PROJECT_ROOT='/path/to/zh-Hant-build'
BUILDER="$PROJECT_ROOT/analysis/native-full-runtime-proof/movie-overlay-abi/rebuild_v25_candidate.py"
COMPONENTS="$PROJECT_ROOT/analysis/native-full-runtime-proof/movie-overlay-abi/rom-candidate-100-dual-resident-v25-fastlut-x0-final-layout"
BASE="$PROJECT_ROOT/analysis/hud-layer-probe/extension-trial-v18e-turn-terrain-full-disc/disc/integrated-patch-manifest.json"
CUE="$PROJECT_ROOT/analysis/hud-layer-probe/extension-trial-v18e-turn-terrain-full-disc/disc/Vandal Hearts - The Lost Civilization (Japan) [T-En by Paul_met & Stardust Crusaders v1.4] [n].cue"

python3 "$BUILDER" --cue "$CUE" --components "$COMPONENTS" \
  --base-manifest "$BASE" --patcher "$SKILL_DIR/scripts/saturn_disc_patch.py" \
  --output "$PROJECT_ROOT/output/vh-v25-candidate-preflight" --preflight-only
```

After `preflight: PASS`, choose a new output path that does not exist and run the same command without `--preflight-only`. The builder packages the candidate BIN/CUE through `saturn_disc_patch.py`; its `candidate-status.json` explicitly retains the pending combat name/class visual gate. Build success confirms pinned image hashes and packaging only. It does not prove full gameplay coverage or release readiness; use [HUD and FMV status](vh-hud-experimental.md) and the emulator provenance procedure for runtime claims.

The final-layout README and component manifests remain with the project inputs. This skill references the source-controlled builder rather than copying ROM-specific build code or assets into the shared skill.

## Upstream rebuild boundary

The manifests record this earlier chain: original Track 1 `6bae252e…` → fixed-disc intermediate `4e13cd79…` → font-installed native base `7fd86fa2…` → v17 `66b87265…` → v18e `d1b2654f…` → v25 `93f46fe2…`. The v25 script reproducibly covers only the last arrow. The project also retains outputs and stage manifests for translated DATs, fixed UI, cart font, DRAM32M bootstrap, and the v17/v18e patch stages. No maintained top-level driver or complete recorded CLI invocation currently reconstructs all stages from the original BIN/CUE; the stored manifests identify hashes and inputs but do not themselves regenerate those components. Treat original-to-v25 rebuilding as incomplete until that upstream chain is scripted and verified from the original source hashes.
