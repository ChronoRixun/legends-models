# SPDX-License-Identifier: MIT
"""Which port build a pack was made for: a content fingerprint of the port files it depends on.

A pack folder replaces some of the port's files (Actors/<skin>.IGB, the heads/portraits) and was fitted to the
port's skeleton and animation database. The marker records the SHA-256 of each of those port files (plus
libIGSg.dll, whose animation sampler RVAs the converters rely on) and one fingerprint over them; `verify` hashes
them again and reports `stale` when a port rebuild changed any of them, so the launcher can offer Rebuild.
Content, not size/mtime: a rebuild that rewrites identical bytes is not stale, a same-size change is.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import roster

PACK_ONLY = {'mod.json', 'readme.txt', 'CREDITS.md', 'legends-models.json'}
ALWAYS = ('libIGSg.dll',)


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _find(port, rel):
    """The port file for a pack-relative path (the port's own spelling may differ in case)."""
    p = Path(port) / rel
    if p.is_file():
        return p
    folder = p.parent
    if folder.is_dir():
        for q in folder.iterdir():
            if q.name.casefold() == p.name.casefold() and q.is_file():
                return q
    return None


def builder_version(port):
    """The builder version the port says it was made with (_build/manifest.json), or None."""
    try:
        return json.loads((Path(port) / '_build' / 'manifest.json').read_text(encoding='utf-8')).get('builder')
    except (OSError, ValueError, AttributeError):
        return None


def dependencies(pack_files, hero_keys):
    """Port-relative paths a pack depends on: every game file it replaces, each hero's animation database and the
    engine DLL."""
    deps = {r for r in pack_files if r not in PACK_ONLY}
    for k in hero_keys:
        h = roster.BY_KEY.get(k)
        if h:
            deps |= {f'Actors/{h.skin}.IGB', f'Actors/{h.animation_db}.IGB'}
    return sorted(deps | set(ALWAYS))


def fingerprint(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f'{rel}|{files[rel]}\n'.encode())
    return h.hexdigest()


def record(port, pack_files, hero_keys):
    """{fingerprint, builder, files{rel: sha256 | None}} for the marker."""
    files = {}
    for rel in dependencies(pack_files, hero_keys):
        p = _find(port, rel)
        files[rel] = _sha(p) if p else None
    return dict(fingerprint=fingerprint(files), builder=builder_version(port), files=files)


def check(port, rec):
    """{stale, changed[], fingerprint, recorded} for a marker's port_build against the port as it is now.
    stale None when the marker has no record (a pack made before 0.3.0) or the port cannot be read."""
    if not rec or not rec.get('files'):
        return dict(stale=None, reason='the pack does not record its port build (made before legends-models 0.3.0)')
    if port is None or not Path(port).is_dir():
        return dict(stale=None, reason='port folder not found')
    now = {}
    for rel in rec['files']:
        p = _find(port, rel)
        now[rel] = _sha(p) if p else None
    changed = sorted(r for r in rec['files'] if rec['files'][r] != now[r])
    return dict(stale=bool(changed), changed=changed, fingerprint=fingerprint(now), recorded=rec.get('fingerprint'),
                builder=builder_version(port), recorded_builder=rec.get('builder'))
