# SPDX-License-Identifier: MIT
"""Find the player's installs (read-only) and report what each can supply per hero."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import community, roster
from .formats.mua_bin import Archive

MUA1_DIR = 'Marvel - Ultimate Alliance'
MUA2_DIR = 'Marvel - Ultimate Alliance 2'
PORT_GUESSES = [r'C:\Legends-classic', r'C:\Games\Legends-classic']
MUA1_LOOSE_ROOTS = ['', 'Data', 'Setup/Data', 'files/Setup/Data']   # as vendor/mua1/convert.py


@dataclass
class Install:
    kind: str
    path: Path | None = None
    flavour: str = ''           # mua1: '2006-loose' | '2016-bin'
    problems: list = field(default_factory=list)
    actors: set = field(default_factory=set)   # lower-case actor IDs present

    @property
    def found(self):
        return self.path is not None and not self.problems

    def as_dict(self):
        return dict(kind=self.kind, path=str(self.path) if self.path else None, flavour=self.flavour,
                    found=self.found, problems=self.problems, actor_count=len(self.actors))


def steam_libraries(steam_root=None):
    roots = []
    for base in filter(None, [steam_root, os.environ.get('LEGENDS_STEAM'),
                              r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam']):
        vdf = Path(base) / 'steamapps' / 'libraryfolders.vdf'
        if vdf.is_file():
            roots.append(Path(base) / 'steamapps')
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8', errors='replace')):
                roots.append(Path(m.group(1).replace('\\\\', '\\')) / 'steamapps')
    roots += _drive_guesses()
    seen, out = set(), []
    for r in roots:
        k = str(r).casefold()
        if k not in seen and r.is_dir():
            seen.add(k)
            out.append(r)
    return out


def _drive_guesses():
    """Libraries Steam's own list may not name (copied or per-game libraries): X:\\*\\steamapps and
    X:\\*\\*\\steamapps on each drive. Directory listings only."""
    out = []
    if os.name != 'nt':
        return out
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        root = Path(f'{letter}:\\')
        try:
            if not root.exists():
                continue
            for a in root.iterdir():
                if not a.is_dir() or a.name.startswith(('$', '.')) or a.name in ('Windows', 'ProgramData'):
                    continue
                if (a / 'steamapps').is_dir():
                    out.append(a / 'steamapps')
                try:
                    for b in a.iterdir():
                        if (b / 'steamapps' / 'common').is_dir():
                            out.append(b / 'steamapps')
                except OSError:
                    pass
        except OSError:
            continue
    return out


def _guess(dirname, libraries):
    for lib in libraries:
        p = lib / 'common' / dirname
        if p.is_dir():
            return p
    return None


def scan_port(path=None):
    inst = Install('port')
    path = path or os.environ.get('LEGENDS_PORT') or next((p for p in PORT_GUESSES if Path(p).is_dir()), None)
    if not path:
        inst.problems.append('Legends Classic port not found (pass --port)')
        return inst
    inst.path = Path(path)
    actors = inst.path / 'Actors'
    if not actors.is_dir():
        inst.problems.append(f'no Actors folder in {inst.path}')
        return inst
    inst.actors = {p.stem.lower() for p in actors.iterdir() if p.suffix.lower() == '.igb'}
    missing = [h.key for h in roster.HEROES if h.skin not in inst.actors]
    if missing:
        inst.problems.append('not a Legends Classic build (missing stock skins for ' + ', '.join(missing) + ')')
    if not (inst.path / 'libIGSg.dll').is_file():
        inst.problems.append('libIGSg.dll missing (the offline animation sampler needs it)')
    return inst


def scan_mua1(path=None, libraries=()):
    inst = Install('mua1')
    path = Path(path) if path else _guess(MUA1_DIR, libraries)
    if not path:
        inst.problems.append('MUA1 not found (optional; pass --mua1)')
        return inst
    inst.path = path
    for rel in MUA1_LOOSE_ROOTS:
        d = path / rel / 'actors' if rel else path / 'actors'
        if d.is_dir() and any(d.glob('*.igb')):
            inst.flavour = '2006-loose'
            inst.actors = {p.stem.lower() for p in d.glob('*.igb')}
            return inst
    if (path / 'actors.bin').is_file():
        inst.flavour = '2016-bin'
        try:
            inst.actors = _bin_actors(path / 'actors.bin', '.igb')
        except (OSError, ValueError) as e:
            inst.problems.append(f'actors.bin unreadable: {e}')
        return inst
    inst.problems.append(f'no MUA1 actors (loose actors/*.igb or actors.bin) under {path}')
    return inst


def scan_mua2(path=None, libraries=()):
    inst = Install('mua2')
    path = Path(path) if path else _guess(MUA2_DIR, libraries)
    if not path:
        inst.problems.append('MUA2 not found (optional; pass --mua2)')
        return inst
    inst.path = path
    for need in ('actors.bin', 'materials.bin', 'textures.bin'):
        if not (path / need).is_file():
            inst.problems.append(f'{need} missing in {path}')
    if not inst.problems:
        inst.flavour = 'pc-bin'
        try:
            inst.actors = _bin_actors(path / 'actors.bin', '.igz')
        except (OSError, ValueError) as e:
            inst.problems.append(f'actors.bin unreadable: {e}')
    return inst


def _bin_actors(path, suffix):
    out = set()
    for name, *_ in Archive(path).entries:
        n = name.lower()
        if n.startswith('actors/actors/') or n.count('/') == 1:
            stem = n.rsplit('/', 1)[-1]
            if stem.endswith(suffix):
                out.add(stem[:-len(suffix)])
    return out


def scan_community(cache):
    data = community.manifest()
    have = {}
    for rec in data['archives']:
        p = Path(cache) / 'archives' / f'{rec["sha256"][:16]}-{rec["archive"]}'
        have[(rec['character'], rec['costume'])] = p.is_file() and p.stat().st_size == rec['bytes']
    return have


def helpers(work):
    from .engines import engine_modules, python_exe, sampler_path
    mods = engine_modules()
    s = sampler_path(work)
    ex = community.find_extractor()
    return dict(python=python_exe(), python_modules=mods, sampler=str(s) if s else None, extractor=ex[0] if ex else None)


def availability(inst, tier, hero, recipe, source=None):
    """(ok, reason) for one hero/tier (and costume `source`, default the recipe's) given the scanned installs."""
    source = source if source is not None else (recipe.source if recipe else None)
    if recipe is None or recipe.status not in roster.USABLE:
        return False, roster.unavailable_reason(hero, tier)
    port = inst['port']
    if not port.found:
        return False, 'port not found'
    if tier == 'sharpen':
        return True, recipe.player_line
    if tier == 'xml2':
        ok = source.lower() in port.actors
        return ok, recipe.player_line if ok else 'Selected XML2 model is missing from the port'
    if tier in ('mua1', 'mua2'):
        g = inst[tier]
        if not g.found:
            return False, (g.problems[0] if g.problems else f'{tier} not found')
        ok = source.lower() in g.actors
        return ok, recipe.player_line if ok else f'Selected {tier.upper()} costume is missing from your game'
    if tier == 'community':
        return True, recipe.player_line
    return False, 'unknown tier'


def scan(args_port=None, args_mua1=None, args_mua2=None, steam=None):
    libs = steam_libraries(steam)
    return {'port': scan_port(args_port), 'mua1': scan_mua1(args_mua1, libs), 'mua2': scan_mua2(args_mua2, libs),
            'libraries': [str(x) for x in libs]}
