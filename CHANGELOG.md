# Changelog

legends-models' version is `__version__` in `legends_models/__init__.py` (and `pyproject.toml`). A release is a tag:
`vX.Y.Z` for a normal release, `vX.Y.Z-beta.N` / `vX.Y.Z-rc.N` for a pre-release of the same version (the release
notes come from the `## vX.Y.Z` section below; a pre-release needs it).

## v0.4.1 (2026-11-14)

### Fixed
- The Character models page showed a developer note ("Owner accepted this exact output hash; later numeric criteria
  do not revoke that decision") next to approved models. It now says **Approved model**; when your game files differ
  from an approved model, it says that hero is being converted again. The detailed reason stays in `--verbose`.

## v0.4.0 (2026-11-14)

### Added
- Upgraded mode offers the exact owner-approved MUA2 Iceman 0801 and Jean/Phoenix
  12301 and arm-repaired 12302 outputs. Jean defaults to 12302. Reviewed private
  bundles must be provisioned separately into the shared work cache; game assets
  are never included in the tool download. MUA2 remains ahead of MUA1 when available.
- Hash-bound accepted output reuse before the ordinary cache, including Rebuild.
  Reports retain the current numeric result separately from exact owner approval.
- Offline provisioning with pinned actor, report and input-manifest receipts,
  atomic staging and a public-package guard against private bundle leakage.
- Bounded bind refinement and live progress for fresh supported conversions,
  preserving the existing fitter objective, layer coupling and recipes.

### Fixed
- Scan/plan offer the reuse-only Jean/Iceman MUA2 choices only after a complete
  local bundle passes integrity and catalogue checks. Missing bundles now plan
  the same fallback that Build writes, preventing repeated launcher Update prompts.
  Existing approved costumes also compare the small owned source actor and stock
  template hashes; unchanged approved inputs keep exact reuse, while changed
  inputs retain the supported fresh-fit path.
- Upgraded no longer offers the MUA1 Jean/Phoenix 12301 fresh fit: combat
  arm and glove distortion can pass its old global gate. Without an available
  approved MUA2 bundle, Jean falls back to Community until that fit is repaired.
- Cancel checks before conversion, between characters and tier attempts, after a
  converter exits, and before publication. A cancelled failure cannot start a
  fallback. Publication already underway finishes consistently (API 1 unchanged).

Existing MUA1 Phoenix fresh fits are separate from the approved MUA2 outputs;
updating the tool alone does not replace installed mods. Build/Rebuild the models
with owned matching inputs and the private accepted cache provisioned. The public tool cannot yet reproduce these approved MUA2 outputs from clean
owned inputs alone; private provisioning is required. Missing or
changed accepted inputs are reported and safely fall back; they never silently
produce a new owner-approved model. Launcher minimum stays 0.5.0-0.

## v0.3.0 (2026-11-05)

The first stable release, published with Ultimate Legends 0.5.0 (the first launcher with the Character models page).
It is the tool of `v0.3.0-beta.3` with no change to the conversion, packs or launcher API, and it is now the launcher's
Stable-channel tool (`releases/latest`); the Beta channel moves to it too. Everything since `v0.3.0-beta.1`, the
first public pre-release, is below.

### Changed (stable)
- `legends-models.json` says `min_launcher` `0.5.0-0` for this stable release as for the betas: Ultimate Legends
  0.5.0 or any beta / release candidate of it. The launcher's Beta channel also picks a stable tool, and a plain
  `0.5.0` would have refused the 0.5.0 betas and release candidates that run it exactly as they ran beta 3.
- README status: the first stable release.

### Changed (beta 3)
- Community skins have a backup download. BaconWizard17's own links are still tried first; if one fails (HTTP
  error, timeout, removed link, or a file that does not match the pinned SHA-256), the tool downloads the
  unmodified backup copy from this project's GitHub release `community-skins-backup-1`, kept with his permission
  (2026-10-25) and credited to him with a link to his marvelmods.com thread. The backup is checked against the same
  size and SHA-256; a wrong file is refused. `--no-mirror` (or `--mirror off`, `LEGENDS_MODELS_MIRROR=off`) uses
  his links only. The `download` event gains `source_label` ("author's link" / "GitHub backup") and, for a backup
  download, `author_error` (why his link failed); `source` stays `author`/`mirror`.
- README and credits say plainly what is hosted: the tool bundles nothing; the backup release holds his original
  archives byte-for-byte, with his terms (credit, no modification, no selling).

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
