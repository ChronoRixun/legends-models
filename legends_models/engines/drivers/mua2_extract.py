# SPDX-License-Identifier: MIT
"""Refresh the private extracted actor from the owned archive; source install is read-only."""
import argparse, os, sys
sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
from pathlib import Path
from mua_bin import Archive  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--mua2', type=Path, required=True)
ap.add_argument('--id', required=True)
a = ap.parse_args()
dest = Path('evidence/actors') / (a.id + '.igz')
arc = Archive(a.mua2 / 'actors.bin')
e = next((e for e in arc.entries if e[0].lower() == f'actors/{a.id}.igz'), None)
if e is None:
    sys.exit(f'actors/{a.id}.igz not in actors.bin')
data = arc.read(e)
if not dest.is_file() or dest.read_bytes() != data:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix('.igz.tmp')
    temp.write_bytes(data)
    os.replace(temp, dest)
print(dest)
