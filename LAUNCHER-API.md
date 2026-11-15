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
5. For 0.4.0+, use the cooperative request described below. Cancellation before publication leaves the previous pack intact; cancellation after publication starts completes that publication. Forced process termination is safe during conversion, but must not be used as a publication transaction guarantee.

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
| `--mirror URL` | https base URL serving the community archives by file name, tried only when the author's link fails (HTTP error, timeout, removed link, size/SHA-256 mismatch); default `$LEGENDS_MODELS_MIRROR`, then the manifest's `mirror` (the GitHub backup release `community-skins-backup-1`, kept with the author's permission); the mirror file is hash-checked the same way and refused on a mismatch; `off` = no mirror |
| `--no-mirror` | `build`/`fetch`: the author's links only, never the backup (same as `--mirror off` or `LEGENDS_MODELS_MIRROR=off`) |
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
| `progress` | `done`, `total`, `message`, `hero`; staged refinement fields below | separate hero and objective-call progress |
| `workspace` | `path` | the per-port private work folder in use |
| `cache` | `hero`, `tier`, `hit` | a stored result for identical inputs was reused; `reason`, `refit`, `owner_accepted`, `acceptance_reason` explain accepted reuse or fresh fitting (`--rebuild` disables only ordinary result caches) |
| `engine` | `engine`, `script`, `log` | the converter process started; `log` is the full engine log path |
| `download` | `archive`, `source` (`author`/`mirror`), `source_label` (`author's link`/`GitHub backup`; `backup mirror` for a non-GitHub `--mirror`), `bytes`, `sha256`, `character`, `costume`, `author_error` (mirror only: why the author's link failed) | community archive fetched and verified (`sha256` = the verified archive hash, the manifest's); show `source_label` in the Downloads tab |
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

### Live refinement progress (additive API 1 fields)

Distinguish `progress` with `stage: "refine_bind"` from the existing hero-count events (which
have no stage). `done`/`total` in a refinement event mean objective calls, not heroes or optimizer
iterations: line-search and gradient-check calls also count. `call`/`max_calls` are the same counts;
`elapsed`/`max_seconds` are seconds for the refinement, independent of the CLI event timestamp `t`.
Keep the hero progress bar separate and show e.g. "Wolverine / MUA2 / bind refinement, call 6 of 120,
42 s, best loss 50". Calls can finish early; do not assume the next event reaches `total`.

The refiner emits on call 1, every 3 calls by default, and on termination. The parent relays these
lines immediately while preserving the full engine log. Example:

```json
{"event":"progress","api":1,"t":42.1,"stage":"refine_bind","hero":"wolverine","tier":"mua2","engine":"mua2","script":"refine_bind.py","done":6,"total":120,"call":6,"max_calls":120,"loss":70.0,"best_loss":50.0,"best_call":3,"elapsed":42.0,"max_seconds":180.0,"stop_reason":null}
```

Default limits: 120 objective calls, 180 seconds, or 5 consecutive rising optimization losses
(a relative 1e-6 noise allowance; derivative probes are excluded from the rising-loss test).
The vendor script exposes `--max-calls`, `--max-seconds`, `--rising-calls`, `--progress-every`.
Limits are checked before/after expensive objective calls, so the wall-clock cap can overrun by
one in-flight call; preprocessing is included in elapsed time but is not preempted. `stop_reason`
is null while running, then `divergence`, `wall_clock`, `max_calls`, `nonfinite_loss`,
`nonfinite_gradient`, `stop_flag`, `deadline`, `stock_margin`, or `optimizer_finished`.
The saved candidate uses the lowest finite evaluated loss, excluding derivative probes, even
if the last optimizer trial was worse. A stopped fit is still independently validated; it does
not become a gate pass or owner acceptance. Hard process cancellation cannot save an in-flight fit.

### Accepted output bundles (offline provisioning)

An accepted catalogue entry is checked before ordinary result caches, even with `--rebuild`.
The private bundle cache is `<work>/accepted/<output_sha256>/` (shared across port folders and tool
versions); `LEGENDS_MODELS_ACCEPTED` can point at an additional read-only bundle root supplied by
the coordinator. Each bundle contains `actor.IGB`, `report.json` and `manifest.json` (schema 1).
The actor must match the shipped acceptance catalogue's exact output hash. The report is re-gated on every use. The existing owner decision is honoured for the exact catalogued output hash even if later criteria fail. Events include `owner_accepted: true`, `acceptance_reason`, numeric pass/fail and a reason; report/output hash mismatches are never waived. Fresh fits must pass the current gate.

The manifest binds hero/tier/source/output to content SHA-256 hashes for the selected source actor
(read directly from the owned loose file/archive), port template, animation databases, sampler DLL,
and, for MUA2, both materials/textures archives. Provisioning adds all clip DBs in the conversion
manifest, attachment files in the gate report, and any extra `--port-file` inputs. Whole archive
hashes are memoized only within one build; no cross-run size/mtime shortcut is used for acceptance.
`input_key` is the SHA-256 of the sorted-key JSON input manifest. Options like iteration count
and changes to the tool version do not override a matching accepted look.

Accepted-cache miss reasons:

- `inputs_changed`, `refit: true`, `changed_inputs[]`: input hashes differ, so a refit is allowed.
  Ordinary results are looked up under an additional content-hash key (old stat-only entries cannot
  match); fit once if absent, then reuse/re-gate that numeric output for the same changed inputs.
  The new output still requires its own gate and exact-hash review.
- `accepted_unavailable`, `refit: false`: matching source/template but missing/corrupt bundle or mismatched
  actor/report hash. Refuse this tier with missing-input code 3 and try planned fallbacks; do not refit it.
- `input_unavailable`: owned inputs cannot be read/verified; refuse this tier with code 3.
- `accepted_inputs_match`, `hit: true`, `refit: false`: exact accepted actor reused and re-gated.

The coordinator must populate bundles before release (no new network fetch path). Use the ORIGINAL
accepted input installs/evidence, not later files whose hashes have merely been assumed equivalent:

```text
python -m legends_models.accepted --hero wolverine --tier mua2 --source 0301 --actor <accepted>/14301.IGB --report <accepted>/gate.json --conversion <accepted>/conversion.json --port <original-port> --mua2 <owned-MUA2> --cache <work>/accepted
```

This validates the catalogue output/source/template hashes and report binding, records the numeric result, and then atomically
publishing the bundle manifest. No game assets belong in the source repository. If original input
installs are gone, prove equivalent reference animation/texture/attachment inputs from surviving
evidence before provisioning. `seed()` cannot establish that historical provenance for the operator.

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
XML2 several minutes per fitted hero. MUA2 Wolverine accepted reuse performs hash checks without fitting. Fresh MUA2 fitting still has a separate initial fit, followed by bind refinement bounded to 120 calls / 180 seconds per pass (plus at most one in-flight objective call). Show separate hero and refinement progress; nothing needs a game slot or the desktop.

## Packaging and releases

`tools/freeze.py` (same shape as the Legends Classic builder's `tools/freeze_builder.py`) freezes `legends-models.exe`
with PyInstaller (one folder, console, no UPX; CPython 3.12 x64 + NumPy, SciPy, Pillow; every package pinned in
`requirements-freeze.txt`) and writes `dist/legends-models-<version>-win64.zip` (exe + `_internal\` at the top, for
`tools\legends-models\<version>\`), `dist/legends-models.json` and `dist/SHA256SUMS.txt`. The frozen exe is also
the converters' interpreter: `legends-models.exe -B <script.py> ...` / `-c <code>` run with the bundled libraries
(`__main__.python_shim`), so `--python` is not needed. `--sampler <exe>` bundles the CI-built `sample_enbaya.exe`
(x86; `tools/build_sampler.cmd`); without it `scan` reports `helpers.sampler: null` and the converter tiers that need
clips refuse with exit 3. Community downloads fall back to the GitHub backup release `community-skins-backup-1`
(unmodified copies, kept with the author's permission) when the author's MediaFire link fails; `--mirror` can name
another permitted host without a tool change. That release is not a tool release: its tag is not a version, it is
a pre-release that is never marked latest, so `releases/latest` and the Beta channel never pick it.

The release workflow (`.github/workflows/release.yml`) builds the sampler with MSVC, runs the unit tests, freezes,
smoke-tests the frozen folder and the unpacked zip on synthetic installs (`tools/freeze_smoke.py`), checks the files
(`tools/verify_dist.py`) and, for a `v*` tag, publishes them as a GitHub release of `ChronoRixun/legends-models`:

| Tag | Release | `legends-models.json` `version` / zip | `min_launcher` |
|---|---|---|---|
| `v0.3.0` | normal, latest: `releases/latest/download/legends-models.json` | `0.3.0` / `legends-models-0.3.0-win64.zip` | `0.5.0-0` |
| `v0.3.0-beta.1`, `v0.3.0-rc.1` | pre-release, never latest: `releases/download/v0.3.0-beta.1/legends-models.json` | `0.3.0-beta.1` / `legends-models-0.3.0-beta.1-win64.zip` | `0.5.0-0` (a launcher beta of 0.5.0 qualifies) |

The tag's `X.Y.Z` must equal the tool's version (`legends_models/__init__.py`); the exe reports the full version
(`--version`, the markers' `version`). Manifest fields: `version`, `content_version` (pack format; a change means
rebuild), `zip`, `size`, `sha256`, `min_launcher` (the fields `tool_install::parse_manifest` reads), plus `api` (the
JSON API version, 1) and `sampler_bundled`. `min_launcher` 0.5.0-0 stands for the first launcher release with the
Character models page, 0.5.0, or any beta / release candidate of it, for a stable tag as for a pre-release: the
launcher's Beta channel also picks a stable tool release, and a 0.5.0 beta or rc launcher runs it as it ran the tool's
betas. It is set in `tools/freeze.py` (`MIN_LAUNCHER`, `default_min_launcher`) or with `--min-launcher`.

## What changed for the launcher in 0.3.0 (the launcher's fake tool should emit these)

1. `scan`: per tier `author` and `costume_info[]` (above); the page shows `label` and `credit`/`author` per costume
   and may show `preview` as an image.
2. `download` events: `sha256`, `character`, `costume`.
3. `--offline` exists (global flag): pass it when the launcher runs with -offline instead of only greying Apply.
4. Folder names: `Upgraded-<Tier>-<Hero>` for the Upgraded group (Community/XML2/Sharpened unchanged); one-time
   `stage` events `renamed <old> to <new> (group-qualified folder names)` on the first 0.3.0 build.
5. `verify`: `stale`, `stale_folders[]`, `port_changed[]`, `port_build_unknown[]`, exit 31 = offer Rebuild.
6. Release: `legends-models.json` manifest next to the zip; the exe needs no Python.
7. (beta 3) Community downloads: the author's link first, then the GitHub backup. `download` events gain
   `source_label` ("author's link" / "GitHub backup") and, for a backup download, `author_error`; `source` keeps its
   values (`author`/`mirror`), so a launcher that maps `source` keeps working. `--no-mirror` (`build`, `fetch`) for a
   player who wants the author's links only.

### Cooperative cancellation (0.4.0 and newer)

Set `LEGENDS_MODELS_CANCEL` to a unique initially absent request-file path. Creating that file requests cancellation. During conversion the CLI polls at most every 0.2 s, kills and reaps its engine, and exits 130. It also checks before a build, between characters and tier attempts, after a converter returns (including failures), and before publication. Once publication starts it finishes the complete group and load-order update and returns its actual result; keep the game-folder reservation until process exit. The launcher retains its existing compatibility policy; API 1, arguments and exit codes are unchanged. Published 0.3.0 lacks cooperative cancellation. Closing the launcher or forcibly killing a publishing process is outside this cooperative guarantee.

Build diagnostics can lower `--refine-max-calls` / `--refine-max-seconds`; normal recipes keep 120 calls / 180 seconds per bind pass. Acceptance reuse still takes precedence over these options.
