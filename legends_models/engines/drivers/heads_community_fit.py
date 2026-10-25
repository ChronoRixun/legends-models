# SPDX-License-Identifier: MIT
"""Driver: fit a community archive's select portrait and 3D HUD head to the port's own layout.

select  The community portrait is a 128 px full-bleed image on a +-28.95 quad; the port's select portraits are
        64 px images with a 2 px dark rim on a +-19.885 quad (UI/models/characters/<pid>.IGB). The community
        image is resized into the rim's interior, the rim is copied from the port's own portrait of this
        hero, and the result is texture-swapped into the PORT's portrait scene (mesh, quad size, format and
        mip structure byte-identical; only the pixel blocks change), so it reads at the size and with the
        frame of every other portrait in the roster.
hud3d   --depth-start/--depth-factor: vertices behind the plane y = start (the head looks down -y) move toward
        it by `factor`. Beast's community 3D head carries a mane that hangs behind and below the ears; the
        HUD shows the head turned, so the mane reads as a dark flap beside the face. The vertex block is edited
        in place (same file size) and both bounding boxes are set to the new extent.
Run with cwd=<work>/xml2; LEGENDS_ENGINE_TOOLS = <work>/xml2/tools.
"""
import argparse, hashlib, json, os, struct, sys
from pathlib import Path

sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
COMMUNITY = Path(__file__).resolve().parents[1] / 'vendor' / 'community'
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
import upscale  # noqa: E402  (vendor/xml2/upscale.py)
from igb_file import IgbFile  # noqa: E402
from append_v6 import Appender  # noqa: E402

RIM = 2          # px of dark frame (copied); widened to one DXT block with its mean colour in the port's 64 px select portraits (measured on 14101/14301/14501/14801/14901)


def community_modules():
    sys.path.insert(0, str(COMMUNITY))
    sys.path.insert(0, str(COMMUNITY / 'round2'))
    import community_igb  # noqa: E402
    from round1_converter import blob, geometry_attribute  # noqa: E402
    return community_igb, blob, geometry_attribute


def port_image(f):
    ta = f.objects_of('igTextureAttr')[0]
    return upscale.decode(f, f.obj(ta.get(12))).convert('RGBA')


def framed(art, stock, rim=RIM, block=4):
    """`art` resized into the interior of a frame in the colour of `stock`'s own rim.

    The port textures are DXT (4x4 blocks, two colours each): a 2 px dark rim beside bright art in one block
    breaks up. The rim is therefore widened to the block size with the rim's own mean colour, so every edge
    block is dark only."""
    w, h = stock.size
    pad = max(rim, block)
    inner = art.convert('RGBA').resize((w - 2 * pad, h - 2 * pad), Image.Resampling.LANCZOS)
    a = np.asarray(stock).astype(float)
    ring = np.concatenate([a[:rim].reshape(-1, 4), a[-rim:].reshape(-1, 4), a[:, :rim].reshape(-1, 4),
                           a[:, -rim:].reshape(-1, 4)])
    colour = tuple(int(round(v)) for v in ring.mean(0))
    out = Image.new('RGBA', (w, h), colour)       # the stock rim's colour, one block wide (clean DXT edges)
    out.paste(inner, (pad, pad))
    return out


def swap_texture(stock_path, dest, image):
    """Replace every image of the port file's texture chain with `image` (resized per level); structure kept."""
    original = IgbFile(Path(stock_path).read_bytes())
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
    return dict(sha256=hashlib.sha256(result).hexdigest(), images=rows,
                mesh_identical=inv['mesh_weights_bind_bounds_identical'])


def fit_select(src, stock, dest):
    community_igb, _, _ = community_modules()
    art = community_igb.png8_texture(community_igb.IgbFile(Path(src).read_bytes()))
    stock_img = port_image(IgbFile(Path(stock).read_bytes()))
    return swap_texture(stock, dest, framed(art, stock_img))


def flatten_back(data, start, factor):
    """(bytes, moved, bbox) with vertices behind y = start pulled toward it by `factor` (same file size)."""
    community_igb, blob, geometry_attribute = community_modules()
    f = community_igb.IgbFile(data)
    geos = f.objects_of('igGeometry')
    if len(geos) != 1:
        raise ValueError(f'{len(geos)} geometries; expected one head mesh')
    ga = geometry_attribute(f, geos[0])
    va = f.obj(ga.get(4))
    if va.name == 'igVertexArray2':
        raise ValueError('unexpected vertex array type')
    raw = blob(f, va.get(2))
    ref = struct.unpack('<%di' % (len(raw) // 4), raw)[0]
    p = np.array(f.block_floats(ref)).reshape(-1, 3)
    m = p[:, 1] > start
    p[m, 1] = start + (p[m, 1] - start) * factor
    f.set_block_floats(ref, p.ravel().tolist())
    lo, hi = p.min(0).tolist(), p.max(0).tolist()
    for box in f.objects_of('igAABox'):
        f.set_floats(box, 2, lo)
        f.set_floats(box, 3, hi)
    out = f.edited()
    if len(out) != len(data):
        raise ValueError('size changed')
    community_igb.IgbFile(out)
    return out, int(m.sum()), [lo, hi]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--select', type=Path, help="community '(Character Select Portrait).igb'")
    ap.add_argument('--select-stock', type=Path, help="the port's UI/models/characters/<pid>.IGB")
    ap.add_argument('--select-out', type=Path)
    ap.add_argument('--hud3d', type=Path, help='community 3D head (already renamed to the port id)')
    ap.add_argument('--hud3d-out', type=Path)
    ap.add_argument('--depth-start', type=float)
    ap.add_argument('--depth-factor', type=float)
    ap.add_argument('--report', type=Path, required=True)
    a = ap.parse_args(argv)
    report = {}
    if a.select:
        try:
            report['select'] = dict(status='fitted', file=str(a.select_out), rim=RIM,
                                    **fit_select(a.select, a.select_stock, a.select_out))
        except Exception as ex:  # noqa: BLE001 - reported; the caller keeps the author's file
            report['select'] = dict(status='failed', reason=f'{type(ex).__name__}: {ex}')
    if a.hud3d:
        try:
            out, moved, box = flatten_back(Path(a.hud3d).read_bytes(), a.depth_start, a.depth_factor)
            a.hud3d_out.parent.mkdir(parents=True, exist_ok=True)
            a.hud3d_out.write_bytes(out)
            report['hud3d'] = dict(status='fitted', file=str(a.hud3d_out), moved_vertices=moved, bounds=box,
                                   depth_start=a.depth_start, depth_factor=a.depth_factor,
                                   sha256=hashlib.sha256(out).hexdigest())
        except Exception as ex:  # noqa: BLE001
            report['hud3d'] = dict(status='failed', reason=f'{type(ex).__name__}: {ex}')
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v['status'] for k, v in report.items()}))


if __name__ == '__main__':
    main()
