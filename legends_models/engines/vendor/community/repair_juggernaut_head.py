"""Juggernaut: the community head+hood is bound rigidly to Spine2 at source scale, so after the fit it is ~1.3x too small
for the port's larger skeleton and sinks into the collar (the port's own head/helmet are separate, larger segments).
Scale the head/hood vertex set about its base, keep weights, then run the unchanged converter + validator."""
import json, sys, os
from pathlib import Path
import numpy as np
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'round2'))
ROOT = HERE.parent
import community_convert
from community_model import Model

def head_set(source, band=(62.5, 66.0), halfwidth=14.0):
    z = source.pos[:, 2]
    names = np.array(source.names)
    dominant = names[source.bone_indices[:, 0]]
    ok = np.isin(dominant, ['Bip01 Spine2', 'Bip01 Head', 'Bip01 Neck']) & (np.abs(source.pos[:, 1]) < halfwidth) & (z > band[0])
    w = np.clip((z - band[0]) / (band[1] - band[0]), 0, 1) * ok
    return w

def main(scale=1.5, tag='final', lift=5.0):
    import validate
    port = Path(os.environ['LEGENDS_PORT'])
    old = ROOT / 'output/juggernaut/classic'  # written by community_convert.py for this hero
    conv = json.loads((old / 'conversion.json').read_text())
    source_path = ROOT / conv['source']
    template = port / 'Actors/17401.IGB'
    source, target = Model(source_path), Model(template)
    fit = {k: np.array(v, copy=True) for k, v in dict(np.load(old / 'fit.npz')).items()}
    w = head_set(source)
    sel = w > 0
    base = fit['pos'][sel & (w >= 0.99)]
    pivot = np.array([base[:, 0].mean(), 0.0, fit['pos'][np.flatnonzero(sel)][:, 2].min()])
    pos = fit['pos'].copy()
    factor = 1 + (scale - 1) * w
    pos = pivot + (pos - pivot) * factor[:, None]
    pos[:, 2] += lift * w
    fit['pos'] = pos
    animations = old / 'animations'
    db = json.loads((animations / 'reference-map.json').read_text())['hero_db']
    out = ROOT / 'output/juggernaut-head' / tag
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    cand = out / '17401.IGB'
    summary = community_convert.convert_candidate(source_path, template, cand, fit)
    v = validate.evaluate(port, source_path, cand, animations, out / 'validation.json', render_root=None,
                          animation_db=db, target_id='17401', claws=False)
    (out / 'repair.json').write_text(json.dumps({'scale': scale, 'lift': lift, 'pivot': pivot.tolist(), 'moved_vertices': int(sel.sum()),
                                                  'acceptance': v['acceptance'], 'conversion': summary}, indent=2))
    print(tag, scale, int(sel.sum()), v['acceptance'])

if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 1.5, sys.argv[2] if len(sys.argv) > 2 else 'final', float(sys.argv[3]) if len(sys.argv) > 3 else 5.0)
