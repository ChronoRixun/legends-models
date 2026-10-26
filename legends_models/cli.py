# SPDX-License-Identifier: MIT
"""legends-models command line (see README.md and LAUNCHER-API.md)."""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from . import __version__, community, errors, packs, plan as planmod, roster, scan as scanmod
from .errors import ToolError
from .progress import Reporter


def default_work():
    base = os.environ.get('LEGENDS_MODELS_WORK') or os.path.join(os.environ.get('LOCALAPPDATA', str(Path.home())), 'legends-models')
    return Path(base)


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', help='one JSON object per line on stdout (launcher mode)')
    common.add_argument('--verbose', action='store_true', help='write technical recipe notes to stderr (never JSON)')
    common.add_argument('--port', type=Path, help='Legends Classic install (has Actors\\, Data\\, libIGSg.dll)')
    common.add_argument('--mua1', type=Path, help='MUA1 install: 2016 Steam folder (actors.bin) or 2006 loose Data')
    common.add_argument('--mua2', type=Path, help='MUA2 install folder (actors.bin, materials.bin, textures.bin)')
    common.add_argument('--steam', type=Path, help='Steam folder, to find MUA1/MUA2 libraries')
    common.add_argument('--work', type=Path, default=default_work(), help='private cache/work folder (default %%LOCALAPPDATA%%\\legends-models)')
    common.add_argument('--game', type=Path, help='folder whose mods\\ receives the pack (default: --port)')
    common.add_argument('--hero', action='append', help='limit to these heroes (repeatable)')
    common.add_argument('--offline', action='store_true', help='never download: community archives only from the cache (missing ones are refused, exit 20)')
    common.add_argument('--python', help='interpreter for the converters (NumPy+SciPy+Pillow); default LEGENDS_MODELS_PYTHON or this one')
    p = argparse.ArgumentParser(prog='legends-models', description='Optional character-model mods for Legends Classic, '
                                'built on this PC from games you own.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('scan', parents=[common], help='find installs; what each can supply per hero')
    for name in ('plan', 'build'):
        s = sub.add_parser(name, parents=[common], help='pick a tier per hero' if name == 'plan' else 'convert, gate, write the pack')
        s.add_argument('--mode', choices=list(planmod.MODES), default='community')
        s.add_argument('--costume', action='append', help='hero=ID or hero=tier:ID, e.g. wolverine=mua1:0303')
        s.add_argument('--strict', action='store_true', help='only owner-accepted costumes')
        s.add_argument('--skip-tier', action='append', choices=list(roster.TIERS), default=[], help='leave a tier out of the order')
        if name == 'build':
            s.add_argument('--pack', help='mods folder name (default per mode, e.g. Models-Community)')
            s.add_argument('--mirror', help='https base URL of the community archives backup, tried only when the '
                           'author\'s link fails (default: this project\'s GitHub backup; "off" = none)')
            s.add_argument('--no-mirror', action='store_true',
                           help='community archives only from the author\'s links (never the GitHub backup)')
            s.add_argument('--iterations', type=int, help='fitter iteration ceiling (research default if omitted)')
            s.add_argument('--ai-model', type=Path, help='sharpen: local Real-ESRGAN x4 ONNX model (optional)')
            s.add_argument('--no-cel', action='store_true', help='community: use the non-cel skin variant')
            s.add_argument('--materials', choices=['spec', 'bake', 'spec+bake'],
                           help='MUA1: also use the MUA specular map (gloss-masked environment shader) and/or bake '
                                'the normal map into the diffuse (optional; off by default)')
            s.add_argument('--keep-existing', action='store_true', help='keep characters already in the pack that this run does not rebuild')
            s.add_argument('--disabled', action='store_true', help='write the pack but leave it disabled in load-order.txt')
            s.add_argument('--rebuild', action='store_true', help='ignore cached conversion results')
            s.add_argument('--no-heads', action='store_true',
                           help='do not install conversation heads, HUD heads and select portraits with the models')
            s.add_argument('--heads-only', action='store_true',
                           help='only the heads/portraits of each hero chosen source (no actor conversion; same folders)')
            s.add_argument('--layout', choices=['per-hero', 'pack'], default='per-hero',
                           help='per-hero (default): MUA-Wolverine, Community-Beast, ... one folder per hero, the mua-models2 '
                                'production layout; pack: one folder for the whole appearance')
    r = sub.add_parser('remove', parents=[common], help='delete a pack folder and its load-order entry')
    r.add_argument('--pack', required=True)
    v = sub.add_parser('verify', parents=[common], help='hash the pack and compare with its (or a given) manifest')
    v.add_argument('--pack', required=True)
    v.add_argument('--manifest', type=Path)
    f = sub.add_parser('fetch', parents=[common], help='download + verify community archives only')
    f.add_argument('--costume', action='append')
    f.add_argument('--mirror', help='https backup base URL (see build --mirror)')
    f.add_argument('--no-mirror', action='store_true', help='only the author\'s links (see build --no-mirror)')
    return p


def _heroes(args):
    return [roster.hero(h) for h in args.hero] if args.hero else roster.HEROES


def _game(args, inst):
    g = args.game or (inst['port'].path if inst['port'].path else None)
    if g is None:
        raise ToolError('no --game/--port folder to write mods into', errors.MISSING_INPUT)
    return Path(g)


def cmd_scan(args, rep):
    inst = scanmod.scan(args.port, args.mua1, args.mua2, args.steam)
    cache = scanmod.scan_community(args.work / 'community-cache')
    from . import costumes as costumesmod
    from .workspace import port_work_path
    pwork = port_work_path(args.work, inst['port'].path) if inst['port'].path else None
    from . import costume_names
    names = costume_names.from_installs(inst)
    heroes = {}
    for h in _heroes(args):
        tiers = {}
        for t in roster.TIERS:
            r = h.recipe(t)
            ok, why = scanmod.availability(inst, t, h, r)
            entry = dict(available=ok, reason=why)
            if r:
                entry.update(source=r.source, status=r.status, note=r.player_line, costumes=sorted(set(r.alternatives) | {r.source}) if r.source else [])
                entry['author'] = costumesmod.tier_author(t)
                entry['costume_info'] = costumesmod.info(h, t, entry['costumes'], pwork, names=names)
            if t == 'community' and r:
                entry['cached'] = {c: cache.get((community.KEY_TO_NAME[h.key], c), False) for c in entry['costumes']}
            tiers[t] = entry
        heroes[h.key] = dict(name=h.name, skin=h.skin, villain=h.villain, tiers=tiers)
    result = dict(installs={k: v.as_dict() for k, v in inst.items() if k != 'libraries'}, steam_libraries=inst['libraries'],
                  helpers=scanmod.helpers(args.work), heroes=heroes)
    if not args.json:
        for k in ('port', 'mua1', 'mua2'):
            i = inst[k]
            print(f'{k:5} {"found" if i.found else "missing":8} {i.flavour:11} {i.path or ""} {"; ".join(i.problems)}')
        print('helpers', result['helpers'])
        for key, h in heroes.items():
            print(f'{h["name"]:<22}', ' '.join(f'{t}={"yes" if h["tiers"][t]["available"] else "-"}' for t in roster.TIERS))
        return errors.OK
    rep.result(result)
    return errors.OK


def _plan(args, inst):
    costumes = planmod.parse_costumes(getattr(args, 'costume', None))
    avail = lambda tier, hero, recipe, source: scanmod.availability(inst, tier, hero, recipe, source)
    if getattr(args, 'offline', False):
        cached = scanmod.scan_community(args.work / 'community-cache')
        online_avail = avail

        def avail(tier, hero, recipe, source):
            ok, why = online_avail(tier, hero, recipe, source)
            if ok and tier == 'community' and not cached.get((community.KEY_TO_NAME[hero.key], source), False):
                return False, f'{source} is not in the download cache (offline)'
            return ok, why
    return planmod.make_plan(args.mode, avail, _heroes(args), costumes, args.strict, tuple(args.skip_tier))


def cmd_plan(args, rep):
    inst = scanmod.scan(args.port, args.mua1, args.mua2, args.steam)
    rows = _plan(args, inst)
    if args.json:
        rep.result(dict(mode=args.mode, pack=planmod.PACK_NAMES[args.mode], rows=[r.as_dict() for r in rows]))
    else:
        print(f'mode {args.mode} -> mods\\{planmod.PACK_NAMES[args.mode]}')
        print(planmod.table(rows))
    return errors.OK


def cmd_build(args, rep):
    from .build import run_build
    from .engines.tiers import Context
    inst = scanmod.scan(args.port, args.mua1, args.mua2, args.steam)
    if not inst['port'].found:
        raise ToolError('; '.join(inst['port'].problems), errors.MISSING_INPUT)
    rows = _plan(args, inst)
    rep.emit('plan', mode=args.mode, rows=[r.as_dict() for r in rows])
    from .engines import SAMPLER
    from .workspace import port_work
    shared_sampler = args.work / 'bin' / SAMPLER
    if not os.environ.get('LEGENDS_MODELS_SAMPLER') and shared_sampler.is_file():
        os.environ['LEGENDS_MODELS_SAMPLER'] = str(shared_sampler)
    work = port_work(args.work, inst['port'].path)
    rep.emit('workspace', path=str(work))
    ctx = Context(inst['port'].path, work, rep, inst['mua1'].path if inst['mua1'].found else None,
                  inst['mua2'].path if inst['mua2'].found else None, args.work / 'community-cache', community.resolve_mirror(args.mirror, disabled=args.no_mirror),
                  args.iterations, args.ai_model, not args.no_cel,
                  heads=not args.no_heads or args.heads_only, heads_only=args.heads_only,
                  materials=tuple((args.materials or '').split('+')) if args.materials else (), offline=args.offline)
    pack = args.pack or planmod.PACK_NAMES[args.mode]
    code, result = run_build(inst, rows, ctx, _game(args, inst), pack, args.mode, rep,
                             keep_existing=args.keep_existing or bool(args.hero), enable=not args.disabled,
                             rebuild=args.rebuild, layout=args.layout)
    rep.result(result)
    return code


def cmd_remove(args, rep):
    inst = {'port': scanmod.scan_port(args.port)}
    result = packs.remove(_game(args, inst) / 'mods', args.pack)
    rep.result(result)
    return errors.OK


def cmd_verify(args, rep):
    inst = {'port': scanmod.scan_port(args.port)}
    mods = _game(args, inst) / 'mods'
    result = packs.verify_group(mods, args.pack, args.manifest)
    from . import loadorder, portbuild
    result['load_order'] = {True: 'enabled', False: 'disabled', None: 'not listed'}[loadorder.is_enabled(loadorder.read(mods), args.pack)]
    # port build: is every folder still made for the port as it is now?
    folders = result.get('folders') or [args.pack]
    stale, changed, unknown = [], [], []
    for f in folders:
        m = packs.read_marker(mods / f) or {}
        port = args.port or (Path(m['port']) if m.get('port') else None)
        c = portbuild.check(port, m.get('port_build'))
        if c['stale']:
            stale.append(f)
            changed += [f'{f}: {r}' for r in c['changed']]
        elif c['stale'] is None:
            unknown.append(f)
    result.update(stale=bool(stale), stale_folders=stale, port_changed=changed, port_build_unknown=unknown)
    rep.result(result)
    if not result['ok']:
        return errors.VERIFY_MISMATCH
    return errors.STALE if stale else errors.OK


def cmd_fetch(args, rep):
    costumes = planmod.parse_costumes(args.costume)
    done = []
    for h in _heroes(args):
        r = h.recipe('community')
        if not r or r.status == 'none':
            continue
        c = planmod.costume_for(h, r, costumes) or r.source
        rec = community.entry(h.key, c)
        p = community.fetch(rec, args.work / 'community-cache', community.resolve_mirror(args.mirror, disabled=args.no_mirror), report=rep,
                            offline=args.offline)
        done.append(dict(hero=h.key, costume=c, archive=str(p), sha256=rec['sha256']))
    rep.result(dict(fetched=done))
    return errors.OK


COMMANDS = {'scan': cmd_scan, 'plan': cmd_plan, 'build': cmd_build, 'remove': cmd_remove, 'verify': cmd_verify,
            'fetch': cmd_fetch}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        if e.code in (0, None):
            return errors.OK
        if '--json' in argv:
            Reporter(True).error('invalid arguments (see usage on stderr)', errors.USAGE)
        return errors.USAGE
    rep = Reporter(args.json)
    if args.python:
        os.environ['LEGENDS_MODELS_PYTHON'] = args.python
    try:
        if args.verbose and args.command in ('scan', 'plan', 'build'):
            for h in _heroes(args):
                for r in h.recipes.values():
                    if r.note:
                        print(f'{h.name} / {r.tier}: {r.note}', file=sys.stderr)
        return COMMANDS[args.command](args, rep)
    except ToolError as e:
        rep.error(str(e), e.code, **e.data)
        return e.code
    except (KeyError, ValueError) as e:
        rep.error(str(e), errors.USAGE)
        return errors.USAGE
    except KeyboardInterrupt:
        rep.error('cancelled', errors.CANCELLED)
        return errors.CANCELLED
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        rep.error(f'internal error: {e!r}', errors.INTERNAL)
        return errors.INTERNAL


if __name__ == '__main__':
    sys.exit(main())
