# SPDX-License-Identifier: MIT
"""Offline accepted-result bundles, independent of per-port and tool-version caches.

The acceptance catalogue is the authority for output bytes. A bundle contains
actor.IGB, report.json and a manifest of content hashes for the owned inputs.
Unchanged accepted inputs must never fall through to a fresh optimizer run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath

from . import gate
from .engines.tiers import Candidate
from .errors import MISSING_INPUT, ToolError
from .formats.mua_bin import Archive


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('invalid bundle input path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ('', '.', '..') for x in value.split('/')):
        raise ValueError('bundle input path must be relative without traversal')
    return p.as_posix()


def _file(root, name):
    if root is None:
        raise ValueError('source install not supplied')
    root = Path(root).resolve()
    p = (root / _relative(name)).resolve()
    if not p.is_relative_to(root):
        raise ValueError('bundle input escapes its install')
    return p


def _source_bytes(hero, choice, ctx):
    root = getattr(ctx, choice.tier, None)
    if root is None:
        raise ValueError(f'{choice.tier} install not supplied')
    if choice.tier == 'mua1':
        for prefix in ('', 'Data/', 'Setup/Data/', 'files/Setup/Data/'):
            p = _file(root, f'{prefix}actors/{choice.source}.igb')
            if p.is_file():
                return p.read_bytes()
    ext = 'igz' if choice.tier == 'mua2' else 'igb'
    archive = Archive(_file(root, 'actors.bin'))
    wanted = f'actors/{choice.source}.{ext}'.casefold()
    entry = next((e for e in archive.entries if e[0].casefold() == wanted), None)
    if entry is None:
        raise ValueError(f'{wanted} not found in owned archive')
    return archive.read(entry)


def input_hash(ref, hero, choice, ctx):
    if ref['root'] == 'source':
        return hashlib.sha256(_source_bytes(hero, choice, ctx)).hexdigest()
    if ref['root'] not in ('port', choice.tier):
        raise ValueError('invalid bundle input root')
    p = _file(getattr(ctx, ref['root']), ref['path'])
    # Hash a large archive just once per build, never reuse a stat-only identity
    # across invocations (same-size/mtime edits must invalidate acceptance).
    memo = getattr(ctx, '_accepted_hashes', None)
    if memo is None:
        memo = ctx._accepted_hashes = {}
    if p not in memo:
        memo[p] = gate.sha256(p)
    return memo[p]


def required_inputs(hero, choice):
    refs = [('source', None), ('port', f'Actors/{hero.skin}.IGB'), ('port', 'libIGSg.dll'),
            ('port', f'Actors/{hero.animation_db}.IGB'), ('port', 'Actors/common.IGB')]
    if choice.tier == 'mua2':
        refs += [('mua2', 'materials.bin'), ('mua2', 'textures.bin')]
    return set(refs)


def _report_inputs(raw):
    if not isinstance(raw, dict):
        raise ValueError('accepted report must be an object')
    refs = {('port', 'Models/Bolton/' + _relative(n))
            for n in raw.get('attachments', {}).get('files', {})}
    for row in raw.get('clips', raw.get('animations', {})).values():
        if row.get('database'):
            refs.add(('port', 'Actors/' + _relative(row['database'])))
    return refs


def _validate_manifest(meta, record, hero, choice, extra_required=()):
    if meta['schema'] != 1 or any(meta[k] != record[k] for k in ('hero', 'tier', 'source', 'output_sha256')):
        raise ValueError('accepted manifest identity mismatch')
    refs = meta['inputs']
    if not isinstance(refs, list) or len(refs) > 256:
        raise ValueError('invalid accepted input manifest')
    seen = set()
    for ref in refs:
        identity = ref['root'], ref['path']
        if identity in seen:
            raise ValueError('duplicate input binding')
        seen.add(identity)
        if ref['root'] != 'source':
            _relative(ref['path'])
        if not isinstance(ref['sha256'], str) or len(ref['sha256']) != 64 or any(c not in '0123456789abcdef' for c in ref['sha256']):
            raise ValueError('invalid input digest')
    if not (required_inputs(hero, choice) | set(extra_required)) <= seen:
        raise ValueError('accepted manifest omits source, template, animation, sampler DLL or texture archive binding')
    by_ref = {(r['root'], r['path']): r['sha256'] for r in refs}
    if by_ref['source', None] != record['source_sha256'] or by_ref['port', f'Actors/{hero.skin}.IGB'] != record['template_sha256']:
        raise ValueError('accepted manifest differs from catalogue source/template hashes')


def accepted_gate(report, actor, record):
    """Owner approval is for these exact bytes, even when a later criterion fails."""
    result = gate.check_report(report, actor, require_layers=record['tier'] in ('mua1', 'mua2'))
    norm = gate.normalize(json.loads(Path(report).read_text(encoding='utf-8')))
    if gate.sha256(actor) != record['output_sha256'] or norm['candidate_sha256'] != record['output_sha256']:
        raise ValueError('accepted actor/report hash differs from catalogue')
    result.update(owner_accepted=True, reason='owner_accepted_exact_output_hash',
                  acceptance_reason='Owner accepted this exact output hash; later numeric criteria do not revoke that decision')
    return result


def bundle_available(hero, choice, cache, records):
    """Verify a complete local bundle for scan/plan, without reading owned archives.

    The build independently rehashes every owned input before allowing reuse.
    This check prevents reuse-only recipes from promising an absent/damaged
    output and leaving the launcher with a permanent tier-mismatch update.
    """
    roots = [Path(cache)]
    if os.environ.get('LEGENDS_MODELS_ACCEPTED'):
        roots.append(Path(os.environ['LEGENDS_MODELS_ACCEPTED']))
    for record in records.values():
        if ((record.get('hero'), record.get('tier'), record.get('source')) !=
                (hero.key, choice.tier, choice.source) or record.get('status', 'accepted') != 'accepted'):
            continue
        for root in roots:
            folder = root / record['output_sha256']
            try:
                paths = [root, folder, *(folder / n for n in ('actor.IGB', 'manifest.json', 'report.json'))]
                if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in paths):
                    continue
                if not folder.is_dir() or {p.name for p in folder.iterdir()} != {'actor.IGB', 'manifest.json', 'report.json'}:
                    continue
                if not all((folder / n).is_file() for n in ('actor.IGB', 'manifest.json', 'report.json')):
                    continue
                meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                raw = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
                _validate_manifest(meta, record, hero, choice, _report_inputs(raw))
                if any(ref['root'] not in ('source', 'port', record['tier']) or
                       ref['root'] == 'source' and ref['path'] is not None for ref in meta['inputs']):
                    continue
                accepted_gate(folder / 'report.json', folder / 'actor.IGB', record)
                return True
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
    return False


def lookup(hero, choice, ctx, records):
    """(accepted candidate or None, changed-input content key or None).

    A content key permits reuse of a later numeric fit for those exact changed
    inputs; it can never hit an older stat-only result cache entry.
    """
    known = [r for r in records.values() if (r.get('hero'), r.get('tier'), r.get('source')) ==
             (hero.key, choice.tier, choice.source)]
    if not known:
        return None, None
    event = dict(hero=hero.key, tier=choice.tier, source=choice.source, kind='accepted')
    roots = [Path(ctx.accepted_cache)]
    if os.environ.get('LEGENDS_MODELS_ACCEPTED'):
        roots.append(Path(os.environ['LEGENDS_MODELS_ACCEPTED']))
    def changed_key(extra_refs=()):
        refs = required_inputs(hero, choice) | set(extra_refs)
        rows = [dict(root=r, path=p, sha256=input_hash(dict(root=r, path=p), hero, choice, ctx))
                for r,p in sorted(refs, key=lambda item: (item[0], item[1] or ''))]
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    try:
        source_hash = input_hash(dict(root='source'), hero, choice, ctx)
        template_hash = input_hash(dict(root='port', path=f'Actors/{hero.skin}.IGB'), hero, choice, ctx)
    except (OSError, ValueError) as e:
        ctx.report.emit('cache', **event, hit=False, reason='input_unavailable', message=str(e))
        raise ToolError(f'{hero.name}: cannot verify accepted inputs: {e}', MISSING_INPUT) from e
    matching = [r for r in known if r['source_sha256'] == source_hash and r['template_sha256'] == template_hash]
    if not matching:
        extra_refs = set()
        for record in known:
            for root in roots:
                try:
                    meta = json.loads((root / record['output_sha256'] / 'manifest.json').read_text(encoding='utf-8'))
                    _validate_manifest(meta, record, hero, choice)
                    extra_refs |= {(r['root'], r['path']) for r in meta['inputs']}
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        try:
            content_key = changed_key(extra_refs)
        except (OSError, ValueError) as e:
            raise ToolError(f'{hero.name}: cannot hash changed accepted inputs: {e}', MISSING_INPUT) from e
        ctx.report.emit('cache', **event, hit=False, reason='inputs_changed', refit=True,
                        changed_inputs=['source/template'], message='Your game files differ from the approved model; converting this hero again')
        return None, content_key
    changed, problems, extra_refs = [], [], set()
    for record in matching:
        for root in roots:
            folder = root / record['output_sha256']
            if not (folder / 'manifest.json').is_file():
                continue
            try:
                meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                raw_report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
                _validate_manifest(meta, record, hero, choice, _report_inputs(raw_report))
                extra_refs |= {(r['root'], r['path']) for r in meta['inputs']}
                mismatch = [f"{ref['root']}:{ref['path'] or 'actor'}" for ref in meta['inputs']
                            if input_hash(ref, hero, choice, ctx) != ref['sha256']]
                if mismatch:
                    changed.extend(mismatch)
                    continue
                actor, report = folder / 'actor.IGB', folder / 'report.json'
                if gate.sha256(actor) != record['output_sha256']:
                    raise ValueError('accepted actor missing or corrupt')
                result = accepted_gate(report, actor, record)
                ctx.report.emit('cache', **event, hit=True, reason='accepted_inputs_match', refit=False,
                                input_key=hashlib.sha256(json.dumps(meta['inputs'], sort_keys=True).encode()).hexdigest(),
                                output_sha256=record['output_sha256'], owner_accepted=True, numeric_pass=result['numeric_pass'],
                                acceptance_reason=result['acceptance_reason'], message='Approved model')
                return Candidate(actor, report, 'deform', notes=['accepted output reused (input hashes match; owner accepted exact bytes)'], gate=result), None
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                problems.append(str(e))
    # Only a valid manifest with a demonstrated hash difference permits refitting.
    # Damaged/missing bundles and mismatched report hashes cannot silently change an accepted look.
    if changed and not problems:
        ctx.report.emit('cache', **event, hit=False, reason='inputs_changed', refit=True,
                        changed_inputs=sorted(set(changed)), message='Your game files differ from the approved model; converting this hero again')
        return None, changed_key(extra_refs)
    message = f'{hero.name}: accepted output unavailable or report/hash invalid; populate the offline accepted cache'
    if problems:
        message += ': ' + problems[0]
    ctx.report.emit('cache', **event, hit=False, reason='accepted_unavailable', refit=False, message=message)
    raise ToolError(message, MISSING_INPUT)


def seed(hero, choice, ctx, record, actor, report, conversion, cache, extra_port_files=()):
    """Coordinator helper: snapshot the ORIGINAL accepted inputs, never a later install.

    Only catalogue-approved bytes with a hash-bound independent report can
    enter the cache. No source game files are copied into the bundle.
    """
    if gate.sha256(actor) != record['output_sha256']:
        raise ValueError('actor is not the catalogue-approved output')
    accepted_gate(report, actor, record)
    refs = required_inputs(hero, choice)
    refs |= {('port', 'Actors/' + db) for _, db in conversion.get('clips', [])}
    refs |= {('port', _relative(n)) for n in extra_port_files}
    raw_report = json.loads(Path(report).read_text(encoding='utf-8'))
    refs |= _report_inputs(raw_report)
    inputs = [dict(root=r, path=p, sha256=input_hash(dict(root=r, path=p), hero, choice, ctx))
              for r,p in sorted(refs, key=lambda item: (item[0], item[1] or ''))]
    meta = dict(schema=1, hero=hero.key, tier=choice.tier, source=choice.source,
                output_sha256=record['output_sha256'], inputs=inputs)
    _validate_manifest(meta, record, hero, choice)
    folder = Path(cache).resolve() / record['output_sha256']
    folder.mkdir(parents=True, exist_ok=True)
    # Incomplete writes are never usable: manifest is replaced last.
    for src, name in [(actor, 'actor.IGB'), (report, 'report.json')]:
        temp = folder / (name + '.tmp')
        shutil.copyfile(src, temp)
        os.replace(temp, folder / name)
    temp = folder / 'manifest.json.tmp'
    temp.write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8', newline='\n')
    os.replace(temp, folder / 'manifest.json')
    return folder


def main():
    from .build import acceptance_records
    from .engines.tiers import Context
    from .plan import Choice
    from .progress import Reporter
    from .roster import BY_KEY
    ap = argparse.ArgumentParser(description='Seed an offline accepted bundle using its ORIGINAL input installs')
    for name in ('hero', 'tier', 'source', 'actor', 'report', 'conversion', 'port', 'cache'):
        ap.add_argument('--' + name, required=True)
    ap.add_argument('--mua1')
    ap.add_argument('--mua2')
    ap.add_argument('--port-file', action='append', default=[], help='additional relative input (repeatable)')
    a = ap.parse_args()
    record = acceptance_records().get(gate.sha256(a.actor))
    if not record or (record['hero'], record['tier'], record['source']) != (a.hero, a.tier, a.source):
        ap.error('actor/hero/tier/source must match the existing acceptance catalogue')
    ctx = Context(a.port, a.cache, Reporter(), a.mua1, a.mua2)
    folder = seed(BY_KEY[a.hero], Choice(a.tier, a.source, 'accepted', True, 'seed'), ctx, record,
                  a.actor, a.report, json.loads(Path(a.conversion).read_text(encoding='utf-8')),
                  a.cache, a.port_file)
    print(folder)


if __name__ == '__main__':
    main()
