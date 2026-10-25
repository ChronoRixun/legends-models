# SPDX-License-Identifier: MIT
"""Conversion result cache: the same inputs never need the CPU-heavy fit twice.

Key = tier, hero, costume, options, tool version, the port's fingerprint and the source
container's identity (size + mtime of the install file it is read from, or the community archive's
SHA-256). Entries live in the per-port work folder (private derived data). A hit is re-gated: the
cached report must still name the cached actor's exact hash, so a damaged entry is just a miss.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import __version__, community, gate
from .workspace import fingerprint


def _stat(p):
    p = Path(p)
    try:
        st = p.stat()
        return f'{p.name}|{st.st_size}|{st.st_mtime_ns}'
    except OSError:
        return f'{p.name}|missing'


def source_identity(hero, choice, ctx):
    t = choice.tier
    if t == 'mua1' and ctx.mua1:
        loose = [ctx.mua1 / r / 'actors' / f'{choice.source}.igb' for r in ('', 'Data', 'Setup/Data', 'files/Setup/Data')]
        hit = next((p for p in loose if p.is_file()), None)
        return [_stat(hit)] if hit else [_stat(ctx.mua1 / 'actors.bin')]
    if t == 'mua2' and ctx.mua2:
        return [_stat(ctx.mua2 / n) for n in ('actors.bin', 'materials.bin', 'textures.bin')]
    if t == 'community':
        return [community.entry(hero.key, choice.source)['sha256'], 'cel' if ctx.cel else 'nocel']
    if t == 'xml2':
        return [_stat(ctx.port / 'Actors' / f'{choice.source}.IGB')]
    if t == 'sharpen':
        return [_stat(ctx.ai_model) if ctx.ai_model else 'lanczos']
    return []


def key(hero, choice, ctx):
    data = dict(tier=choice.tier, hero=hero.key, source=choice.source, iterations=ctx.iterations, tool=__version__,
                port=fingerprint(ctx.port), inputs=source_identity(hero, choice, ctx))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:32]


def folder(ctx, k):
    return Path(ctx.work) / 'results' / k


def load(ctx, k):
    """(candidate_path, gate_result, notes, extra_files) or None."""
    d = folder(ctx, k)
    meta_p = d / 'meta.json'
    if not meta_p.is_file():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding='utf-8'))
        actor = d / 'actor.IGB'
        if meta['kind'] == 'texture-only':
            if gate.sha256(actor) != meta['sha256']:
                return None
            result = dict(meta['gate'], candidate_sha256=meta['sha256'])
        else:
            result = gate.check_report(d / 'report.json', actor, require_layers=meta.get('require_layers', False))
            if result.get('candidate_sha256') != meta['sha256']:
                return None
        extras = {rel: d / 'extras' / rel for rel in meta.get('extras', [])}
        if not all(f.is_file() for f in extras.values()):
            return None
        return actor, result, meta.get('notes', []), extras
    except (OSError, ValueError, KeyError):
        return None


def store(ctx, k, cand, result, require_layers=False):
    d = folder(ctx, k)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    shutil.copyfile(cand.path, d / 'actor.IGB')
    if cand.report:
        shutil.copyfile(cand.report, d / 'report.json')
    for rel, src in cand.extra_files.items():
        (d / 'extras' / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / 'extras' / rel)
    meta = dict(kind=cand.kind, extras=sorted(cand.extra_files), sha256=result['candidate_sha256'], notes=cand.notes, passed=result['numeric_pass'],
                require_layers=require_layers,
                gate=result if cand.kind == 'texture-only' else None)
    (d / 'meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    cand.extra_files = {rel: d / 'extras' / rel for rel in cand.extra_files}
    return d / 'actor.IGB'
