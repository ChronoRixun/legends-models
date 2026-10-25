# SPDX-License-Identifier: MIT
"""Driver: copy actors/<id>.igz out of the player's MUA2 actors.bin into <work>/mua2/evidence/actors
(where convert_mua2.py expects it). Read-only on the install; never overwrites."""
import argparse, os, sys
sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
from pathlib import Path
from mua_bin import Archive  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--mua2', type=Path, required=True)
ap.add_argument('--id', required=True)
a = ap.parse_args()
dest = Path('evidence/actors') / (a.id + '.igz')
if not dest.exists():
    arc = Archive(a.mua2 / 'actors.bin')
    e = next((e for e in arc.entries if e[0].lower() == f'actors/{a.id}.igz'), None)
    if e is None:
        sys.exit(f'actors/{a.id}.igz not in actors.bin')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(arc.read(e))
print(dest)
