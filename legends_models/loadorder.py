# SPDX-License-Identifier: MIT
r"""Edit <game>\mods\load-order.txt the way the XML2 Fix mod loader reads it (src/mod_order.hpp):

  "+Name" enabled, "-Name" disabled (kept for its place), '#' comments, blank/other lines skipped,
  UTF-8 with an optional BOM, trailing spaces/tabs/CR trimmed, later lines win, names compared
  case-insensitively, unlisted folders load after the listed ones (alphabetically, enabled),
  folders starting with '.' never load.

The loader takes the name as everything after the sign, so "+ Name" names " Name". Older research
tools wrote that form; it is recognised here (and rewritten as "+Name") so their entries can be
replaced or removed. Every other line is preserved byte-for-byte.
"""
from __future__ import annotations

import os
from pathlib import Path

BOM = '\ufeff'


def _split(text):
    bom = text.startswith(BOM)
    if bom:
        text = text[1:]
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines()
    return bom, newline, lines


def parse_line(line):
    """(sign, name) for an entry line the loader would read, else None. name keeps the loader's exact
    spelling (a leading space stays: that is what the game looks for)."""
    s = line.rstrip(' \t\r')
    if len(s) < 2 or s[0] not in '+-':
        return None
    return s[0], s[1:]


def _same(a, b):
    return a.strip().casefold() == b.strip().casefold()


def entries(text):
    """[(name, enabled)] as the loader reads them (exact names)."""
    _, _, lines = _split(text)
    out = []
    for line in lines:
        p = parse_line(line)
        if p:
            out.append((p[1], p[0] == '+'))
    return out


def effective_order(text, folders):
    """Enabled mod names in load order given the folder names present (loader semantics)."""
    listed = entries(text)
    present = {f.casefold(): f for f in folders if f and not f.startswith('.')}
    order = []
    for name, enabled in listed:
        if enabled and name.casefold() in present:
            order.append(present[name.casefold()])
    listed_names = {n.casefold() for n, _ in listed}
    order += sorted((f for k, f in present.items() if k not in listed_names), key=str.casefold)
    return order


def set_entry(text, name, enabled=True):
    """Set `name` enabled/disabled. Keeps its position if listed (first match; duplicates dropped),
    else appends. Legacy "+ Name" spellings are matched and normalised."""
    _check_name(name)
    bom, nl, lines = _split(text)
    out, placed = [], False
    for line in lines:
        p = parse_line(line)
        if p and _same(p[1], name):
            if not placed:
                out.append(('+' if enabled else '-') + name)
                placed = True
            continue
        out.append(line)
    if not placed:
        out.append(('+' if enabled else '-') + name)
    return (BOM if bom else '') + nl.join(out) + nl


def rename_entry(text, old, new):
    """Rename a listed folder in place (same line, same sign); not listed -> unchanged."""
    _check_name(new)
    bom, nl, lines = _split(text)
    out = []
    for line in lines:
        p = parse_line(line)
        out.append(p[0] + new if p and _same(p[1], old) else line)
    return (BOM if bom else '') + (nl.join(out) + nl if out else '')


def remove_entry(text, name):
    bom, nl, lines = _split(text)
    out = [line for line in lines if not ((p := parse_line(line)) and _same(p[1], name))]
    return (BOM if bom else '') + (nl.join(out) + nl if out else '')


def is_enabled(text, name):
    state = None
    for n, enabled in entries(text):
        if _same(n, name):
            state = enabled if state is None else state
    return state


def _check_name(name):
    if not name or name != name.strip() or name.startswith('.') or any(c in name for c in '\\/:*?"<>|\r\n\t'):
        raise ValueError(f'unsafe mod folder name {name!r}')


def read(mods_dir):
    p = Path(mods_dir) / 'load-order.txt'
    return p.read_text(encoding='utf-8') if p.exists() else ''


def write(mods_dir, text):
    """Atomic replace (write a sibling temp file, then os.replace)."""
    mods_dir = Path(mods_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)
    target = mods_dir / 'load-order.txt'
    tmp = mods_dir / '.load-order.txt.legends-models.tmp'
    tmp.write_bytes(text.encode('utf-8'))
    os.replace(tmp, target)
    return target
