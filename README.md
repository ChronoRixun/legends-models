# legends-models

**Optional character-model mods for [Legends Classic](https://github.com/ChronoRixun/legends-classic), built on
your PC from games you own.**

Legends Classic is the free fan port of X-Men Legends (2004) onto the X-Men Legends II PC engine. Its heroes use the
first game's models. `legends-models` can give them newer looks:

- **Community pack** (the suggested choice): BaconWizard17's XML2-style skins for the X-Men Legends heroes, made by
  the author for the community and credited to him. Nothing of his is in this repository: each player downloads the
  skins from his own marvelmods.com links (his posted terms allow use in projects with credit; his readme asks that
  the files are not shared outside marvelmods.com or sold), and they are fitted on that player's PC. Needs no other game.
- **Upgraded**: per hero, the best model that passes the checks below, from Marvel: Ultimate Alliance 2, then
  Marvel: Ultimate Alliance, then the Community pack - if you own those games and have them installed.
- **XML2 look** (X-Men Legends II's own models) and **Sharpened classic** (the port's own textures upscaled, mesh
  unchanged) as fallbacks.

It is a converter, not a download of models: every model file is made on your computer from your own installs, or
downloaded from the community author's own links. It writes an ordinary mod folder for the XML2 Fix mod loader;
remove the folder (or switch it off in the launcher's Mods tab) and the stock models are back.

> **Status: first public pre-release, 0.3.0-beta.1.** The Ultimate Legends launcher's Character models page drives
> it; until a launcher release ships that page, the tool is for testers and command-line use.

## What this project ships, and what it never ships

- **Code and format facts only.** No game file, no converted model, no texture and no community skin is in this
  repository or in any release, and none ever will be. CI refuses game content on every push
  (`tools/check_no_game_content.py`), and the release build checks its own bundle the same way.
- **Bring your own copy.** The tool reads your own installs (Legends Classic, and optionally Marvel: Ultimate
  Alliance and Marvel: Ultimate Alliance 2) **read-only**, converts on your PC, and writes only under
  `<Legends Classic>\mods\`. Nothing is uploaded anywhere.
- **Community skins** are downloaded on your PC from the author's own links, checked against a SHA-256 manifest and
  a file allow-list, and credited in each pack's `CREDITS.md`. This project never bundles, mirrors or re-hosts them.
- Generated packs say so in their `readme.txt`: they are for your own use; do not share them.
- This is an unofficial, non-commercial fan project. It is not affiliated with or endorsed by the owners of the
  games or the characters, or by the community authors whose skins it can download. You need your own legal copies
  of the games.

## Getting it

- **With the Ultimate Legends launcher** (recommended, from its next release): the X-Men Legends page gets a
  Character models section that runs this tool for you (Off / Community pack / Upgraded / More). Every release
  carries the manifest `legends-models.json` the launcher reads (see [LAUNCHER-API.md](LAUNCHER-API.md)).
- **Standalone**: download `legends-models-<version>-win64.zip` from the
  [releases page](https://github.com/ChronoRixun/legends-models/releases), unzip it anywhere and run
  `legends-models.exe --help` from a terminal. It needs no Python. `SHA256SUMS.txt` lists the hashes.
- **From source**: Python 3.11+ (`pip install .[convert]` for NumPy, SciPy and Pillow), plus the animation sampler
  (below).

What you need besides it:

| | |
|---|---|
| **Legends Classic** | a port build made by its builder. The tool reads the port's actors and `libIGSg.dll`. |
| **Windows 10 or 11, 64-bit** | the release is a Windows program. |
| **Marvel: Ultimate Alliance** (optional) | the 2006 release (loose `actors\*.igb`) or the 2016 release (`actors.bin`), for the Upgraded tier. |
| **Marvel: Ultimate Alliance 2** (optional) | the PC release (`actors.bin`, `materials.bin`, `textures.bin`), for the Upgraded tier. |
| **Internet** (Community pack only) | once per costume, for the author's archive (1-2 MB each). `--offline` never downloads. |

## Credits and terms

- **Community skins, heads and portraits: BaconWizard17** ([his release thread on
  marvelmods.com](https://marvelmods.com/forum/index.php/topic,10629.0.html)), with the co-credits he lists per
  costume. Use with credit is permitted as stated by the author; his readmes ask that his files are not shared
  outside marvelmods.com, so this project never hosts them: they come from his own links on your PC. The full
  credits are in [`legends_models/data/community-credits.md`](legends_models/data/community-credits.md) and are
  copied into every pack that uses them.
- The games and characters belong to their owners. This project contains none of their files.
- The code is MIT licensed (see [`LICENSE`](LICENSE)).

## The pack

Default layout (`--layout per-hero`, one folder per converted hero):

```
<Legends Classic>\mods\Upgraded-MUA2-Wolverine\   one folder per converted hero: [<Group>-]<Tier>-<Hero>
    Actors\14301.IGB          the port's stock skin ID
    mod.json                  name, description, character, skin (source costume), output_sha256, tier
    readme.txt                how to turn it off
    CREDITS.md                sources and credits for this hero
    legends-models.json       ownership marker, per-file SHA-256, provenance + gate, group (= --pack name),
                              port_build (SHA-256 of the port files it depends on: verify reports stale)
<Legends Classic>\mods\Upgraded-MUA-Storm\ ...  Community-Beast\ ... (Models-Community)
<Legends Classic>\mods\load-order.txt   gets "+Upgraded-MUA2-Wolverine", "+Upgraded-MUA-Storm", ...
```

Tier prefixes: `MUA2-`, `MUA-` (MUA1), `XML2-`, `Community-`, `Sharpened-`. A build's folders form a group named by
`--pack` (default per mode, e.g. `Models-Upgraded`); `verify`/`remove --pack Models-Upgraded` act on the whole group,
or name one folder (`--pack Upgraded-MUA-Storm`). Folders carry the group name unless the group is that tier's own
(`Community-Beast` in Models-Community, `Upgraded-Community-Beast` in Models-Upgraded), so groups never share a
folder; 0.2.x installs are renamed on the next build. `--layout pack` instead writes one folder for the whole
appearance (`mods\Models-Upgraded\Actors\...`, one load-order line).

Heads and portraits travel with the hero folder (unless `--no-heads`): `HUD\hud_head_<skin>.IGB` (conversation
portrait), `UI\hud\characters\<skin>.IGB` (the 3D head of the health panel) and
`UI\models\characters\<skin minus 2 digits>01.IGB` (character select portrait). Community heroes get the author's
three files (the 3D head's in-file names renamed to the port's ID, as the builder does); MUA1 heroes get MUA1's
conversation and select portraits swapped into the port's own scenes (texture only, mesh/UVs byte-identical). MUA2,
XML2 and Sharpened keep the port's heads. Alternate-costume heads (`hud_head_14302`, ...) are never touched.
`build --heads-only` writes only these files (no actor conversion, no sampler needed).

Only the skin mesh, its bind and its textures change. The port's skeleton, animations, bounds and gameplay data stay
byte-identical (co-op sync depends on that). Remove the folder, or disable the line, and restart: the stock models
come back.

## Modes (tiers)

| `--mode` | Per character | Needs |
|---|---|---|
| `community` (default) | BaconWizard17 pack | nothing else installed (downloads on first build) |
| `upgraded` | best of **MUA2 > MUA1 > Community** that passes the gate | MUA2 and/or MUA1 set up, else falls to Community |
| `xml2` | X-Men Legends II's own model of that hero | nothing (XML2 actors are already in the port) |
| `sharpen` | the port's own textures upscaled 4x (Lanczos + unsharp), mesh unchanged | nothing |

A hero whose tiers are all unavailable or fail the gate **stays stock** (no file written for it).

## The gate

Before a character is written, its converter's validation report is re-checked by this tool
(`legends_models/gate.py`), bound to the exact output bytes: over the six reference clips (idle, run, attack_light1,
f_heavy_attack_4, power_2, pain_high), body vs stock body and outline vs stock outline, max edge stretch and max
bone-chain distance must not exceed stock's, coincident vertices must never separate, hand/head/Motion matrices must
be unchanged, attachments keep hand-local coordinates, idle feet within 2% of stock height, and the
skeleton/palette/bounds unchanged; for the MUA1/MUA2 tiers also **zero layer crossings**: every inner cloth shell
(coat lining, cowl over scalp) stays behind its outer shell in the converted bind and in every frame. A character
that fails is refused (the next tier is tried in `upgraded`).

Two fitter rules (`engines/vendor/_shared/layers.py`, used by the MUA1/MUA2 converters):
- **layered cloth**: inner shells are found on the source bind (closest outer triangle within 1.25 units along its
  normal, same garment = overlapping source skinning) and coupled to their outer triangle: bound with its blend,
  weights tied to its interpolated weights while fitting, bind corrections shared, and any residual crossing closed
  by one shared influence for that patch;
- **long hair / loose geometry**: geometry hanging past its leaf bone (Jean's hair below Ponytail11) blends, with a
  smooth falloff along the strand, into the body bone it rests on, and the fitter's chain objective keeps every
  frame within the stock chain distance. The gate itself is unchanged.

The visual half of the gate (renders and in-game close-ups) cannot be automated; owner-reviewed outputs are
recorded by exact SHA-256 in `legends_models/data/acceptance.json` and reported as `owner_accepted` per character.

## Command line

```powershell
legends-models scan  --port "C:\Legends-classic"
legends-models plan  --port "C:\Legends-classic" --mode upgraded
legends-models build --port "C:\Legends-classic" --mode upgraded --costume wolverine=mua1:0301
legends-models verify --port "C:\Legends-classic" --pack Models-Upgraded
legends-models remove --port "C:\Legends-classic" --pack Models-Upgraded
```

- `--mua1 / --mua2` point at the installs if Steam's library list does not find them. MUA1 may be the 2006 release
  (loose `actors\*.igb`) or the 2016 release (`actors.bin`).
- `--hero NAME` (repeatable) limits a build; other characters already in the pack are kept.
- `--costume hero=ID` or `hero=tier:ID` picks a costume (MUA1 Wolverine: `0303` classic yellow/blue is the default,
  `0301` white shirt/jeans, `0302` brown/yellow; community `Original`/`Ultimate XML1`/... per hero; `scan --json`
  lists them).
- Community archives: the author's own links are always the fallback. `--mirror URL` (or `LEGENDS_MODELS_MIRROR`, or
  the manifest's `mirror` entry) names an https base URL that is tried first; the archive file name is appended.
  Size and SHA-256 are checked against the built-in manifest either way, so a wrong or tampered mirror file is
  refused and the author's link is used. Only point it at a mirror whose host has the author's OK.
- `--offline` never downloads: community costumes that are not in the download cache stay unavailable.
- The roster has the 15 playable heroes plus 5 villains/bosses (Magneto, Juggernaut, Sabretooth, Toad, Pyro); the
  villains only have the community and sharpened tiers.
- `--materials spec|bake|spec+bake` (MUA1 tier, optional, off by default) also uses the MUA skin's surface maps,
  after the gate: `spec` wraps the body in XML2's own gloss-masked environment shader (igEnvironmentMapShader2; the
  MUA `_s` map is the gloss map, a generated studio cube the light), `bake` folds the MUA `_n` normal map's detail
  shading into the diffuse (XML2 has no normal-map path a skinned actor can use). The result is used only if
  `materials.material_invariant` proves mesh, weights, skeleton, bind and bounds byte-identical to the gated file;
  its hash is new, so it is not `owner_accepted` until reviewed. The specular stage is added only for heroes played
  in game with it (`materials.SPEC_VERIFIED_HEROES`: Wolverine, Storm, Iceman, Colossus, Psylocke); others get the
  bake only, with a warning.
- `--no-heads` leaves the heads/portraits out of the folders; `--heads-only` builds only them.
- `--strict` builds only owner-accepted costumes. `--skip-tier` leaves a tier out of the order.
- `--json` prints one JSON object per line for the launcher (see [LAUNCHER-API.md](LAUNCHER-API.md)).
- `--work` is the private cache (default `%LOCALAPPDATA%\legends-models`): extracted source models, animation
  caches, engine logs and finished conversions (reused when nothing changed; `--rebuild` forces a fresh
  conversion). One sub-folder per port install, reset when that install changes. It holds derived game data: keep
  it private.

The converter tiers need the offline animation sampler `sample_enbaya.exe` (bundled in the release; from source,
built from `tools/sample_enbaya.cpp` with `tools\build_sampler.cmd`, Visual Studio C++ build tools, x86). The
sampler loads the port's own `libIGSg.dll` to decode animation clips for the gate; it never starts a game.
Community archives need UnRAR, 7-Zip or Windows' own `tar.exe`.

## Development

- `legends_models/`: CLI, planner, gate, packs, load-order editor, community fetcher, readers (`formats/`: IGB v6/v8,
  IGZ v6, MUA PC `.bin`).
- `legends_models/engines/vendor/`: the research converters, verbatim except for the research-PC paths listed in
  `PATCHES.json` and the layered-cloth / loose-geometry rules (`_shared/layers.py`, wired into the MUA1/MUA2
  fitters, refine_bind and validators); each runs in its own process from a private copy under `--work`.
- `legends_models/data/`: facts and manifests only: community archive hashes + allow-list, credits, owner acceptance
  records.
- `tests/`: unit tests over synthetic data, no game needed: `python -m unittest discover -s tests -t tests`.
- `tools/check_no_game_content.py`: the content guard (the same one as Legends Classic's). Run
  `pip install pre-commit && pre-commit install` once, or `python tools/check_no_game_content.py --staged` before a
  commit. Put your own name, host name or drive layout as regular expressions in `.git/info/content-guard-deny`
  (local, never committed).
- Release build (`.github/workflows/release.yml`, the same steps by hand on Windows with CPython 3.12 x64):

  ```powershell
  python -m pip install -r requirements-freeze.txt     # every package pinned
  python -m unittest discover -s tests -t tests
  tools\build_sampler.cmd                              # -> tools\sample_enbaya.exe (MSVC x86)
  python tools/freeze.py --sampler tools/sample_enbaya.exe --smoke
  python tools/verify_dist.py dist --sampler
  ```

  `freeze.py` writes `dist/legends-models/` (exe + `_internal\`), `dist/legends-models-<version>-win64.zip`,
  `dist/legends-models.json` and `dist/SHA256SUMS.txt`; `freeze_smoke.py` runs the frozen exe on synthetic
  installs (scan, plan, a full sharpen build + verify + remove, offline refusals, the converters' interpreter).
  A tag `vX.Y.Z` publishes a release; `vX.Y.Z-beta.N` / `-rc.N` a pre-release (never "latest"), whose version
  (`0.3.0-beta.1`) the exe, zip and manifest carry. See [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT (see [`LICENSE`](LICENSE)). Credits for community skins are in
[`legends_models/data/community-credits.md`](legends_models/data/community-credits.md) and are copied into every
pack that uses them.
