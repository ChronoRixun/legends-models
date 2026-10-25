# Changelog

legends-models' version is `__version__` in `legends_models/__init__.py` (and `pyproject.toml`). A release is a tag:
`vX.Y.Z` for a normal release, `vX.Y.Z-beta.N` / `vX.Y.Z-rc.N` for a pre-release of the same version (the release
notes come from the `## vX.Y.Z` section below; a pre-release needs it).

## v0.3.0

The first public release (a pre-release first: `v0.3.0-beta.1`).

### Changed (beta 2)
- Player-facing status wording: each tier now says "not in MUA2" / "not in MUA1" (that game has no model of the
  character) or "not converted yet", instead of "no recipe"; available tiers read "MUA1 model", "Community model" and
  so on. Developer notes no longer reach the launcher (they stay on `--verbose`). MUA1/MUA2 presence is recorded from
  the installed games' own skin lists.

### Added
- One command-line tool over the four character-model converters (MUA2, MUA1, XML2 look, sharpened textures) and
  the community-skin pipeline (BaconWizard17's XML2 skins, downloaded from the author's own links, hash-checked,
  credited), with the acceptance gate and a normal mod folder for the XML2 Fix mod loader per hero.
- Heads and portraits (conversation portrait, 3D HUD head, character select portrait) installed with each hero
  folder; `--no-heads`, `--heads-only`.
- Optional MUA1 materials stage (`--materials spec|bake|spec+bake`), only used when it is proven byte-identical
  in mesh, weights, skeleton, bind and bounds to the gated model.
- Launcher API 1 (`LAUNCHER-API.md`): per-costume display data (`costume_info[]`, `author`), download events with
  hashes, `--offline`, group-qualified folder names (`Upgraded-MUA-Storm`, with a one-time rename of 0.2.x
  folders), and `verify` reporting a pack made for an older port build as stale (exit 31).
- `legends-models.exe`: a frozen build that needs no Python (it is also the converters' interpreter), released as
  `legends-models-<version>-win64.zip` with the launcher's `legends-models.json` manifest and `SHA256SUMS.txt`.
