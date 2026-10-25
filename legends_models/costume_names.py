# SPDX-License-Identifier: MIT
"""Costume display names from each game's own data (herostat / npcstat), read-only.

MUA1 (2006 loose `data/*.engb` or 2016 `data.bin`): `herostat` stats rows carry `skin="0301"` (the first
costume) plus `skin_NN="02"` / `skin_NN_name="Classic"` pairs; the name of the first costume is
`skin_01_name`. `npcstat` rows are story NPC models (`skin="4701"`, `charactername`).
MUA2 (`data.bin`): one file per character, `data/stats/heroes/<name>.xmlb` and `data/stats/npcs/<name>.xmlb`,
same attributes as MUA1.
XML2 (`Data/herostat.engb`): `skin="0303"` is the default costume, alternates are `skin_<style>="NN"`
(`skin_astonishing="01"`, `skin_aoa="02"`, ...); `npcstat` as above.

Every reader returns {actor_id: {name, character, kind}} with kind 'hero' (a selectable costume) or 'npc'.
`data/costume-names.json` is the same data for the IDs the roster offers, made by
tools/derive_costume_names.py from the games' files, so a scan without the install still has a name.
"""
from __future__ import annotations

import json
import struct
from functools import lru_cache
from pathlib import Path

from .formats import xmlb
from .formats.mua_bin import Archive

DATA = Path(__file__).parent / 'data' / 'costume-names.json'
XML2_STYLES = {'astonishing': 'Astonishing', 'aoa': 'Age of Apocalypse', '60s': "'60s", '70s': "'70s",
               'future': 'Future', 'winter': 'Winter', 'civilian': 'Civilian', 'weaponx': 'Weapon X'}
XML2_DEFAULT = 'Legends II default'


class _Row(dict):
    """One `stats` node's attributes (dict with .attrib / .get like an ElementTree element)."""

    @property
    def attrib(self):
        return self


def _rows(data):
    try:
        return [_Row(a) for tag, a in xmlb.elements(data) if tag == 'stats']
    except (ValueError, IndexError, struct.error):  # an unreadable table just yields no names
        return []


def _mua_stats(rows, kind, out):
    """MUA1/MUA2 rows -> out[id] (hero costumes win over NPC rows of the same model)."""
    for s in rows:
        skin = s.get('skin', '')
        if not (skin.isdigit() and len(skin) >= 4):
            continue
        character = s.get('charactername') or s.get('name') or ''
        if kind == 'npc':
            out.setdefault(skin, dict(name=character, character=character, kind='npc'))
            continue
        prefix = skin[:-2]
        out[skin] = dict(name=s.get('skin_01_name') or 'Default', character=character, kind='hero')
        for key, value in s.attrib.items():
            if key.startswith('skin_') and not key.endswith('_name') and value.isdigit():
                name = s.get(key + '_name')
                if name:
                    out[prefix + value.zfill(2)] = dict(name=name, character=character, kind='hero')


def _xml2_stats(rows, kind, out):
    for s in rows:
        skin = s.get('skin', '')
        if not (skin.isdigit() and len(skin) >= 4):
            continue
        character = s.get('charactername') or s.get('name') or ''
        if kind == 'npc':
            out.setdefault(skin, dict(name=character, character=character, kind='npc'))
            continue
        prefix = skin[:-2]
        out[skin] = dict(name=XML2_DEFAULT, character=character, kind='hero')
        for key, value in s.attrib.items():
            style = key[5:] if key.startswith('skin_') else ''
            if style and value.isdigit():
                out[prefix + value.zfill(2)] = dict(name=XML2_STYLES.get(style, style.title()), character=character,
                                                    kind='hero')


def _merge(npcs, heroes):
    out = dict(npcs)
    out.update(heroes)
    return out


def read_mua1(path):
    """MUA1 2006 loose files or 2016 data.bin under `path` (the install folder)."""
    path = Path(path)
    for rel in ('', 'Data', 'Setup/Data', 'files/Setup/Data'):
        d = (path / rel if rel else path) / 'data'
        if (d / 'herostat.engb').is_file():
            heroes, npcs = {}, {}
            _mua_stats(_rows((d / 'herostat.engb').read_bytes()), 'hero', heroes)
            if (d / 'npcstat.engb').is_file():
                _mua_stats(_rows((d / 'npcstat.engb').read_bytes()), 'npc', npcs)
            return _merge(npcs, heroes)
    if (path / 'data.bin').is_file():
        a = Archive(path / 'data.bin')
        entries = {e[0].lower(): e for e in a.entries}
        heroes, npcs = {}, {}
        for name, kind, into in (('data/herostat.engb', 'hero', heroes), ('data/npcstat.engb', 'npc', npcs)):
            if name in entries:
                _mua_stats(_rows(a.read(entries[name])), kind, into)
        return _merge(npcs, heroes)
    return {}


def read_mua2(path):
    """MUA2 data.bin under `path`: data/stats/heroes/*.xmlb and data/stats/npcs/*.xmlb."""
    path = Path(path)
    if not (path / 'data.bin').is_file():
        return {}
    a = Archive(path / 'data.bin')
    heroes, npcs = {}, {}
    for e in a.entries:
        n = e[0].lower()
        if n.startswith('data/stats/heroes/') and n.endswith('.xmlb'):
            _mua_stats(_rows(a.read(e)), 'hero', heroes)
        elif n.startswith('data/stats/npcs/') and n.endswith('.xmlb'):
            _mua_stats(_rows(a.read(e)), 'npc', npcs)
    return _merge(npcs, heroes)


def read_xml2(path):
    """X-Men Legends II PC install (`Data/herostat.engb`, `Data/npcstat.engb`)."""
    d = Path(path) / 'Data'
    heroes, npcs = {}, {}
    if (d / 'herostat.engb').is_file():
        _xml2_stats(_rows((d / 'herostat.engb').read_bytes()), 'hero', heroes)
    if (d / 'npcstat.engb').is_file():
        _xml2_stats(_rows((d / 'npcstat.engb').read_bytes()), 'npc', npcs)
    return _merge(npcs, heroes)


READERS = {'mua1': read_mua1, 'mua2': read_mua2, 'xml2': read_xml2}


@lru_cache(maxsize=None)
def table():
    """The shipped names: {tier: {id: {name, character, kind}}}."""
    try:
        return json.loads(DATA.read_text(encoding='utf-8'))['names']
    except (OSError, ValueError, KeyError):
        return {}


def from_installs(inst):
    """{tier: {id: row}} read from the scanned installs (empty for a game that is missing or unreadable)."""
    out = {}
    for tier in ('mua1', 'mua2'):
        i = inst.get(tier) if isinstance(inst, dict) else None
        if i is None or not getattr(i, 'path', None):
            continue
        try:
            out[tier] = READERS[tier](i.path)
        except (OSError, ValueError):
            out[tier] = {}
    return out


def lookup(tier, costume, derived=None):
    """The game's row for this actor ID: the player's own install first, then the shipped table."""
    row = ((derived or {}).get(tier) or {}).get(costume)
    return row or table().get(tier, {}).get(costume)


def label(hero_name, tier, costume, derived=None):
    """Short display name from the game's data, or None when the game does not name this ID."""
    row = lookup(tier, costume, derived)
    if not row:
        return None
    if row['kind'] == 'hero':
        return row['name']
    # Story NPC model: its own character name when it is a different character (Dark Psylocke),
    # otherwise say what it is.
    base = hero_name.split(' (')[0].casefold()
    character = (row.get('character') or row.get('name') or '').strip()
    if character and character.casefold() != base and base not in character.casefold().split(' ('):
        return character
    return 'Story character model'
