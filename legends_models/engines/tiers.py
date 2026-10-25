# SPDX-License-Identifier: MIT
"""One adapter per tier: produce a candidate actor + its validation report for one hero.

Every adapter calls the research converter for that tier with the research defaults (fitting math
untouched). The build then re-applies the gate itself (legends_models.gate) before using anything.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import community, roster
from ..errors import MISSING_INPUT, ToolError
from . import materialise, run, tail


@dataclass
class Candidate:
    path: Path | None
    report: Path | None          # validation JSON (None for the texture-only tier)
    kind: str                    # 'deform' | 'texture-only'
    log: Path | None = None
    notes: list = field(default_factory=list)
    extra_files: dict = field(default_factory=dict)   # pack-relative path -> source file
    gate: dict | None = None     # texture-only invariant result


class Context:
    def __init__(self, port, work, report, mua1=None, mua2=None, community_cache=None, mirror=None,
                 iterations=None, ai_model=None, cel=True, extractor=None, heads=True, heads_only=False, materials=(), offline=False):
        self.port = Path(port).resolve()
        self.work = Path(work).resolve()
        self.report = report
        self.mua1 = Path(mua1).resolve() if mua1 else None
        self.mua2 = Path(mua2).resolve() if mua2 else None
        self.community_cache = Path(community_cache or self.work / 'community-cache')
        self.mirror = mirror
        self.iterations = iterations
        self.ai_model = ai_model
        self.cel = cel
        self.heads = heads
        self.heads_only = heads_only
        self.extractor = extractor
        self.offline = offline
        self.materials = tuple(materials or ())   # optional MUA material steps: 'spec', 'bake'

    @property
    def env(self):
        env = {'LEGENDS_PORT': str(self.port)}
        if self.mua2:
            env['LEGENDS_MUA2'] = str(self.mua2)
        return env


def _clear(*paths):
    """Remove a previous run's outputs first, so a crashed run can never hand back stale results.
    (The research converters' own fit/animation caches are left alone: they reuse them by design.)"""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def _fail(msg, log=None):
    detail = tail(log) if log else ''
    raise ToolError(msg + (f'\n--- engine log tail ({log}) ---\n{detail}' if detail else ''), log=str(log) if log else None)


# ----------------------------------------------------------------------------------------- sharpen
def sharpen(hero, choice, ctx):
    root = materialise('xml2', ctx.work, needs_sampler=False)
    out = root / 'out' / 'sharpen'
    src = ctx.port / 'Actors' / f'{hero.skin}.IGB'
    dest = out / f'{hero.skin}.IGB'
    rep = out / f'{hero.skin}.json'
    log = root / 'logs' / f'sharpen-{hero.key}.log'
    args = ['--src', src, '--dest', dest, '--report', rep] + (['--ai-model', ctx.ai_model] if ctx.ai_model else [])
    _clear(dest, rep)
    out.mkdir(parents=True, exist_ok=True)
    if run('xml2', root, 'sharpen_one.py', args, ctx.env, log, ctx.report, driver=True):
        _fail(f'{hero.key}: sharpen failed', log)
    from ..gate import texture_only_gate
    info = json.loads(rep.read_text())
    return Candidate(dest, None, 'texture-only', log, [info.get('method', '')], gate=texture_only_gate(info))


# ----------------------------------------------------------------------------------------- MUA1
def mua1(hero, choice, ctx):
    if ctx.mua1 is None:
        raise ToolError('MUA1 install not given/found', MISSING_INPUT)
    root = materialise('mua1', ctx.work)
    name = roster.MUA1_NAMES.get(hero.key, hero.key)
    out = root / 'out' / f'{hero.key}-{choice.source}'
    log = root / 'logs' / f'mua1-{hero.key}-{choice.source}.log'
    args = ['--mua', ctx.mua1, '--port', ctx.port, '--output', out, '--character', name, '--skin', choice.source]
    if ctx.iterations:
        args += ['--fit-iterations', ctx.iterations]
    cand = out / '.mua-converter-cache' / name / choice.source / 'candidate.IGB'
    report = out / 'validation.json'
    _clear(cand, report, out / 'mods', out / 'reports', out / 'conversion.json')
    code = run('mua1', root, 'convert.py', args, ctx.env, log, ctx.report)
    if not (cand.is_file() and report.is_file()):
        _fail(f'{hero.key}: MUA1 converter produced no candidate (exit {code})', log)
    # exit code 1 with "failed stock-relative gate" is a gate refusal; the build reports it.
    return Candidate(cand, report, 'deform', log)


def mua1_source(ctx, hero, choice):
    """The MUA skin file the MUA1 converter read (a loose 2006 file, or its cached copy from actors.bin)."""
    if ctx.mua1 is None:
        return None
    for r in (ctx.mua1, ctx.mua1 / 'Data', ctx.mua1 / 'Setup/Data', ctx.mua1 / 'files/Setup/Data'):
        if (r / 'actors' / f'{choice.source}.igb').is_file():
            return r / 'actors' / f'{choice.source}.igb'
    name = roster.MUA1_NAMES.get(hero.key, hero.key)
    # same layout as mua1(): <work>/mua1/out/<hero>-<id>/.mua-converter-cache/<name>/<id>/<id>.igb
    cached = Path(ctx.work) / 'mua1' / 'out' / f'{hero.key}-{choice.source}' / '.mua-converter-cache' / name /         choice.source / f'{choice.source}.igb'
    return cached if cached.is_file() else None


# ----------------------------------------------------------------------------------------- MUA2
MUA2_SUPPORTED = {'wolverine', 'gambit'}   # vendor/mua2/convert_mua2.py --character choices
# Settings recorded in the accepted Wolverine's evidence (fit.json: 160 iterations over 24 samples per
# clip; bind-refinement.json: all frames, coordinate bound 2). Gambit: the research pass came only from
# the joint weight + bind refinement over all frames (bound 4, surface regulariser); convert alone fails
# stretch and chain. Both tie inner cloth shells to their outer shell (--couple-layers, layers.py).
# Gambit refines twice: first to the stock stretch/chain limits with the shells tied (no inset
# constraint, which otherwise fights the surface terms), then a short bounded pass with the
# offset-preserving constraint until zero layer crossings.
MUA2_RECIPES = {'wolverine': dict(iterations=160, refine=[['--samples', '0', '--bound', '2', '--couple-layers']]),
                'gambit': dict(iterations=120, refine=[
                    ['--iterations', '75', '--samples', '0', '--bound', '4', '--joint-weights', '--surface-regularize',
                     '--couple-layers', '--layer-weight', '0'],
                    ['--iterations', '40', '--samples', '0', '--bound', '1', '--joint-weights', '--surface-regularize',
                     '--couple-layers']])}


def mua2(hero, choice, ctx):
    if ctx.mua2 is None:
        raise ToolError('MUA2 install not given/found', MISSING_INPUT)
    if hero.key not in MUA2_SUPPORTED:
        raise ToolError(f'{hero.key}: no MUA2 converter recipe yet', MISSING_INPUT)
    root = materialise('mua2', ctx.work)
    log = root / 'logs' / f'mua2-{hero.key}.log'
    if run('mua2', root, 'mua2_extract.py', ['--mua2', ctx.mua2, '--id', choice.source], ctx.env, log, ctx.report, driver=True):
        _fail(f'{hero.key}: could not read actors/{choice.source}.igz from MUA2', log)
    recipe = MUA2_RECIPES[hero.key]
    ev = root / 'evidence' / hero.key
    _clear(ev / f'{hero.skin}.IGB', ev / 'gate.json', ev / 'conversion.json', ev / 'bind-refinement.json')
    (root / 'renders').mkdir(parents=True, exist_ok=True)  # validate_mua2.py expects the research folder layout
    args = ['--character', hero.key, '--iterations', ctx.iterations or recipe['iterations']]
    if run('mua2', root, 'convert_mua2.py', args, ctx.env, log, ctx.report):
        _fail(f'{hero.key}: MUA2 converter failed', log)
    for refine in recipe.get('refine') or []:
        if run('mua2', root, 'refine_bind.py', ['--character', hero.key, *refine], ctx.env, log, ctx.report):
            _fail(f'{hero.key}: MUA2 bind refinement failed', log)
    if run('mua2', root, 'validate_mua2.py', ['--character', hero.key, '--no-renders'], ctx.env, log, ctx.report):
        _fail(f'{hero.key}: MUA2 validator failed', log)
    return Candidate(ev / f'{hero.skin}.IGB', ev / 'gate.json', 'deform', log,
                     ['MUA2 Gambit coat lining is a known visual failure'] if hero.key == 'gambit' else [])


# ----------------------------------------------------------------------------------------- XML2
XML2_BESPOKE = {'frost': 'chain fit (refine_chain.py)', 'phoenix': 'chain fit (refine_chain.py)',
                'gambit': 'envelope fit (envelope_fit.py)', 'iceman': 'envelope fit (envelope_fit.py)'}


def xml2(hero, choice, ctx):
    root = materialise('xml2', ctx.work)
    log = root / 'logs' / f'xml2-{hero.key}.log'
    inv = root / 'evidence' / 'inventory.json'
    if not inv.is_file() and run('xml2', root, 'inventory.py', ['--port', ctx.port], ctx.env, log, ctx.report):
        _fail('XML2 inventory failed', log)
    row = json.loads(inv.read_text())[hero.key]
    default = row.get('source')
    # 1) direct transplant (passes for donors whose binds already match, e.g. Mystique/Sabretooth)
    stem = hero.key if choice.source == default else f'{hero.key}-{choice.source}'
    _clear(*(root / 'evidence' / d / f'{n}.{e}' for d, e in [('candidates', 'IGB'), ('gates', 'json'), ('fitted', 'IGB'),
                                                              ('fitted-gates', 'json')] for n in {hero.key, stem}))
    if choice.source == default:
        run('xml2', root, 'build_xml2.py', ['--port', ctx.port, '--characters', hero.key], ctx.env, log, ctx.report)
        cand, rep = root / 'evidence/candidates' / f'{hero.key}.IGB', root / 'evidence/gates' / f'{hero.key}.json'
        if rep.is_file() and json.loads(rep.read_text()).get('pass_numeric'):
            return Candidate(cand, rep, 'deform', log, ['direct transplant'])
    # 2) round-2 fitter with the six reference clips
    args = ['--port', ctx.port, '--characters', hero.key] + (['--source', choice.source] if choice.source != default else [])
    if ctx.iterations:
        args += ['--iterations', ctx.iterations]
    run('xml2', root, 'fit_roster.py', args, ctx.env, log, ctx.report)
    cand, rep = root / 'evidence/fitted' / f'{stem}.IGB', root / 'evidence/fitted-gates' / f'{stem}.json'
    if not (cand.is_file() and rep.is_file()):
        _fail(f'{hero.key}: XML2 fitter produced no candidate', log)
    notes = ['round-2 fit']
    if not json.loads(rep.read_text()).get('pass_numeric') and hero.key in XML2_BESPOKE:
        notes.append(f'research passed this hero with a {XML2_BESPOKE[hero.key]} step that legends-models does not run yet')
    return Candidate(cand, rep, 'deform', log, notes)


# ----------------------------------------------------------------------------------------- community
def community_folder(hero, choice, ctx, report=None):
    """The verified, extracted community archive for this hero/costume (fetched and extracted on first use)."""
    root = materialise('community', ctx.work, needs_sampler=False)
    name = community.KEY_TO_NAME[hero.key]
    rec = community.entry(hero.key, choice.source)
    extractor = ctx.extractor or community.find_extractor()
    if extractor is None:
        raise ToolError('no RAR extractor found (UnRAR, 7-Zip, or Windows tar.exe)', MISSING_INPUT)
    data = community.manifest()
    slug = lambda s: s.lower().replace(' ', '-')
    folder = root / 'extracted' / slug(name) / slug(rec['costume'])
    stamp = folder / '.verified'
    if not (stamp.is_file() and stamp.read_text() == rec['sha256']):
        archive = community.fetch(rec, ctx.community_cache, ctx.mirror, report=report or ctx.report,
                                  offline=getattr(ctx, 'offline', False))
        if folder.exists():
            shutil.rmtree(folder)
        community.extract(archive, folder, rec, extractor, data['allowed_member_suffixes'])
        (folder / '.verified').write_text(rec['sha256'])
    return folder


def community_tier(hero, choice, ctx):
    root = materialise('community', ctx.work)
    name = community.KEY_TO_NAME[hero.key]
    rec = community.entry(hero.key, choice.source)
    log = root / 'logs' / f'community-{hero.key}.log'
    folder = community_folder(hero, choice, ctx)
    slug = lambda s: s.lower().replace(' ', '-')
    skin = community.skin_file(folder, ctx.cel)
    (root / 'CREDITS.md').write_text(community.credits_text(), encoding='utf-8')
    inv_path = root / 'evidence' / 'pack-inventory.json'
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(inv_path.read_text()) if inv_path.is_file() else []
    rows = [r for r in rows if not (r['character'] == name and r['costume'] == rec['costume'])]
    rows.append(dict(character=name, costume=rec['costume'], post=rec['post'], sha256=rec['sha256'],
                     extracted=str(folder), target_skin=hero.skin, animation_db=hero.animation_db, source_skin=str(skin)))
    inv_path.write_text(json.dumps(rows, indent=2))
    work = root / 'output' / slug(name) / slug(rec['costume'])
    fin = root / 'output/final' / slug(name) / slug(rec['costume'])
    _clear(work / f'{hero.skin}.IGB', work / 'validation.json', work / 'conversion.json', fin,
           root / 'output/chain-refined' / slug(name) / slug(rec['costume']), root / 'output/rgba8' / slug(name))
    args = ['--character', name, '--costume', rec['costume']] + (['--iterations', ctx.iterations] if ctx.iterations else [])
    run('community', root, 'community_convert.py', args, ctx.env, log, ctx.report)
    cand, rep = work / f'{hero.skin}.IGB', work / 'validation.json'
    if not (cand.is_file() and rep.is_file()):
        _fail(f'{hero.key}: community converter produced no candidate', log)
    notes = ['round-2 fit']
    recipe = hero.recipe('community')
    steps = recipe.steps if recipe and choice.source == recipe.source else ()
    passed = json.loads(rep.read_text())['acceptance']['numeric_pass']
    if not passed and 'chain_refine' in steps:
        run('community', root, 'refine_candidates.py', ['--character', name, '--no-render'], ctx.env, log, ctx.report)
        rc = root / 'output/chain-refined' / slug(name) / slug(rec['costume'])
        if (rc / 'validation.json').is_file() and (rc / f'{hero.skin}.IGB').is_file():
            cand, rep = rc / f'{hero.skin}.IGB', rc / 'validation.json'
            passed = json.loads(rep.read_text())['acceptance']['numeric_pass']
            notes.append('bounded chain-envelope refinement')
    # Authored wrist/cuff repairs (Jubilee, Magma). They run whenever the recipe lists them, even when the
    # plain fit already passes the numbers: the near-coincident but disconnected cuff shells pass the
    # exact-seam test and only show in the renders, so the repaired file is the one that is used.
    for step, repaired in (('repair_jubilee', 'jubilee-repaired'), ('repair_magma', 'magma-repaired')):
        if step in steps:
            rdir = root / 'output' / repaired
            _clear(rdir)
            if run('community', root, step + '.py', [], ctx.env, log, ctx.report):
                _fail(f'{hero.key}: {step} failed', log)
            for mode in ('authored', 'localized'):
                rc, rr = rdir / mode / f'{hero.skin}.IGB', rdir / mode / 'validation.json'
                if rc.is_file() and rr.is_file() and json.loads(rr.read_text())['acceptance']['numeric_pass']:
                    cand, rep, passed = rc, rr, True
                    notes.append(f'authored wrist/cuff repair ({step}, {mode})')
                    break
            else:
                # The unrepaired fit passes the numbers but shows detached cuffs: refuse rather than ship it.
                _fail(f'{hero.key}: {step} produced no candidate that passes the numeric gate (the plain fit has the cuff defect)', log)
    hide = ''
    if 'repair_juggernaut' in steps:
        rdir = root / 'output' / 'juggernaut-head'
        if run('community', root, 'repair_juggernaut_head.py', [], ctx.env, log, ctx.report):
            _fail(f'{hero.key}: repair_juggernaut_head failed', log)
        rc, rr = rdir / 'final' / f'{hero.skin}.IGB', rdir / 'final' / 'validation.json'
        if not (rc.is_file() and rr.is_file() and json.loads(rr.read_text())['acceptance']['numeric_pass']):
            _fail(f'{hero.key}: head-scaled candidate does not pass the numeric gate', log)
        cand, rep, passed = rc, rr, True
        hide = '3401_head,3401_head_outline,3401_helmet,3401_helmet_outline'
        notes.append('community head/hood scaled to the port skeleton; stock head/helmet segments hidden')
    if 'rgba8' in steps and passed:
        rgba = root / 'output/rgba8' / slug(name)
        if run('community', root, 'rgba8_probe.py', ['--source', skin, '--candidate', cand, '--output', rgba],
               ctx.env, log, ctx.report) == 0 and (rgba / f'{hero.skin}.IGB').is_file():
            cand = rgba / f'{hero.skin}.IGB'
            notes.append('RGBA8 texture (rgba8_probe)')
    # Finishing: mip block-row pitch fix + validation bound to the final bytes.
    args = ['--port', ctx.port, '--source', skin, '--candidate', cand, '--animations', work / 'animations',
            '--out-dir', fin, '--db', hero.animation_db + '.IGB', '--target', hero.skin] + (['--claws'] if hero.key == 'wolverine' else []) + (['--hide-segments', hide] if hide else [])
    if run('community', root, 'community_finish.py', args, ctx.env, log, ctx.report, driver=True):
        _fail(f'{hero.key}: community finishing/validation failed', log)
    # heads/portraits are routed after the gate by heads.collect (build.add_heads), like the MUA1 ones
    return Candidate(fin / f'{hero.skin}.IGB', fin / 'validation.json', 'deform', log, notes)


ADAPTERS = {'sharpen': sharpen, 'mua1': mua1, 'mua2': mua2, 'xml2': xml2, 'community': community_tier}
