# Vandal Hearts English v1.4 adapter

This adapter is version-specific. It does not define a general Saturn text format. Its scripts live in `adapters/vandal-hearts-english-v1.4/scripts/`; set `SATURN_PROJECT_ROOT` to the extracted project directory when it is not the current working directory. Preserve the original BIN/CUE and build into a separate output directory.

## Source and parser contract

The verified source is the English v1.4 MODE1/2352 Track 1 plus AUDIO Track 2. Known source identities include `0.BIN` SHA-256 `232cecf4ed1683709fa778dba886e69fe6987f2b1fe259fb47d7ac96e1005b40` and Track 1 SHA-256 `6bae252e67460fa7b616adb6d5da9c3a4a92589fa35742a8874b6628f02c2f07`. A changed source hash invalidates this profile until rechecked.

The confirmed English single-byte map is `A1..BA → A..Z`, `C1..DA → a..z` (subtract `0x60`), `A0` space, `0A` line feed. For event dialogue, verified two-byte Japanese-compatible pairs are parsed through the English-specific lead/trail-to-94×94 slot map; do not apply that pair conversion to arbitrary bytes. Known event action bytes include `0x46`/`0x66` as one-byte actions and `0x57` as an action with a non-consuming LF peek. `0x23`, `0x54`, and `0x6F` consume following decimal parameter digits. `0x0A` is line feed. Unknown bytes remain opaque and retain order and source offsets. The `0x57` lookahead guard is mandatory: inspect the following byte without advancing the cursor when it is LF.

Do not treat every NUL as a proven script record boundary. The broad scan found 2,213 NUL-delimited candidate blocks; current translation data has 8,159 parsed text spans, while a separate byte scan reported 8,167 candidate spans. The eight-span discrepancy is unresolved and must not be silently reconciled. NUL offsets and opaque control/action bytes are preserved. The latest capacity audit is incomplete: 2,211 cached blocks, 2 pending, 1,976 passing, and 235 overflow. Capacity pass is not proof of semantic record boundaries or complete coverage.

Translation requests use fixed target locale `zh-Hant` and model `gpt-6-luna`. Keep translation cache rows and usage lineage keyed by stable source identity, locale, model, glossary/version, and segment identity. Deduplicate work by that key; do not count repeated cached segments as new translations or usage. The response payload contains only stable IDs and target strings; source offsets, bytes, control structure, and capacity are program-owned. Never synthesize missing control placeholders or guess their placement.

## Dialogue font and verified runtime scope

The native dialogue renderer uses 16×16 1bpp glyphs (32 bytes each). The current adapter’s dialogue proof uses a 4 MiB cartridge profile and a multi-row helper that preserves the native coordinate/advance path while routing the custom row to the added glyph bank. Its current verified font asset SHA-256 is `292bfea006312b80548fc2810d19e4b1e41a811cdedac6a65c619baf77577d1b`; mapping SHA-256 is `0be2db797fe5f8fb525eecd119b8b4cdc07aa478ebeb7275822fece9ac962b1d`; helper SHA-256 is `698eb1d97e5f14f44e3643b25755e93a83ec773b9a2cbd9c147fdf481b757d8f`. The generated font profile has 1,810 unique non-ASCII target glyphs; its audit reports no missing mappings in the current target set. This is coverage of the audited translation set, not all possible Traditional Chinese text.

The historical 1,810-glyph runtime proof passed the title menu (`新遊戲 / 繼續 / 設定`) and first New Game dialogue (`士兵：這是什麼地方？`) with runtime overrides off. Later v17/v18e tests added selected dialogue, battle labels (`回合4`, `平原0%`), and terrain (`平原0%`) within their tested scopes. The v29 candidate passed no-override cold title/menu, opening dialogue, and the first battle name/class row (`艾許 / 英雄`) on a normal 32 Mbit DRAM cartridge. The 700-frame name/class run had no runtime text/font overrides or invalid opcode; its exact source, state, inputs, screenshot and hashes are recorded in the v29 candidate manifest. The separate v28 HardMode label proof is a forced-gate diagnostic, not a natural HardMode route. The v25 movie candidate has a candidate-born no-override PASS for tested M0001 intervals only; the other seven movie files have static caption coverage but no runtime validation. None of these results proves every dialogue, fixed-UI row, battle HUD field, movie caption, or full-game completion. Read [current HUD and FMV status](vh-hud-experimental.md) before making claims or modifying those paths. The original 1,810-glyph project proof is `analysis/native-full-runtime-proof/native-1810-runtime-proof.json`.

## Script usage and guardrails

From the skill directory, run deterministic checks before using the adapter:

```bash
python3 adapters/vandal-hearts-english-v1.4/scripts/vh_event_text.py --self-check
python3 adapters/vandal-hearts-english-v1.4/scripts/vh_native_dialogue_font.py --self-check
python3 adapters/vandal-hearts-english-v1.4/scripts/build_translated_dat_pool.py --self-check
python3 adapters/vandal-hearts-english-v1.4/scripts/verify_hud_trial_disc.py --help
python3 adapters/vandal-hearts-english-v1.4/scripts/prepare_yabause_cue.py --help
python3 scripts/text_workflow.py self-check
python3 scripts/saturn_disc_patch.py --self-check
```

For project operations, run scripts from the project root or set `SATURN_PROJECT_ROOT`; inspect each script’s `--help` before use. `verify_hud_trial_disc.py` guards the final `/0.BIN`, `/1.BIN`, and `/FONT.BIN` ISO extents against the exact compiled files and patch manifest. `prepare_yabause_cue.py` builds an isolated combined BIN/CUE and validates Track 1 prefix plus audio Track 2 suffix hashes. Use both for the Vandal Hearts image chain. Keep generated batches, cache, and output image outside the skill directory. Do not copy project ROMs, full extracted assets, debugger logs, or large runtime dumps into this skill. Before building, run source-hash, record/control, newline-group, text-width, encoded-capacity, font/map alignment, and fixed-UI capacity checks. A failed or unverified check blocks release claims.
