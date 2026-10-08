# Repository maintenance instructions

Keep this repository installable as a small collection of agent skills. The canonical skill source is under `skills/<skill-name>/`; top-level README and `docs/` explain installation and route agents to the relevant skill references.

When changing a skill, read its `SKILL.md` and only the references relevant to the task. Keep the entrypoint short, preserve progressive disclosure, and run the bundled skill creator's `quick_validate.py` on changed skills. Run self-checks for changed scripts and check internal Markdown links. Do not include ROMs, disc images, save states, translation batches/caches, private project manifests, emulator binaries, or machine-specific run artifacts.

For Saturn workflows, keep game-specific parsers and addresses inside a clearly versioned adapter. Every game/version needs fresh evidence for record boundaries, controls, codecs, glyph lookup and rendering. The model analyzes unknown structures and translates zh-Hant text; deterministic code handles byte preservation, capacities, manifests, and disc images. If the required `gpt-6-luna` model is unavailable for an operation that needs model reasoning, stop that model step and report the limitation rather than silently substituting another model.

All source paths, fonts and emulator executables must be supplied by the user or configured for their environment. Scripts must not assume a developer home directory, `/tmp` layout, installed ROM, or bundled third-party binary. Preserve source images and create outputs separately. Distinguish declared metadata checks from hashes computed over actual files. Keep licenses with any third-party assets; do not add such assets unless their redistribution terms and attribution are explicit.
