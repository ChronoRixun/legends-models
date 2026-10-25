# SPDX-License-Identifier: MIT
"""Conversation heads, HUD heads and select portraits: where the port reads them and how a pack supplies them.

Every hero has three static UI scenes keyed by its skin ID (x1names.py / characters.py in the builder
rename the XML1 files to these names; the final generated packages already list all three, so a
replacement at the same path needs no package or data edit):

  role     file the port reads                      what shows it
  hud      HUD/hud_head_<skin>.IGB                  conversation portrait (the speaker's circle)
  hud3d    UI/hud/characters/<skin>.IGB             the 3D head of the in-game health panel
  select   UI/models/characters/<prefix>01.IGB      character select / team roster portrait

<skin> is the hero's base actor skin (roster.Hero.skin). The engine looks a head up as the current costume,
then the default costume, then variant 01 (XMen2.exe 0x5f4ec0), so a pack replaces the base-costume file only;
alternate costumes keep their stock heads. The select portrait is per character (variant 01): skin[:-2] + '01',
which is also right for Professor X (astral skin 15104, portrait 15101).

Sources: the community pack ships all three roles (BaconWizard17's files are used as they are, only renamed
to the port's paths); MUA1 supplies the conversation portrait and the select portrait as a texture swap into
the port's own scene (mesh and UVs byte-identical); MUA1's 3D HUD head is a different mesh (UV atlas) and
MUA2 keeps its portraits in a UI atlas, so those roles stay stock.
"""
from __future__ import annotations

import re
import shutil
import struct
from pathlib import Path

from . import community, roster
from .engines import VENDOR, materialise, run
from .errors import MISSING_INPUT, ToolError

MAGIC = 0xFADA
ROLES = ('hud', 'hud3d', 'select')


def portrait_id(hero):
    return hero.skin[:-2] + '01'


def port_paths(hero):
    """Pack-relative path of each role for this hero."""
    return {'hud': f'HUD/hud_head_{hero.skin}.IGB',
            'hud3d': f'UI/hud/characters/{hero.skin}.IGB',
            'select': f'UI/models/characters/{portrait_id(hero)}.IGB'}


def igb_header(path):
    """(magic, version) of an IGB file, or None if it is not one."""
    try:
        with open(path, 'rb') as f:
            head = f.read(48)
    except OSError:
        return None
    if len(head) < 48:
        return None
    magic = int.from_bytes(head[40:44], 'little')
    version = int.from_bytes(head[44:48], 'little')
    return (magic, version) if magic == MAGIC else None


def check_head_file(path, max_bytes=2_000_000):
    """A head must be a real IGB (v4 community, v6 port) of sane size; anything else is not installed."""
    p = Path(path)
    if not p.is_file() or not 0 < p.stat().st_size <= max_bytes:
        return 'missing or oversized'
    h = igb_header(p)
    if h is None:
        return 'not an IGB file'
    if h[1] not in (0x80000004, 0xF0000006):
        return f'unexpected IGB version {h[1]:#x}'
    return None


def rename_ids(data, prefix, new):
    """Rename the IGB string fields that name this head after its source character ('0501', '0501_outline',
    '0501_skel' with the archive's two-digit prefix) to the port's ID, as the port builder does for the
    files it imports (igb_rename in tools/xml1build/lib/x1names.py). IGB serialises a string as
    <u32 padded length><bytes, NUL, zero padded to 4>, so a name that still fits its padded length is
    renamed in place and no offset moves. Returns (bytes, replacements, problems)."""
    buf = bytearray(data)
    count, problems = 0, []
    pattern = re.compile(re.escape(prefix.encode()) + rb'\d\d(_outline|_skel)?\x00')
    for m in list(pattern.finditer(data)):
        start = m.start()
        if start < 4:
            continue
        (plen,) = struct.unpack_from('<I', data, start - 4)
        body = m.group(0)[:-1]
        if plen % 4 or plen < len(body) + 1 or plen > len(body) + 4:
            continue                                      # not a length-prefixed string field
        repl = new.encode() + (m.group(1) or b'')
        if len(repl) + 1 > plen:
            problems.append((start, body.decode(), plen))
            continue
        buf[start:start + plen] = repl + b'\x00' * (plen - len(repl))
        count += 1
    return bytes(buf), count, problems


def community_routes(folder, hero, near=None, out_dir=None):
    """Pack path -> source file for the community archive's head files. The villain-border conversation
    portrait is never used for a hero. With several candidates (Magma ships one set per platform) the files
    next to the chosen skin (`near`) win."""
    paths = port_paths(hero)
    found = {role: [] for role in ROLES}
    for p in sorted(community.platform_root(folder).rglob('*.igb'), key=lambda q: q.as_posix().lower()):
        n = p.name.lower()
        if n.startswith('hud_head_') and '(villain)' not in n:
            found['hud'].append(p)
        elif '(3d head)' in n:
            found['hud3d'].append(p)
        elif '(character select portrait)' in n:
            found['select'].append(p)
    routes, notes = {}, []
    for role in ROLES:
        files = found[role]
        if len(files) > 1:       # e.g. platform sub-folders (Magma): the PC/XML2 one, else the first
            beside = [p for p in files if near is not None and p.parent == Path(near).parent]
            files = beside[:1] or files[:1]
            notes.append(f'{role}: several files in the archive, used {files[0].parent.name}/{files[0].name}')
        if not files:
            notes.append(f'{role}: not in the community archive; stock kept')
            continue
        bad = check_head_file(files[0])
        if bad:
            notes.append(f'{role}: {files[0].name} refused ({bad}); stock kept')
            continue
        src = files[0]
        prefix = re.match(r'(?:hud_head_)?(\d{2})', src.name, re.I)
        if out_dir is not None and prefix:
            new_id = portrait_id(hero) if role == 'select' else hero.skin
            data, count, problems = rename_ids(src.read_bytes(), prefix.group(1), new_id)
            if problems:
                notes.append(f'{role}: {src.name} has names that do not fit {new_id} ({problems[:2]}); stock kept')
                continue
            if count:
                dest = Path(out_dir) / paths[role]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                src = dest
                notes.append(f'{role}: {count} in-file names renamed to {new_id}')
        routes[paths[role]] = src
    return routes, notes


def mua1_routes(hero, choice, ctx, out_dir, log=None):
    """Texture-swap MUA1's conversation and select portraits into the port's own scenes (driver heads_mua1.py)."""
    if ctx.mua1 is None:
        raise ToolError("MUA1 install not given/found", MISSING_INPUT)
    root = materialise('xml2', ctx.work, needs_sampler=False)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    report = out_dir / 'heads.json'
    log = log or root / 'logs' / f'heads-mua1-{hero.key}.log'
    args = ['--mua1', ctx.mua1, '--mua-bin', VENDOR / 'mua1' / 'mua_bin.py', '--port', ctx.port, '--out', out_dir,
            '--target', hero.skin, '--portrait-target', portrait_id(hero), '--source', choice.source, '--report', report]
    code = run('xml2', root, 'heads_mua1.py', args, ctx.env, log, ctx.report, driver=True)
    import json
    if code or not report.is_file():
        raise ToolError(f'{hero.key}: MUA1 head swap failed (exit {code})')
    info = json.loads(report.read_text())['files']
    routes, notes = {}, []
    for role, row in info.items():
        if row['status'] == 'replaced':
            routes[row['file']] = out_dir / row['file']
            notes.append(f'{role}: from MUA1 {row["donor"]} (texture only)')
        else:
            notes.append(f'{role}: {row["reason"]}; stock kept')
    return routes, notes


def collect(hero, choice, ctx):
    """(pack path -> source file, notes) for the heads of this hero's chosen source. Never raises for a
    missing/unusable head: a head problem leaves that role at stock and says why."""
    try:
        if choice.tier == 'community':
            from .engines.tiers import community_folder
            folder = community_folder(hero, choice, ctx)
            out = ctx.work / 'heads' / f'community-{hero.key}'
            if out.exists():
                shutil.rmtree(out)
            return community_routes(folder, hero, community.skin_file(folder, ctx.cel), out)
        if choice.tier == 'mua1':
            return mua1_routes(hero, choice, ctx, ctx.work / 'heads' / f'mua1-{hero.key}-{choice.source}')
    except ToolError as e:
        return {}, [f'heads: {str(e).splitlines()[0]}; stock kept']
    reason = {'mua2': 'MUA2 keeps its portraits in a UI atlas (no per-hero file)',
              'xml2': 'XML2 look keeps the port\'s own heads', 'sharpen': 'Sharpened keeps the port\'s own heads'}
    return {}, [f'heads: {reason.get(choice.tier, "no source")}; stock kept']
