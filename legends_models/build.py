# SPDX-License-Identifier: MIT
"""build / remove orchestration."""
from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path

from . import __version__, community, gate, heads as headsmod, packs, plan as planmod, portbuild, results, roster
from .engines.tiers import ADAPTERS, Candidate, Context
from .errors import DOWNLOAD, MISSING_INPUT, NOTHING_WRITTEN, OK, PARTIAL, ToolError

GAMES = {'mua1': "Marvel: Ultimate Alliance (your own copy)", 'mua2': "Marvel: Ultimate Alliance 2 (your own copy)",
         'xml2': "X-Men Legends II (actors already in your Legends Classic build)",
         'sharpen': "Legends Classic's own X-Men Legends textures (your own disc)",
         'community': "BaconWizard17's community skins (downloaded on this PC from the author's links)"}


def acceptance_records():
    data = json.loads(resources.files('legends_models.data').joinpath('acceptance.json').read_text(encoding='utf-8'))
    return {r['output_sha256']: r for r in data['records']}


def credits(characters):
    lines = ['# Credits', '',
             'This pack was generated on this PC by legends-models (MIT) from games and files you own.',
             'It contains no files from the legends-models project. Do not share or upload it.', '']
    tiers = sorted({c['tier'] for c in characters.values()})
    for t in tiers:
        names = ', '.join(c['name'] for c in characters.values() if c['tier'] == t)
        lines += [f'## {roster.TIER_NAMES[t]}', '', f'Source: {GAMES[t]}.', f'Characters: {names}.', '']
    if 'community' in tiers:
        text = community.credits_text()
        sections = re.split(r'(?m)^## ', text)
        head = sections[0].strip().splitlines()
        lines += ['## Community skins', ''] + [x for x in head[1:] if x.strip() and 'local-only' not in x] + ['']
        wanted = {community.KEY_TO_NAME[k] for k, c in characters.items() if c['tier'] == 'community'}
        for s in sections[1:]:
            title = s.splitlines()[0].strip()
            if title in wanted:
                lines += ['### ' + s.strip(), '']
    lines += ['## Converter', '',
              'Model conversion, fitting and validation code: the Legends Classic character-model research tools,',
              'packaged as legends-models. Original games by their respective developers and publishers.', '']
    return '\n'.join(lines)


TIER_PREFIX = {'mua1': 'MUA', 'mua2': 'MUA2', 'xml2': 'XML2', 'community': 'Community', 'sharpen': 'Sharpened'}


# A group whose own tier this is keeps the plain mua-models2 name (Community-Beast in Models-Community); every
# other combination is prefixed with the group, so two groups never share a folder (Upgraded-Community-Beast).
OWN_TIER = {'Models-Community': 'community', 'Models-XML2-Look': 'xml2', 'Models-Sharpened': 'sharpen'}


def group_prefix(group):
    g = group[len('Models-'):] if group.casefold().startswith('models-') else group
    return g or group


def hero_folder(key, tier, group=None):
    """Per-hero folder name, as mua-models2's production output (MUA-Wolverine, MUA-Jean, ...), qualified by the
    group unless the group is that tier's own (Models-Upgraded -> Upgraded-MUA-Wolverine, Upgraded-Community-Beast)."""
    name = roster.MUA1_NAMES.get(key, key) if tier == 'mua1' else key
    base = f'{TIER_PREFIX[tier]}-{name.title()}'
    if group is None or OWN_TIER.get(group) == tier:
        return base
    return f'{group_prefix(group)}-{base}'


def migrate_folder_names(mods):
    """Installs made before group-qualified names (0.2.x) used <Tier>-<Hero> in every group, so Models-Community and
    Models-Upgraded could write the same folder. Rename every owned per-hero folder to its group's name (load-order
    line renamed in place, sign kept; mod files and their hashes untouched). Returns [(old, new)]."""
    from . import loadorder
    moved = []
    for f in packs.owned_packs(mods):
        m = packs.read_marker(mods / f) or {}
        group, chars = m.get('group'), m.get('characters') or {}
        if not group or group.casefold() == f.casefold() or len(chars) != 1:
            continue                                    # a --layout pack folder, or not a per-hero folder
        key, info = next(iter(chars.items()))
        if info.get('tier') not in TIER_PREFIX or key not in roster.BY_KEY:
            continue
        want = hero_folder(key, info['tier'], group)
        if want == f or (mods / want).exists():
            continue
        os.replace(mods / f, mods / want)
        m['pack'] = want
        (mods / want / packs.MARKER).write_text(json.dumps(m, indent=1), encoding='utf-8')
        loadorder.write(mods, loadorder.rename_entry(loadorder.read(mods), f, want))
        moved.append((f, want))
    return moved


def mod_json(name, key, info):
    """mod.json in the mua-models2 production shape (name, description, character, skin, output_sha256)."""
    return json.dumps(dict(name=name, description=f'Local {roster.TIER_NAMES[info["tier"]]} {info["name"]} {info["source"]}; '
                           f'{info["gate"]}; owned assets only', character=key, skin=info['source'], tier=info['tier'],
                           port_skin=info['skin'], output_sha256=info['sha256'], owner_accepted=info.get('owner_accepted', False),
                           generator='legends-models'), indent=2)


def readme(pack, mode):
    return (f'{pack}: optional character models for Legends Classic ({mode}).\n'
            'Generated on this PC from games you own. Do not distribute.\n'
            'Turn it off in the launcher (or change "+' + pack + '" to "-' + pack + '" in mods\\load-order.txt),\n'
            'or delete this folder, then restart the game: the stock models come back.\n')


def _apply_materials(ctx, hero, choice, cand, result, report):
    """Optional MUA material steps on a gated MUA1 candidate (see legends_models.materials). The gate result stays
    the converter's; the material output is used only when its invariant proves mesh, weights, skeleton, bind and
    bounds byte-identical to the gated file. Any problem keeps the plain gated candidate."""
    from . import materials
    from .engines.tiers import mua1_source
    from .formats.igb import IgbFile
    src = mua1_source(ctx, hero, choice)
    if src is None:
        report.warn(f'{hero.name}: materials skipped (MUA source skin not found)', hero=hero.key)
        return cand, result, {'skipped': 'MUA source skin not found'}
    steps = tuple(s for s in ctx.materials if s != 'spec' or hero.key in materials.SPEC_VERIFIED_HEROES)
    if steps != tuple(ctx.materials):
        report.warn(f'{hero.name}: specular stage not verified in game for this hero; bake only', hero=hero.key)
    if not steps:
        return cand, result, {'skipped': 'specular stage not verified for this hero'}
    try:
        data, summary = materials.apply(Path(cand.path).read_bytes(), IgbFile(Path(src).read_bytes()), steps)
    except Exception as e:                      # a material step never costs the player the converted model
        report.warn(f'{hero.name}: materials skipped ({e})', hero=hero.key)
        return cand, result, {'skipped': str(e)}
    if not summary['invariant']['pass'] or not summary['steps']:
        return cand, result, {'skipped': 'invariant failed' if summary['steps'] else 'no maps', **summary}
    out = Path(cand.path).with_name(Path(cand.path).stem + '-materials' + Path(cand.path).suffix)
    out.write_bytes(data)
    result = dict(result, gated_sha256=result['candidate_sha256'], candidate_sha256=gate.sha256(out))
    cand.path = out
    cand.notes = list(cand.notes) + [f'material steps: {", ".join(summary["steps"])}']
    report.emit('materials', hero=hero.key, steps=summary['steps'])
    return cand, result, summary


def add_heads(info, hero, choice, ctx, report):
    """Conversation head, HUD head and select portrait of the chosen source, installed beside the actor.
    A head problem never refuses the hero: that role just stays stock (and the notes say why)."""
    routes, notes = headsmod.collect(hero, choice, ctx)
    info['head_files'] = {rel: str(src) for rel, src in routes.items()}
    info['heads'] = {rel: packs.sha256(src) for rel, src in routes.items()}
    info['head_notes'] = notes
    for n in notes:
        report.stage('convert', f'{hero.name}: {n}', hero=hero.key, tier=choice.tier, source=choice.source)


def heads_entry(hero, choice, ctx, report):
    """--heads-only: no actor conversion; the folder carries only the head files."""
    info = dict(name=hero.name, skin=hero.skin, tier=choice.tier, source=choice.source, file=None, sha256=None,
                gate='heads only (no actor in this folder)', owner_accepted=False, acceptance=None, notes=[],
                fallback_from=None, path=None)
    add_heads(info, hero, choice, ctx, report)
    return info


def run_build(scan_result, rows, ctx, game_dir, pack, mode, report, keep_existing=False, enable=True, rebuild=False,
              layout='pack'):
    mods = packs.check_output_location(game_dir, [ctx.mua1, ctx.mua2])
    migrated = migrate_folder_names(mods) if mods.is_dir() else []
    for old, new in migrated:
        report.stage('publish', f'renamed {old} to {new} (group-qualified folder names)')
    accepted = acceptance_records()
    produced, refused = {}, {}
    todo = [r for r in rows if r.chosen]
    for i, row in enumerate(todo):
        hero = roster.BY_KEY[row.hero]
        report.progress(i, len(todo), f'{hero.name}: {row.chosen.tier}', hero=hero.key)
        attempts = []
        if getattr(ctx, 'heads_only', False):
            entry = heads_entry(hero, row.chosen, ctx, report)
            if entry['heads']:
                produced[hero.key] = entry
            else:
                refused[hero.key] = dict(name=hero.name, attempts=[dict(tier=row.chosen.tier, source=row.chosen.source,
                                                                        error='no head files: ' + '; '.join(entry['head_notes']))])
            continue
        for choice in [row.chosen] + row.fallbacks:
            report.stage('convert', f'{hero.name} from {roster.TIER_NAMES[choice.tier]} ({choice.source})',
                         hero=hero.key, tier=choice.tier, source=choice.source)
            try:
                k = results.key(hero, choice, ctx)
                hit = None if rebuild else results.load(ctx, k)
                if hit:
                    path, result, notes, extras = hit
                    cand = Candidate(path, None, 'cached', None, notes + ['cached result (same inputs)'], extra_files=extras)
                    report.emit('cache', hero=hero.key, tier=choice.tier, hit=True)
                else:
                    cand = ADAPTERS[choice.tier](hero, choice, ctx)
                    result = cand.gate if cand.kind == 'texture-only' else gate.check_report(
                        cand.report, cand.path, require_layers=choice.tier in ('mua1', 'mua2'))
                    if cand.kind == 'texture-only':
                        result['candidate_sha256'] = gate.sha256(cand.path)
                    if cand.path and Path(cand.path).is_file():
                        cand.path = results.store(ctx, k, cand, result, require_layers=choice.tier in ('mua1', 'mua2'))
            except ToolError as e:
                attempts.append(dict(tier=choice.tier, source=choice.source, error=str(e).splitlines()[0], code=e.code))
                report.warn(f'{hero.name}: {choice.tier} could not run: {str(e).splitlines()[0]}', hero=hero.key)
                continue
            if not result['numeric_pass']:
                attempts.append(dict(tier=choice.tier, source=choice.source, gate_failures=result['failures'][:8],
                                     notes=cand.notes))
                report.emit('gate', hero=hero.key, tier=choice.tier, passed=False, failures=result['failures'][:8])
                continue
            materials_info = None
            if ctx.materials and choice.tier == 'mua1':
                cand, result, materials_info = _apply_materials(ctx, hero, choice, cand, result, report)
            rec = accepted.get(result['candidate_sha256'])
            produced[hero.key] = dict(name=hero.name, skin=hero.skin, tier=choice.tier, source=choice.source,
                                      file=f'Actors/{hero.skin}.IGB', sha256=result['candidate_sha256'],
                                      gate='texture-only invariants (mesh/skeleton/bind/bounds byte-identical)'
                                      if choice.tier == 'sharpen' else 'numeric pass (stock-relative, six clips)',
                                      owner_accepted=bool(rec), acceptance=rec, notes=cand.notes,
                                      fallback_from=attempts or None, path=str(cand.path), materials=materials_info,
                                      extras={rel: str(f) for rel, f in cand.extra_files.items()})
            report.emit('gate', hero=hero.key, tier=choice.tier, passed=True, owner_accepted=bool(rec))
            if getattr(ctx, 'heads', True):
                add_heads(produced[hero.key], hero, choice, ctx, report)
            break
        else:
            refused[hero.key] = dict(name=hero.name, attempts=attempts)
    report.progress(len(todo), len(todo), 'conversions done')
    stock = {r.hero: r.as_dict()['reason'] for r in rows if not r.chosen}
    if not produced:
        codes = {a.get('code') for r in refused.values() for a in r['attempts']}
        code = DOWNLOAD if codes == {DOWNLOAD} else MISSING_INPUT if codes == {MISSING_INPUT} else NOTHING_WRITTEN
        return code, dict(pack=pack, written=False, characters={}, refused=refused, stock=stock,
                                     message='every requested character was refused; previous pack left untouched')
    if layout == 'per-hero':
        return publish_per_hero(mods, pack, mode, produced, refused, stock, ctx, keep_existing, enable)
    carried = {}
    old = packs.read_marker(mods / pack)
    if keep_existing and old:
        for key, info in old.get('characters', {}).items():
            if key not in produced and (mods / pack / info['file']).is_file() and \
                    packs.sha256(mods / pack / info['file']) == info['sha256']:
                carried[key] = dict(info, path=str(mods / pack / info['file']), carried_over=True)
    report.stage('publish', f'writing mods\\{pack}')
    with packs.Staging(mods, pack) as st:
        everything = {**carried, **produced}
        for key, info in everything.items():
            if info.get('file') and info.get('path'):
                st.add(info['file'], info['path'])
            for rel, src in {**(info.get('extras') or {}), **(info.get('head_files') or {})}.items():
                st.add(rel, src)
        for info in everything.values():
            info.pop('path', None)
            info.pop('extras', None)
            info.pop('head_files', None)
        st.write_text('CREDITS.md', credits(everything))
        st.write_text('readme.txt', readme(pack, mode))
        st.write_text('mod.json', json.dumps(dict(name=pack, description=f'Local character models ({mode}); owned assets only',
                                                  characters={k: dict(skin=v['skin'], tier=v['tier'], source=v['source'],
                                                                      output_sha256=v['sha256']) for k, v in everything.items()},
                                                  generator='legends-models'), indent=2))
        port_build = portbuild.record(ctx.port, packs.file_hashes(st.path), everything)
        marker = st.publish(mode, everything, extra=dict(refused=refused, stock=stock, port=str(ctx.port),
                                                         port_build=port_build, tool_version=__version__), enable=enable)
    code = PARTIAL if refused else OK
    return code, dict(pack=pack, layout='pack', written=True, folder=str(mods / pack), enabled=enable,
                      characters=marker['characters'], refused=refused, stock=stock, files=len(marker['files']))


def publish_per_hero(mods, group, mode, produced, refused, stock, ctx, keep_existing, enable):
    """mua-models2 production layout: one folder per hero (MUA-Wolverine/Actors/14301.IGB, mod.json,
    readme.txt) plus CREDITS.md and the legends-models marker (group = the --pack name)."""
    from . import loadorder
    existing = {f: packs.read_marker(mods / f) for f in packs.members(mods, group) if f.casefold() != group.casefold()}
    folders, characters = [], {}
    for key, info in produced.items():
        name = hero_folder(key, info['tier'], group)
        packs.check_same_group(mods, name, group)
        with packs.Staging(mods, name) as st:
            if info.get('file') and info.get('path'):
                st.add(info['file'], info.pop('path'))
            info.pop('path', None)
            for rel, src in {**(info.pop('extras', None) or {}), **(info.pop('head_files', None) or {})}.items():
                st.add(rel, src)
            st.write_text('mod.json', mod_json(name, key, info))
            st.write_text('readme.txt', readme(name, mode))
            st.write_text('CREDITS.md', credits({key: info}))
            port_build = portbuild.record(ctx.port, packs.file_hashes(st.path), [key])
            st.publish(mode, {key: info}, extra=dict(group=group, port=str(ctx.port), port_build=port_build,
                                                     tool_version=__version__),
                       enable=enable, disable_others=False)
        folders.append(name)
        characters[key] = dict(info, folder=name)
    # The same hero from another tier in this group (e.g. MUA-Wolverine -> MUA2-Wolverine), or heroes not
    # rebuilt without --keep-existing: those folders go.
    for f, m in existing.items():
        if f in folders:
            continue
        heroes = set((m or {}).get('characters', {}))
        if heroes & set(produced) or not keep_existing:
            packs.remove_folder(mods, f)
        else:
            for k, v in m['characters'].items():
                characters.setdefault(k, dict(v, folder=f, carried_over=True))
    loadorder.write(mods, packs.disable_other_groups(mods, loadorder.read(mods), group))
    code = PARTIAL if refused else OK
    used = sorted({c['folder'] for c in characters.values()})
    return code, dict(pack=group, layout='per-hero', written=True, folders=used, enabled=enable, characters=characters,
                      refused=refused, stock=stock, files=sum(len(packs.file_hashes(mods / f)) for f in used))
