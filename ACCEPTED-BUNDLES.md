# Offline private accepted bundles

The public tool contains code and hash receipts only. It does not download or
include game-derived accepted actors, reports or input manifests. A maintainer
with the reviewed private store can provision `<shared-work>/accepted/<hash>/`
using `tools/provision_accepted.py`; players still need matching owned inputs.
Use the root passed by the active launcher as `--work`, never `ports/<id>` or the
tool-version directory. Close model operations before provisioning. Tool update
alone does not replace installed models; Build or Rebuild publishes them.

```powershell
python tools/provision_accepted.py --bundle "$PrivateBundle" --work "$SharedWork" --output-sha256 $OutputHash --check-only
python tools/provision_accepted.py --bundle "$PrivateBundle" --work "$SharedWork" --output-sha256 $OutputHash
```

For installed Ultimate Legends, the shared root is normally
`%LOCALAPPDATA%/ultimate-legends/cache/legends-models`; portable launchers use
`<portable-root>/cache/legends-models`. Confirm the actual root first.

## Approved outputs

Owen approved Jean 12301, arm-repaired Jean 12302 and original Iceman 0801 on
2026-10-27. This supersedes the old preparation brief's pending 12301 status.
Exact output hashes, source/template identities and owner evidence are in
`legends_models/data/acceptance.json`. The three-file integrity receipts are in
`tools/accepted-bundles.json`. No new model receives approval from these checks.

| Hero / source | Exact actor SHA-256 | Bundle |
| --- | --- | --- |
| Wolverine MUA2 0301 | d066435e4cc3d5c38ab9de7be57d6d83e68ddbc65e6051db2f21dfe8a5ca7472 | Existing reviewed private bundle |
| Iceman MUA2 0801 | 8a862f855c20e47448449face84f38e206d01be373e163a0e90fc3003b71ed4d | Verified reconstruction with current port bindings |
| Jean MUA2 12301 | d2628a6ef1d8d26abf2b4671e0e233d5065082a8d8d1d053392a7b913ad69bd3 | Existing exact reproduction bundle |
| Jean MUA2 12302 | a2c7e18985f1eb6fd9774b662c091aeb1bdff1e5fb7f9a61c0c34fa15d0ca905 | Verified reconstruction of the approved output |

Jean defaults to arm-repaired 12302; `--costume phoenix=mua2:12301` selects her
other approved look. Upgraded retains MUA2 > MUA1 > Community. Iceman and Jean
MUA2 entries reuse approved bundles only: no new generic conversion recipe is
claimed. Scan/plan offer these reuse-only choices only when a complete local
bundle verifies against the acceptance catalogue. They validate the three-file
layout, manifest bindings, exact actor and hash-bound report without hashing large
owned archives during a page refresh. Build still rehashes every owned input.
Missing/damaged bundles select the same fallback that Build writes, avoiding a
repeated launcher Update prompt. With no explicit costume choice, the preferred
12302 is used when available, otherwise a verified 12301 may be used. An explicit
unavailable choice keeps normal fallback behavior; its costume is never silently
changed. Existing approved costumes (including Wolverine 0301) also check the small
owned source actor and stock-template hashes before scan/plan promise accepted
reuse. An original matching source without its verified bundle plans the lower
tier that Build will use; changed source/template inputs keep the existing
fresh-fit path for supported recipes. Matching complete bundles still reuse the
exact approved bytes, including Rebuild. Build remains the authority for all
other input hashes and fresh-fit numeric gates.
The public tool cannot reproduce these approved outputs from clean owned inputs
alone in this release: the verified reconstruction uses private surviving final
fit data, which is not distributable. Without a provisioned matching MUA2 bundle,
Jean falls back to Community. Her MUA1 12301 fit has documented arm/glove distortion
and is disabled in Upgraded until repaired. A failed/offline Community build leaves
the previous install intact; disable an existing MUA1 Jean folder until a safe
replacement build succeeds. The MUA1 actor is a different model and has no
owner-accepted hash here.

The recovered 12302 and updated Iceman manifests bind a verified reconstruction:
serialization of the surviving final fit with verified source/template and an
atlas resolved from owned material/texture archives produced the exact approved
IGB bytes; all six fresh native animation sample arrays equal the historical
validation arrays. This is not a new fit or proof of unused historical archive
bytes. Iceman's current common animation database differs as a whole file from
the old reproduction snapshot; sampled inputs and resulting actor remain exact.
The private reconstruction records document these checks and input identities.

## Integrity and numeric disclosure

Each bundle contains exactly `actor.IGB`, `manifest.json`, `report.json`.
Provisioning pins every file, validates catalogue identity, all required input
bindings and the report's output hash, copies into a temporary sibling,
reverifies, then renames the complete directory. Repeat staging is idempotent;
corrupt existing destinations refuse and remain untouched. Links/junctions,
missing/extra files and incomplete provenance refuse. Check-only writes nothing.
Runtime rehashes actual owned inputs before reuse, even on Rebuild; changed
inputs never inherit acceptance. Exact owner approval and current numeric results
are reported separately. Wolverine's older report lacks a later layer check;
Iceman's approval predates the unshipped regional arm criteria. Their approval
is for the listed bytes, not a claim that every later metric passes. Fresh fits
still require all current shipped gates. Regional-arm engine work is outside this
release. Pending-review opt-in remains supported and tested for future private
reviews but never grants runtime acceptance.

## Public packaging guard

`freeze.py` checks the frozen folder and temporary ZIP before replacing a public
artifact; `verify_dist.py` independently checks the distribution. The guard scans
every member for private paths, actor suffix/signatures, pinned private file
hashes (even renamed) and accepted-manifest structure. Hash metadata may ship;
private game-derived bundle files may not. Keep the private store outside source
and public distribution folders. Run this against any launcher ZIP too:

```powershell
python tools/private_bundle_guard.py "$ToolZip" "$LauncherZip"
python tools/verify_dist.py "$ToolDist" --sampler
```

Exit 1 blocks release. Publish only the explicit tool ZIP, release manifest and
checksum file. MUA1 Wolverine remains catalogued but has no complete provisioning
receipt in this release; do not manufacture one from an unrelated install.
