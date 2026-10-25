# SPDX-License-Identifier: MIT
"""Mod pack folders owned by legends-models: stage, publish atomically, remove, verify.

A pack is <game>\\mods\\<Pack>\\ laid out like the game folder (Actors\\<skin>.IGB ...), plus
legends-models.json (ownership marker + per-file SHA-256 + per-character provenance), CREDITS.md and
readme.txt. Staging happens in a '.'-prefixed sibling folder, which the mod loader never loads, and
is renamed into place only when complete; a cancelled or failed build leaves the previous pack and
load-order.txt untouched. legends-models never deletes or replaces a folder without its marker.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import __version__, loadorder
from .errors import UNSAFE, VERIFY_MISMATCH, ToolError

MARKER = 'legends-models.json'
GENERATOR = 'legends-models'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def file_hashes(folder, skip=(MARKER,)):
    folder = Path(folder)
    out = {}
    for p in sorted(folder.rglob('*')):
        if p.is_file():
            rel = p.relative_to(folder).as_posix()
            if rel not in skip:
                out[rel] = {'sha256': sha256(p), 'bytes': p.stat().st_size}
    return out


def read_marker(folder):
    p = Path(folder) / MARKER
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if data.get('generator') == GENERATOR else None


def group_of(folder):
    m = read_marker(folder)
    return None if m is None else m.get('group', Path(folder).name)


def owned_packs(mods_dir):
    mods_dir = Path(mods_dir)
    if not mods_dir.is_dir():
        return []
    return sorted(p.name for p in mods_dir.iterdir() if p.is_dir() and not p.name.startswith('.') and read_marker(p))


def check_output_location(game, sources):
    """The pack goes under <game>\\mods; never inside a source install we read."""
    game = Path(game).resolve()
    for s in sources:
        if s is None:
            continue
        s = Path(s).resolve()
        if s != game and (game.is_relative_to(s) or s.is_relative_to(game / 'mods')):
            raise ToolError(f'output {game} overlaps read-only source {s}', UNSAFE)
    return game / 'mods'


class Staging:
    """with Staging(mods_dir, name) as st: write into st.path; st.publish(...) at the end."""

    def __init__(self, mods_dir, name):
        loadorder._check_name(name)
        self.mods = Path(mods_dir)
        self.name = name
        self.final = self.mods / name
        self.path = self.mods / f'.legends-models-staging-{name}-{os.getpid()}-{int(time.time())}'
        self.published = False

    def __enter__(self):
        if self.final.exists() and read_marker(self.final) is None:
            raise ToolError(f'{self.final} exists and is not a legends-models pack; refusing to replace it', UNSAFE)
        self.mods.mkdir(parents=True, exist_ok=True)
        for stale in self.mods.glob(f'.legends-models-staging-{self.name}-*'):
            shutil.rmtree(stale, ignore_errors=True)
        self.path.mkdir()
        return self

    def __exit__(self, *exc):
        if not self.published and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        return False

    def add(self, rel, src):
        dest = self.path / rel
        if not dest.resolve().is_relative_to(self.path.resolve()):
            raise ToolError(f'pack path escapes the pack: {rel}', UNSAFE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def write_text(self, rel, text):
        dest = self.path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')

    def publish(self, mode, characters, extra=None, enable=True, disable_others=True):
        marker = dict(generator=GENERATOR, version=__version__, pack=self.name, mode=mode,
                      created=time.strftime('%Y-%m-%dT%H:%M:%S%z'), characters=characters,
                      distribution='Generated on this PC from games you own. Do not share or upload.',
                      files=file_hashes(self.path), **(extra or {}))
        (self.path / MARKER).write_text(json.dumps(marker, indent=1), encoding='utf-8')
        old = None
        if self.final.exists():
            old = self.mods / f'.legends-models-old-{self.name}-{os.getpid()}'
            os.replace(self.final, old)
        try:
            os.replace(self.path, self.final)
        except OSError:
            if old is not None:
                os.replace(old, self.final)
            raise
        self.published = True
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        text = loadorder.read(self.mods)
        if disable_others:
            text = disable_other_groups(self.mods, text, (extra or {}).get('group', self.name))
        text = loadorder.set_entry(text, self.name, enabled=enable)
        loadorder.write(self.mods, text)
        return marker


def check_same_group(mods_dir, name, group):
    """A per-hero folder is only ever replaced by a build of its own group (exit 40 otherwise)."""
    g = group_of(Path(mods_dir) / name) if (Path(mods_dir) / name).exists() else None
    if g is not None and g.casefold() != group.casefold():
        raise ToolError(f'{name} belongs to the {g} group; refusing to take it over for {group}', UNSAFE)


def disable_other_groups(mods_dir, text, group):
    """One appearance at a time: every legends-models folder of another group is set to '-'."""
    for other in owned_packs(mods_dir):
        g = group_of(Path(mods_dir) / other)
        if g.casefold() != group.casefold() and loadorder.is_enabled(text, other) is not False:
            text = loadorder.set_entry(text, other, enabled=False)
    return text


def members(mods_dir, name):
    """Folders a --pack name refers to: the folder itself (if owned) and every owned folder of that group."""
    mods_dir = Path(mods_dir)
    out = [f for f in owned_packs(mods_dir) if f.casefold() == name.casefold() or
           (group_of(mods_dir / f) or '').casefold() == name.casefold()]
    return sorted(set(out))


def remove(mods_dir, name):
    """Remove a pack folder, or every folder of a per-hero pack group, and their load-order lines."""
    mods_dir = Path(mods_dir)
    loadorder._check_name(name)
    group = [f for f in members(mods_dir, name) if f.casefold() != name.casefold()]
    results = [remove_folder(mods_dir, f) for f in group]
    single = remove_folder(mods_dir, name)
    if not group:
        return single
    return dict(pack=name, folder_removed=single['folder_removed'] or any(r['folder_removed'] for r in results),
                load_order_entry_removed=single['load_order_entry_removed'] or any(r['load_order_entry_removed'] for r in results),
                folders=[r['pack'] for r in results if r['folder_removed']])


def remove_folder(mods_dir, name):
    mods_dir = Path(mods_dir)
    loadorder._check_name(name)
    folder = mods_dir / name
    removed = False
    if folder.exists():
        if read_marker(folder) is None:
            raise ToolError(f'{folder} is not a legends-models pack; refusing to delete it', UNSAFE)
        trash = mods_dir / f'.legends-models-removing-{name}-{os.getpid()}'
        os.replace(folder, trash)
        shutil.rmtree(trash)
        removed = True
    text = loadorder.read(mods_dir)
    had_entry = loadorder.is_enabled(text, name) is not None
    if had_entry:
        loadorder.write(mods_dir, loadorder.remove_entry(text, name))
    return dict(pack=name, folder_removed=removed, load_order_entry_removed=had_entry)


def verify_group(mods_dir, name, manifest=None):
    """verify() for a pack folder, or for every folder of a per-hero group (aggregated)."""
    mods_dir = Path(mods_dir)
    group = [f for f in members(mods_dir, name) if f.casefold() != name.casefold()]
    if not group or manifest is not None:
        return verify(mods_dir / name, manifest)
    rows = [verify(mods_dir / f) for f in group]
    pref = lambda r, k: [f'{r["pack"]}/{x}' for x in r[k]]
    return dict(pack=name, ok=all(r['ok'] for r in rows), files=sum(r['files'] for r in rows), folders=group,
                missing=sum((pref(r, 'missing') for r in rows), []), extra=sum((pref(r, 'extra') for r in rows), []),
                changed=sum((pref(r, 'changed') for r in rows), []))


def verify(folder, manifest=None):
    """Compare a pack's files with its marker (or an external manifest: a marker file or {rel: sha})."""
    folder = Path(folder)
    marker = read_marker(folder)
    if manifest is not None:
        data = json.loads(Path(manifest).read_text(encoding='utf-8'))
        expected = data.get('files', data)
    elif marker is not None:
        expected = marker['files']
    else:
        raise ToolError(f'{folder} has no legends-models marker and no manifest was given', VERIFY_MISMATCH)
    expected = {k: (v['sha256'] if isinstance(v, dict) else v) for k, v in expected.items()}
    actual = {k: v['sha256'] for k, v in file_hashes(folder).items()} if folder.is_dir() else {}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
    return dict(pack=folder.name, ok=not (missing or extra or changed), files=len(expected),
                missing=missing, extra=extra, changed=changed)
