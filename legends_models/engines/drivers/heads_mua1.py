# SPDX-License-Identifier: MIT
"""Driver: MUA1 conversation portrait / select portrait -> the port's own head IGBs, texture only.

For each role the port's stock IGB (same scene, same UVs, same texture format) is rewritten with
vendor/xml2 upscale.rewrite, replacing only the base image (and its mip chain) with MUA1's. Mesh,
transforms and bounds of the port file are byte-identical afterwards (rewrite proves it). Roles whose
MUA1 file is a different mesh (ui/hud/characters, a UV-atlas head) are not touched.
Run with cwd=<work>/xml2; LEGENDS_ENGINE_TOOLS = <work>/xml2/tools.
"""
import argparse, hashlib, importlib.util, json, os, sys
sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
from pathlib import Path
from PIL import Image
import upscale  # noqa: E402  (vendor/xml2/upscale.py)
from igb_file import IgbFile  # noqa: E402
from append_v6 import Appender  # noqa: E402

LOOSE_ROOTS = ['', 'Data', 'Setup/Data', 'files/Setup/Data']

ap = argparse.ArgumentParser()
ap.add_argument('--mua1', type=Path, required=True)
ap.add_argument('--mua-bin', type=Path, required=True, help='vendor mua_bin.py (stdlib archive reader)')
ap.add_argument('--port', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--target', required=True, help='port skin ID (HUD/hud_head_<target>.IGB)')
ap.add_argument('--portrait-target', required=True, help='port select-portrait ID (UI/models/characters/<id>.IGB)')
ap.add_argument('--source', required=True, help='MUA1 skin ID of the chosen costume')
ap.add_argument('--report', type=Path, required=True)
a = ap.parse_args()

spec = importlib.util.spec_from_file_location('mua_bin', a.mua_bin)
mua_bin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mua_bin)


class Donor:
    """Read-only MUA1 file access: 2006 loose files or the 2016 .bin archives."""

    def __init__(self, root):
        self.root, self.archives, self.loose = Path(root), {}, None
        for rel in LOOSE_ROOTS:
            if (self.root / rel / 'hud').is_dir() and (self.root / rel / 'ui').is_dir():
                self.loose = self.root / rel
                break

    def read(self, rel):
        rel = rel.lower()
        if self.loose is not None:
            for p in (self.loose / rel).parent.glob('*'):
                if p.name.lower() == rel.rsplit('/', 1)[1]:
                    return p.read_bytes()
            return None
        stem = rel.split('/', 1)[0]
        if stem not in self.archives:
            path = self.root / f'{stem}.bin'
            self.archives[stem] = mua_bin.Archive(path) if path.is_file() else None
        arc = self.archives[stem]
        if arc is None:
            return None
        for e in arc.entries:
            if e[0].lower() == rel:
                return arc.read(e)
        return None


def port_file(rel):
    cur = a.port
    for part in rel.split('/'):
        hit = [p for p in cur.iterdir() if p.name.lower() == part.lower()]
        if not hit:
            return None
        cur = hit[0]
    return cur


def donor_image(data):
    f = IgbFile(data)
    for ta in f.objects_of('igTextureAttr'):
        base = f.obj(ta.get(12))
        if base is not None and base.name == 'igImage':
            im = upscale.decode(f, base)
            # MUA's RGB565 endpoint convention is the opposite of D3D's (see vendor/mua1/convert.py swap_rb)
            r, g, b, alpha = im.split()
            return Image.merge('RGBA', (b, g, r, alpha))
    raise ValueError('no base image in the donor file')


def swap_texture(stock, dest, image):
    """Replace the pixels of every image of the port file's texture chain with `image` (resized to each level),
    keeping the file's structure exactly as it is: same objects, sizes, format, mip list (the stock UI heads
    have no mip chain, and the engine is only known to load them that way). Only memory blocks change."""
    original = IgbFile(Path(stock).read_bytes())
    f = IgbFile(original.data)
    app = Appender(f)
    replacements, allowed, rows = {}, set(), []
    for ta in f.objects_of('igTextureAttr'):
        base = f.obj(ta.get(12))
        if base is None or base.name != 'igImage':
            continue
        miplist = f.obj(ta.get(16))
        chain = [base] + [f.obj(r) for r in (f.list_items(miplist.ref) if miplist else [])]
        fmt = base.get(11)
        for im in chain:
            if im is None or im.get(11) != fmt or fmt not in upscale.FORMATS:
                raise ValueError('unsupported texture chain')
            level = image if image.size == (im.get(2), im.get(3)) else image.resize((im.get(2), im.get(3)), Image.Resampling.LANCZOS)
            data = upscale.encode(level, fmt)
            if len(data) != im.get(12):
                raise ValueError(f'encoded {len(data)} bytes, the file stores {im.get(12)}')
            replacements[im.get(13)] = data
            allowed.add(im.get(13))
            rows.append([im.get(2), im.get(3), upscale.FORMATS[fmt]])
    result = app.finish(replacements)
    check = IgbFile(result)
    inv = upscale.texture_invariant(original, check, allowed, set())
    for im in check.objects_of('igImage'):
        upscale.decode(check, im)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(result)
    return dict(output_sha256=hashlib.sha256(result).hexdigest(), images=rows, **inv)


donor = Donor(a.mua1)
report = {'source': a.source, 'files': {}}
roles = [('hud', f'HUD/hud_head_{a.target}.IGB', 'hud/hud_head_{}.igb', [a.source, a.source[:-2] + '01']),
         ('portrait', f'UI/models/characters/{a.portrait_target}.IGB', 'ui/models/characters/{}.igb', [a.source[:-2] + '01'])]
for role, out_rel, donor_tpl, ids in roles:
    stock = port_file(out_rel)
    if stock is None:
        report['files'][role] = dict(status='stock', reason='the port has no such file')
        continue
    data = used = None
    for i in dict.fromkeys(ids):
        data = donor.read(donor_tpl.format(i))
        if data:
            used = i
            break
    if not data:
        report['files'][role] = dict(status='stock', reason=f'MUA1 has no {donor_tpl.format(ids[0])}')
        continue
    try:
        image = donor_image(data)
    except Exception as ex:  # an unreadable donor leaves the role at stock
        report['files'][role] = dict(status='stock', reason=f'donor unreadable: {ex}')
        continue

    dest = a.out / out_rel
    info = swap_texture(stock, dest, image)
    report['files'][role] = dict(status='replaced', file=out_rel, donor=donor_tpl.format(used), sha256=info['output_sha256'],
                                 images=info['images'], mesh_identical=info['mesh_weights_bind_bounds_identical'])
a.report.write_text(json.dumps(report, indent=2))
print(json.dumps({k: v['status'] for k, v in report['files'].items()}))
