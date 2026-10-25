# SPDX-License-Identifier: MIT
"""The owner's stock-relative acceptance gate (numeric part), applied independently to an engine's
validation report before anything is written into a pack.

Rules (spec-character-models.md), per reference clip, body vs stock body AND outline vs stock outline:
  max per-triangle edge stretch <= stock's;  max bone-chain distance <= stock's;
  coincident vertices stay coincident (separation exactly 0) in every frame;
  hand/head/Motion matrix delta exactly 0;  attachments keep hand-local coordinates;
  idle feet within 2% of stock height;  the port skeleton/palette/bounds unchanged;
  all six reference clips present;
  layered cloth: zero layer crossings in the converted bind and in every frame of every clip
  (MUA1/MUA2 validators, engines/vendor/_shared/layers.py), required for the MUA1/MUA2 tiers.
Offline renders, in-game close-ups and on/off checks are the visual part of the gate; they are
recorded per exact output hash in data/acceptance.json and reported, never inferred.

The three research validators write different JSON shapes; `normalize` maps each to one form and
`evaluate` applies the rules. `stock_relative_gate` is the MUA1 validator's function, unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

REFERENCE_CLIPS = ['idle', 'run', 'attack_light1', 'f_heavy_attack_4', 'power_2', 'pain_high']
PAIRS = [('converted', 'stock'), ('converted_outline', 'stock_outline')]
METRICS = ['max_edge_ratio', 'max_chain_distance']
FEET_LIMIT = 0.02
ATTACHMENT_EPS = 1e-10


def stock_relative_gate(output):
    """mua-models2/tools/validate.py (MUA1 + community validator format), plus the layer-crossing rule."""
    failures = []
    if not output.get('gameplay_structure_unchanged', True):
        failures.append('Port skeleton, palette or bounds changed')
    for name in REFERENCE_CLIPS:
        if name not in output['animations']:
            failures.append(name + ': missing reference clip'); continue
        row = output['animations'][name]
        for new, old in [('converted', 'stock'), ('converted_outline', 'stock_outline')]:
            for metric in ['max_edge_ratio', 'max_chain_distance']:
                values = [row['models'][new][metric], row['models'][old][metric]]
                if not all(math.isfinite(v) for v in values):
                    failures.append(f'{name}: non-finite {metric}')
                elif values[0] > values[1]:
                    failures.append(f'{name}: {new} {metric} exceeds stock')
            if row['models'][new]['max_seam_separation'] != 0:
                failures.append(f'{name}: {new} seam opens')
        if row['hand_head_motion_matrix_delta'] != 0:
            failures.append(name + ': gameplay matrix changed')
        if not math.isfinite(row['claw_hand_local_transform_error']) or row['claw_hand_local_transform_error'] > 1e-10:
            failures.append(name + ': attachment coordinates changed')
    feet = output['animations'].get('idle', {}).get('foot_height_fraction', float('inf'))
    if not math.isfinite(feet) or feet > .02:
        failures.append('idle: feet differ by more than 2% of stock height')
    # Layered-cloth rule added with the layer-crossing check (vendor/mua1/validate.py does the same).
    lc = output.get('layer_check')
    if lc is not None:
        if lc.get('bind_crossings') != 0:
            failures.append(f"bind: converted layer crossings ({lc.get('bind_crossings')})")
        for name in REFERENCE_CLIPS:
            n = output['animations'].get(name, {}).get('models', {}).get('converted', {}).get('layer_crossings', 0)
            if n != 0:
                failures.append(f'{name}: converted layer crossings ({n})')
    return {'numeric_pass': not failures, 'failures': failures, 'idle_feet_fraction_max': feet,
            'comparison': 'body against stock body, outline against stock outline; separately per clip',
            'visual_acceptance': 'requires separate full-size offline and native inspection'}


def detect(report):
    if 'animations' in report:
        return 'mua1'           # mua-models2 validate.evaluate (also models-community)
    if 'clips' in report and 'pass_numeric' in report:
        return 'xml2'           # models-xml2 validate_roster.measure
    if 'clips' in report and 'numeric_available_clips_pass' in report:
        return 'mua2'           # mua2-models validate_mua2.check
    raise ValueError('unrecognised validation report')


def normalize(report):
    kind = detect(report)
    out = {'kind': kind, 'clips': {}, 'structure_unchanged': None, 'candidate_sha256': None, 'pairs': PAIRS,
           'layer_check': report.get('layer_check')}
    if kind == 'mua1':
        out['structure_unchanged'] = report.get('gameplay_structure_unchanged')
        out['candidate_sha256'] = report.get('converted_sha256')
        for name, row in report['animations'].items():
            out['clips'][name] = dict(models=row['models'], matrix_delta=row.get('hand_head_motion_matrix_delta'),
                                      attachment_error=row.get('claw_hand_local_transform_error'),
                                      feet_fraction=row.get('foot_height_fraction'))
    else:
        out['candidate_sha256'] = report.get('candidate_sha256')
        if kind == 'xml2':
            # validate_roster measures every mesh of the actor together (body + outline hull + parts).
            out['pairs'] = [('converted', 'stock')]
            inv = report.get('invariant', {})
            out['structure_unchanged'] = bool(inv.get('skeleton_and_bind_bytes_identical')) and not report.get('errors')
        else:
            pres = report.get('preservation')
            out['structure_unchanged'] = _all_true(pres) if pres is not None else None
        for name, row in report['clips'].items():
            feet = row.get('idle_feet_fraction', row.get('idle_feet_stock_height_fraction'))
            out['clips'][name] = dict(models=row.get('models', {}), matrix_delta=row.get('hand_head_motion_matrix_delta'),
                                      attachment_error=None, feet_fraction=feet)
            if 'layer_crossings' in row and 'converted' in out['clips'][name]['models']:
                out['clips'][name]['models']['converted'].setdefault('layer_crossings', row['layer_crossings'])
    return out


def _all_true(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        bools = [v for v in value.values() if isinstance(v, bool)]
        return all(bools) if bools else None
    return None


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def evaluate(norm, require_layers=False):
    """Apply the numeric rules to a normalised report. Missing data is a failure, never a pass.

    require_layers: the report must carry the layer-crossing check (MUA1/MUA2 tiers always do; the
    XML2 and community validators do not measure it yet, so it is checked only where present)."""
    failures = []
    layer = norm.get('layer_check')
    if layer is None:
        if require_layers:
            failures.append('layer-crossing check not measured')
    elif layer.get('bind_crossings') != 0:
        failures.append(f"bind: converted layer crossings ({layer.get('bind_crossings')})")
    if norm.get('structure_unchanged') is not True:
        failures.append('port skeleton/palette/bounds not proven unchanged')
    for name in REFERENCE_CLIPS:
        row = norm['clips'].get(name)
        if row is None:
            failures.append(f'{name}: missing reference clip'); continue
        models = row.get('models') or {}
        for new, old in norm.get('pairs', PAIRS):
            if new not in models or old not in models:
                failures.append(f'{name}: {new}/{old} measurements missing'); continue
            for metric in METRICS:
                a, b = models[new].get(metric), models[old].get(metric)
                if not (_finite(a) and _finite(b)):
                    failures.append(f'{name}: non-finite {metric}')
                elif a > b:
                    failures.append(f'{name}: {new} {metric} exceeds stock ({a:.6g} > {b:.6g})')
            sep = models[new].get('max_seam_separation')
            if sep != 0:
                failures.append(f'{name}: {new} seam opens ({sep})')
        if layer is not None:
            crossings = (models.get('converted') or {}).get('layer_crossings')
            if crossings != 0:
                failures.append(f'{name}: converted layer crossings ({crossings})')
        if row.get('matrix_delta') != 0:
            failures.append(f'{name}: hand/head/motion matrix changed ({row.get("matrix_delta")})')
        err = row.get('attachment_error')
        if err is not None and (not _finite(err) or err > ATTACHMENT_EPS):
            failures.append(f'{name}: attachment coordinates changed')
    feet = (norm['clips'].get('idle') or {}).get('feet_fraction')
    if not _finite(feet) or feet > FEET_LIMIT:
        failures.append(f'idle: feet differ by more than 2% of stock height ({feet})')
    return {'numeric_pass': not failures, 'failures': failures, 'idle_feet_fraction': feet,
            'layer_check': 'not measured' if layer is None else {'pairs': layer.get('pairs'), 'bind_crossings': layer.get('bind_crossings')}}


def check_report(path, candidate=None, require_layers=False):
    """Load a validation report, re-apply the rules, and bind it to the candidate's exact bytes."""
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    norm = normalize(report)
    result = evaluate(norm, require_layers)
    result['kind'] = norm['kind']
    if candidate is not None:
        digest = sha256(candidate)
        result['candidate_sha256'] = digest
        if norm['candidate_sha256'] and norm['candidate_sha256'] != digest:
            result['numeric_pass'] = False
            result['failures'].append('validation report is for a different file (hash mismatch)')
        elif not norm['candidate_sha256']:
            result['numeric_pass'] = False
            result['failures'].append('validation report does not name the candidate hash')
    return result


def texture_only_gate(invariant):
    """Sharpened tier: mesh, skeleton, bind, bounds and every non-texture byte identical (upscale.rewrite
    proves this exactly), so the deformation measurements equal stock's by construction."""
    failures = [k for k in ('nontexture_fields_and_blocks_identical', 'mesh_weights_bind_bounds_identical')
                if invariant.get(k) is not True]
    if invariant.get('hand_head_motion_matrix_delta') != 0:
        failures.append('hand_head_motion_matrix_delta')
    return {'numeric_pass': not failures, 'failures': failures, 'kind': 'texture-only'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
