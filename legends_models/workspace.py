# SPDX-License-Identifier: MIT
"""Per-port private work folders.

The research converters cache decoded animation clips and fits by file NAME, not content
(models-xml2 REPORT: "clear cache/animations when changing input installs"). So each port install
gets its own folder, and that folder is wiped when the port's relevant files change (a new
Legends Classic build): stale caches can never leak into a new build's gate.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import __version__, roster

ENGINE_DIRS = ('mua1', 'mua2', 'xml2', 'community')
SHARED_DBS = ('common.IGB', 'fightstyle_hero.IGB', 'fightstyle_finesse1.IGB', 'fightstyle_wrestling.IGB',
              'fightstyle_psionic.IGB', 'x1_03_wolverine.IGB')


def fingerprint(port):
    port = Path(port)
    names = [f'Actors/{h.skin}.IGB' for h in roster.HEROES] + [f'Actors/{h.animation_db}.IGB' for h in roster.HEROES]
    names += [f'Actors/{n}' for n in SHARED_DBS] + ['libIGSg.dll', 'Data/herostat.engb']
    h = hashlib.sha256()
    for n in sorted(set(names)):
        p = port / n
        try:
            st = p.stat()
            h.update(f'{n}|{st.st_size}|{st.st_mtime_ns}\n'.encode())
        except OSError:
            h.update(f'{n}|missing\n'.encode())
    return h.hexdigest()


def port_work_path(work, port):
    """<work>/ports/<id> for this port install (no reset; see port_work)."""
    port = Path(port).resolve()
    return Path(work) / 'ports' / hashlib.sha256(str(port).casefold().encode()).hexdigest()[:12]


def port_work(work, port):
    """<work>/ports/<id>, reset when the port's fingerprint (or the tool version) changes."""
    port = Path(port).resolve()
    root = port_work_path(work, port)
    stamp = root / 'port.json'
    want = dict(port=str(port), fingerprint=fingerprint(port), tool=__version__)
    have = None
    if stamp.is_file():
        try:
            have = json.loads(stamp.read_text(encoding='utf-8'))
        except ValueError:
            have = None
    if have != want:
        for d in ENGINE_DIRS:
            if (root / d).exists():
                shutil.rmtree(root / d)
        root.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps(want, indent=1), encoding='utf-8')
    return root
