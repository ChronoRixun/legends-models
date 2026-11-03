# Agent guidance for legends-models

These instructions apply throughout this repository, including automated pull-request reviews.

## Project and sources of truth

This Python tool builds optional character-model mods for Legends Classic on the player's PC from legally owned games or permissioned community downloads. It writes XML2 Fix mod packs; the launcher and content builder live elsewhere. Read `README.md`, `LAUNCHER-API.md`, `pyproject.toml` and the affected `legends_models/` implementation. Release tooling and CI live in `tools/` and `.github/workflows/`.

## Content and privacy boundaries

Keep source and tool releases to code, original documentation, format facts, manifests and synthetic fixtures. Never add game assets, converted output, decoded game data, official logos, game screenshots, game-download links or personal paths/IPs/emails/tokens. Do not bundle community skins in source or the tool package.

The README documents a separate permissioned `community-skins-backup-1` release of unmodified author archives. Preserve the author's terms and credits, original bytes, SHA-256 checks and author-first/mirror-fallback policy in `legends_models/data/community.json` and `community-credits.md`. This limited exception does not authorize new redistribution or publishing; do not broaden it or promise permissions that have not been established.

## Implementation and review

- Keep game inputs read-only. Write only to documented work/cache locations and owned mod outputs; preserve other mods and load-order entries. Respect ownership markers, hash checks, staging/atomic replacement, cancellation and safe removal in `legends_models/packs.py`.
- Preserve the port's skeleton, animations, bounds and gameplay structure. Keep the stock-relative gate in `legends_models/gate.py` bound to exact output bytes; numeric acceptance does not prove visual acceptance. A refused or unavailable tier must fall back safely.
- Keep `--offline` download-free, archives bounded and paths confined, author terms explicit and generated credits accurate. Preserve vendored-converter provenance under `legends_models/engines/`.
- Treat JSON-line events, API version, exit codes, pack/group ownership, stale-port checks and manifests as launcher contracts. Read `LAUNCHER-API.md` before changing them and identify companion changes.
- Review concrete failure paths with file/line, trigger and consequence; distinguish hypotheses, synthetic checks and real model/runtime acceptance.

## Validation

CI uses Python 3.12 with `requirements-freeze.txt`. Relevant root checks are:

```
python -m unittest discover -s tests -t tests -v
python tools/check_no_game_content.py --self-test
python tools/check_no_game_content.py --all
```

Use synthetic fixtures; keep owned inputs, conversion outputs and captures out of Git and PR attachments. The release workflow also builds `tools/build_sampler.cmd` with MSVC x86, freezes with `python tools/freeze.py --sampler tools/sample_enbaya.exe --smoke`, then checks the unpacked package and distribution. Read the workflow for the exact version/manifest arguments. The sampler uses the player's own `libIGSg.dll`; it is not a game launch. Report unavailable owned-input or visual validation honestly.

Preserve LF and byte-sensitive provenance/hashes; review allowlist changes individually. Keep changes scoped and preserve unrelated work. Do not change release versions or publish/tag/download new community archives merely to resolve a review. A review does not authorize commits, pushes, merges, launches, deployment or announcements.
