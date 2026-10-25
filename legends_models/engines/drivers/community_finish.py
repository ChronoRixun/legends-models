# SPDX-License-Identifier: MIT
"""Driver: models-community finishing steps on one candidate, unchanged functions:
fix_mip_stride.fix_candidate (block-row pitch only), then the round-2 validator, bound to the
final file's hash. Run with cwd=<work>/community."""
import argparse, json, os, sys
sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
from pathlib import Path
import community_convert  # noqa: E402,F401  installs the community model reader as `model`
import validate  # noqa: E402  (round2 validator)
from fix_mip_stride import fix_candidate  # noqa: E402

ap = argparse.ArgumentParser()
for k in ('--port', '--source', '--candidate', '--animations', '--out-dir'):
    ap.add_argument(k, type=Path, required=True)
ap.add_argument('--db', required=True)
ap.add_argument('--target', required=True)
ap.add_argument('--claws', action='store_true')
ap.add_argument('--hide-segments', default='', help='comma list of stock sub-skin geometries to hide (zero primitives)')
a = ap.parse_args()
a.out_dir.mkdir(parents=True, exist_ok=True)
fixed, proof = fix_candidate(a.candidate.read_bytes())
if a.hide_segments:
    from hide_segments import hide  # noqa: E402
    fixed, hidden = hide(fixed, set(a.hide_segments.split(',')))
    proof['hidden_segments'] = hidden
final = a.out_dir / (a.target + '.IGB')
final.write_bytes(fixed)
(a.out_dir / 'metadata-proof.json').write_text(json.dumps(proof, indent=2))
v = validate.evaluate(a.port, a.source, final, a.animations, a.out_dir / 'validation.json', render_root=None,
                      animation_db=a.db, target_id=a.target, claws=a.claws)
# Bind the report to the exact final bytes (the research validator does not record the candidate hash).
import hashlib  # noqa: E402
rp = a.out_dir / 'validation.json'
rep = json.loads(rp.read_text(encoding='utf-8'))
rep['converted_sha256'] = hashlib.sha256(final.read_bytes()).hexdigest()
rp.write_text(json.dumps(rep, indent=2), encoding='utf-8')
print(json.dumps(v['acceptance']))
