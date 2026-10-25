# Launcher API (Ultimate Legends -> legends-models)

How the launcher drives the character-model tool. This builds on the models-xml2 launcher design
(one appearance selector, per-character tier shown with a plain reason, stock = mod off, staging
then replace, preserve the player's other load-order entries, credits visible). API version **1**
(every JSON event carries `"api": 1`).

## In five lines

1. Run `legends-models <scan|plan|build|verify|remove> --json --port <port> --work <cache> [--mua1 <dir>] [--mua2 <dir>]`.
2. Read stdout line by line: each line is one JSON object with an `event` field; the last line is `result` (or `error`).
3. The exit code says how it ended: 0 ok, 10 partial (some heroes stayed stock), 11 nothing written, 3 missing input, 20 download/hash refusal (or a download needed under `--offline`), 30 verify mismatch, 31 verify: files intact but the port was rebuilt since (stale), 40 unsafe path refused, 2 usage, 1 bug, 130 cancelled.
4. `build` writes one folder per converted hero (`<port>\mods\Community-Beast` for Community, `Upgraded-MUA2-Wolverine`, `Upgraded-MUA-Storm`, `Upgraded-Community-Beast` for Upgraded, ...; `--layout pack` = one folder) atomically per folder, sets `+<Folder>` lines in `mods\load-order.txt` (other legends-models groups are set to `-`); `remove --pack <group>` deletes the group's folders and lines.
5. Kill the process to cancel (converter child processes sit in a kill-on-close job and die with it): staging folders start with `.` (the loader ignores them), are cleaned next run, and the previous pack stays as it was.

## Appearance selector -> commands

| Launcher choice | Command |
|---|---|
| Best available | `build --mode upgraded` (per hero MUA2 > MUA1 > Community, first that passes the gate) |
| Community pack | `build --mode community` (no other game needed; downloads from the author's links) |
| XML2 look | `build --mode xml2` |
| Sharpened classic | `build --mode sharpen` (`--ai-model <onnx>` only if the player opted into AI smoothing) |
| Stock | `remove --pack <the pack>`, or keep the folder and switch its line to `-` in the Mods tab |

Default group names (`--pack`): `Models-Upgraded`, `Models-Community`, `Models-XML2-Look`,
`Models-Sharpened`. With the default `--layout per-hero` a build writes one folder per hero (the MUA1 converter's
production layout: `Actors\<skin>.IGB`, `mod.json`, `readme.txt`, plus `CREDITS.md`
and `legends-models.json`); each folder's marker carries `"generator": "legends-models"` and
`"group": "<pack>"`, so the Mods tab can list the heroes under Appearance and toggle them one by one.
`verify`/`remove --pack` accept the group name (all its folders) or one folder name. Building one
group disables the other groups' folders (one appearance at a time; later lines win in the loader).
Rebuilding a hero whose tier changed (e.g. MUA2 now passes) replaces its old folder in the group.

Folder names (0.3.0): `<Tier>-<Hero>` when the group is that tier's own (`Models-Community` -> `Community-Beast`,
`Models-XML2-Look` -> `XML2-Storm`, `Models-Sharpened` -> `Sharpened-Storm`), otherwise `<Group>-<Tier>-<Hero>` with
the group's `Models-` prefix dropped (`Models-Upgraded` -> `Upgraded-MUA-Storm`, `Upgraded-Community-Beast`; a custom
`--pack MyPack` -> `MyPack-MUA-Storm`). So two groups never share a folder: switching appearance only flips `+`/`-`
lines, and `remove --pack` of one group never takes the other's folders. Tiers: `MUA2-`, `MUA-` (MUA1), `XML2-`,
`Community-`, `Sharpened-`. A build never replaces a folder whose marker names another group (exit 40).
Migration: installs from 0.2.x used `<Tier>-<Hero>` in every group. The first 0.3.0 `build` renames each owned
per-hero folder to its group's name before writing anything (folder renamed, its load-order line renamed in place
with the same sign and position, files and hashes untouched) and reports each as a `stage` event
`renamed <old> to <new> (group-qualified folder names)`. The launcher should rescan the Mods tab after a build.

Heads and portraits: each hero folder also carries `HUD\hud_head_<skin>.IGB`, `UI\hud\characters\<skin>.IGB`
and `UI\models\characters\<pid>.IGB` when the chosen source has them (community: all three; MUA1: conversation and
select portraits; MUA2/XML2/Sharpened: none). `build --no-heads` leaves them out, `build --heads-only` writes only them
(no converter, no sampler). A build result's `characters{hero}.heads{path: sha256}` lists what was installed and
`head_notes[]` says in plain text why a role stayed stock. A head problem never refuses the hero.

## Inputs

| Flag | Meaning |
|---|---|
| `--port DIR` | the Legends Classic install (required for build; must contain `Actors\`, `libIGSg.dll`) |
| `--game DIR` | where `mods\` lives, if not the port folder (tests/staging) |
| `--mua1 DIR` | MUA1 2016 Steam folder (`actors.bin`) or 2006 loose data (`actors\*.igb`); optional |
| `--mua2 DIR` | MUA2 folder (`actors.bin`, `materials.bin`, `textures.bin`); optional |
| `--steam DIR` | Steam folder, if MUA1/MUA2 should be found from its library list instead |
| `--work DIR` | private cache: engine copies, extracted sources, animation caches, logs (default `%LOCALAPPDATA%\legends-models`) |
| `--hero NAME` | limit to heroes (repeatable); characters already in the pack are kept |
| `--costume hero=ID` / `hero=tier:ID` | costume choice (`scan --json` lists the costumes per hero and tier) |
| `--strict` | only owner-accepted costumes |
| `--mirror URL` | https base URL serving the community archives by file name (only with the author's OK; credit unchanged); default `$LEGENDS_MODELS_MIRROR`, then the manifest's `mirror`; every archive is hash-checked, a bad mirror file falls back to the author's link |
| `--offline` | never download: community archives come only from the download cache (`<work>\community-cache`); `plan`/`build` show uncached community costumes as unavailable (`reason`: "... is not in the download cache (offline)") and fall to the next tier or stock; `fetch` exits 20 |
| `--no-heads` | do not install the conversation portrait / 3D HUD head / select portrait with the models (`--heads-only`: only those) |
| `--python EXE` | interpreter with NumPy/SciPy/Pillow for the converters (or `LEGENDS_MODELS_PYTHON`) |
| `LEGENDS_MODELS_SAMPLER` | path to `sample_enbaya.exe` if it is not in `<work>\bin` |

The launcher already knows the MUA1/MUA2 paths from its own game setup; pass them explicitly
rather than relying on the Steam scan.

## Events (stdout, one JSON object per line)

Every event: `{"event": ..., "api": 1, "t": seconds-since-start, ...}`.

| event | fields | use |
|---|---|---|
| `plan` | `mode`, `rows[]` (see below) | first line of `build`: show the table before converting |
| `stage` | `stage` (`convert`/`publish`), `message`, `hero`, `tier`, `source` | status text |
| `progress` | `done`, `total`, `message`, `hero` | progress bar (heroes) |
| `workspace` | `path` | the per-port private work folder in use |
| `cache` | `hero`, `tier`, `hit` | a stored result for identical inputs was reused (re-gated; `--rebuild` disables) |
| `engine` | `engine`, `script`, `log` | the converter process started; `log` is the full engine log path |
| `download` | `archive`, `source` (`mirror`/`author`), `bytes`, `sha256`, `character`, `costume` | community archive fetched and verified (`sha256` = the verified archive hash, the manifest's) |
| `gate` | `hero`, `tier`, `passed`, `failures[]` or `owner_accepted` | per attempt; a failure moves to the next tier |
| `warning` | `message`, `hero` | a tier could not run (e.g. missing helper); next tier tried |
| `error` | `message`, `code` | fatal; same code as the exit code. A build where every hero was refused for a download (or a missing helper) exits 20 (or 3) instead of 11 |
| `result` | command-specific (below) | last line |

`scan` hero entries carry `villain: true` for the five boss/villain actors (Magneto, Juggernaut, Sabretooth, Toad, Pyro).

Plan row: `hero`, `name`, `skin`, `tier` (`mua2`/`mua1`/`xml2`/`community`/`sharpen`/`stock`),
`source` (costume ID or name), `status` (`accepted`/`numeric`), `accepted` (owner-accepted costume),
`reason` (plain text: why this tier, or why stock), `fallbacks[]`, `skipped[{tier, reason}]`.

Results:
- `scan`: `installs{port,mua1,mua2}` (`found`, `path`, `flavour`, `problems[]`), `helpers` (`python`,
  `python_modules`, `sampler`, `extractor`), `heroes{key: {name, skin, villain, tiers{tier: {available, reason,
  source, status, note, author, costumes[], costume_info[], cached{}}}}}`.
  `author` is the tier's credited author (`BaconWizard17` for community, `""` for models from the player's games).
  `costume_info[]` is one row per entry of `costumes[]` (same order): `{id, label, author, credit, description?,
  source_url?, bytes?, preview?}` - `id` is what `--costume hero=tier:ID` takes, `label` a display name: for
  MUA1/MUA2/XML2 models the game's own costume name from its stat tables ("Astonishing", "Modern", "Classic";
  "Story character model", or the character's own name such as "Dark Psylocke", for a story-NPC model; read from
  the player's install when scanned, else from the shipped `data/costume-names.json`), the costume name for
  community skins ("Ultimate XML1"); `description` (optional, game models) what it looks like where research
  described it ("Classic yellow/blue"); `credit` one plain line ("by BaconWizard17
  (marvelmods.com)" / "from your own copy of Marvel: Ultimate Alliance"), `source_url` the author's post and `bytes`
  the archive size (community), `preview` an absolute path to the author's character-select portrait PNG when that
  archive is already extracted on this PC (never downloaded by `scan`; absent otherwise).
- `plan`: `mode`, `pack`, `rows[]`.
- `build`: `pack` (group), `layout`, `written`, `folders[]` (per-hero) or `folder` (pack), `enabled`, `files`,
  `characters{hero: {tier, source, file, folder, sha256, gate, owner_accepted, notes[], fallback_from[]}}`, `refused{hero: {attempts[]}}`, `stock{hero: reason}`.
- `verify`: `ok`, `files`, `missing[]`, `extra[]`, `changed[]`, `load_order` (`enabled`/`disabled`/`not listed`),
  `stale` (bool), `stale_folders[]`, `port_changed[]` (`"<folder>: <port file>"`), `port_build_unknown[]` (folders
  made before 0.3.0, which record no port build). Exit 30 when `ok` is false, else 31 when `stale`, else 0.
  Each marker's `port_build` = `{fingerprint, builder, files{port path: sha256}}`: the SHA-256 of every port file the
  folder replaces or was fitted to (its `Actors\<skin>.IGB`, the stock heads it replaces, the hero's animation
  database, `libIGSg.dll`) and the builder version from the port's `_build\manifest.json`. Content, not dates: a
  rebuild that writes the same bytes is not stale. The launcher offers Rebuild on 31.
- `remove`: `folder_removed`, `load_order_entry_removed`.

Human-readable output (no `--json`) is for people; parse only `--json`.

### Player status wording (API 1, schema unchanged)

`scan` tier `note` contains the recipe's short player-facing line, never its technical research note.
Tier `reason` and plan `skipped[].reason` use the same player line for an unavailable conversion.
An absent character model is reported as `not in MUA1` / `not in MUA2`; a character present in
that installed PC edition whose conversion is not implemented is `not converted yet`.
Presence includes story models (MUA1 Dark Cyclops and chair Professor X); it does not imply
that a matching playable or astral conversion exists. Gambit's failed MUA2 conversion reports
`MUA2 conversion not ready: the coat lining shows through the coat` and `available: false`.
Usable recipes say `MUA2 conversion ready`, `<MUA1/XML2/Community> model`,
or `Original model with sharper textures`. Existing status codes and JSON field shapes are unchanged;
no API bump is required. `--verbose` writes technical recipe notes only to stderr, even with `--json`.

## Behaviour the launcher can rely on

- Source installs are only read. The pack is written only under `<port>\mods` (or `--game`), and
  only into folders that carry the legends-models marker; any other folder of the same name is
  refused (exit 40). The player's other load-order lines are kept byte-for-byte.
- `load-order.txt` lines are written as `+Name` / `-Name` exactly as the XML2 Fix loader parses
  them (no space after the sign).
- A refused character is never written; the pack is published only when at least one passed.
- `verify` re-hashes the pack against its marker (or `--manifest`), for a "repair" button.
- The game must be restarted to see a change (the loader indexes mods at start).
- Run one `build` at a time per `--work` folder.

## Timing (this PC, research defaults)

Sharpen ~1-2 s per hero. MUA1 ~30-40 s per hero. Community: one round-2 fit per hero (not timed here; research batches allowed 5 min at 100 iterations) plus a 1-2 MB download.
XML2 several minutes per fitted hero. MUA2 Wolverine ~15 min (fit + bind refinement). Show a
per-hero progress bar; nothing needs a game slot or the desktop.

## Packaging and releases

`tools/freeze.py` (same shape as the Legends Classic builder's `tools/freeze_builder.py`) freezes `legends-models.exe`
with PyInstaller (one folder, console, no UPX; CPython 3.12 x64 + NumPy, SciPy, Pillow; every package pinned in
`requirements-freeze.txt`) and writes `dist/legends-models-<version>-win64.zip` (exe + `_internal\` at the top, for
`tools\legends-models\<version>\`), `dist/legends-models.json` and `dist/SHA256SUMS.txt`. The frozen exe is also
the converters' interpreter: `legends-models.exe -B <script.py> ...` / `-c <code>` run with the bundled libraries
(`__main__.python_shim`), so `--python` is not needed. `--sampler <exe>` bundles the CI-built `sample_enbaya.exe`
(x86; `tools/build_sampler.cmd`); without it `scan` reports `helpers.sampler: null` and the converter tiers that need
clips refuse with exit 3. Community downloads honour `--mirror` so a credited, permitted mirror can replace MediaFire
later without a tool change.

The release workflow (`.github/workflows/release.yml`) builds the sampler with MSVC, runs the unit tests, freezes,
smoke-tests the frozen folder and the unpacked zip on synthetic installs (`tools/freeze_smoke.py`), checks the files
(`tools/verify_dist.py`) and, for a `v*` tag, publishes them as a GitHub release of `ChronoRixun/legends-models`:

| Tag | Release | `legends-models.json` `version` / zip | `min_launcher` |
|---|---|---|---|
| `v0.3.0` | normal, latest: `releases/latest/download/legends-models.json` | `0.3.0` / `legends-models-0.3.0-win64.zip` | `0.5.0` |
| `v0.3.0-beta.1`, `v0.3.0-rc.1` | pre-release, never latest: `releases/download/v0.3.0-beta.1/legends-models.json` | `0.3.0-beta.1` / `legends-models-0.3.0-beta.1-win64.zip` | `0.5.0-0` (a launcher beta of 0.5.0 qualifies) |

The tag's `X.Y.Z` must equal the tool's version (`legends_models/__init__.py`); the exe reports the full version
(`--version`, the markers' `version`). Manifest fields: `version`, `content_version` (pack format; a change means
rebuild), `zip`, `size`, `sha256`, `min_launcher` (the fields `tool_install::parse_manifest` reads), plus `api` (the
JSON API version, 1) and `sampler_bundled`. `min_launcher` 0.5.0 stands for the first launcher release with the
Character models page; it is set in `tools/freeze.py` (`MIN_LAUNCHER`) or with `--min-launcher`.

## What changed for the launcher in 0.3.0 (the launcher's fake tool should emit these)

1. `scan`: per tier `author` and `costume_info[]` (above); the page shows `label` and `credit`/`author` per costume
   and may show `preview` as an image.
2. `download` events: `sha256`, `character`, `costume`.
3. `--offline` exists (global flag): pass it when the launcher runs with -offline instead of only greying Apply.
4. Folder names: `Upgraded-<Tier>-<Hero>` for the Upgraded group (Community/XML2/Sharpened unchanged); one-time
   `stage` events `renamed <old> to <new> (group-qualified folder names)` on the first 0.3.0 build.
5. `verify`: `stale`, `stale_folders[]`, `port_changed[]`, `port_build_unknown[]`, exit 31 = offer Rebuild.
6. Release: `legends-models.json` manifest next to the zip; the exe needs no Python.
