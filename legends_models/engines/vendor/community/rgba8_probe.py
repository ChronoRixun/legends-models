"""Player-local texture-only RGBA8888 probe; no native acceptance implied."""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path
import numpy as np
from PIL import Image
from community_igb import IgbFile, png8_texture


def block_bytes(f, ref):
    off, size = f.blocks[ref]
    return f.data[off:off + size]


def decode_rgba8(f, image):
    """Decode linear format 7 only; fail rather than reinterpret other formats."""
    if image.get(11) != 7:
        raise ValueError('image is not format 7 RGBA8888')
    w, h = image.get(2), image.get(3)
    raw = block_bytes(f, image.get(13))
    if image.get(19) != w * 4 or len(raw) != w * h * 4:
        raise ValueError('invalid RGBA8 dimensions, stride or payload')
    return Image.frombytes('RGBA', (w, h), raw)


def rgba8_candidate(original, atlas):
    """Keep every object/reference and non-pixel block; replace only image fields/pixels."""
    f = IgbFile(original)
    if f.header[11] != 0xf0000006:
        raise ValueError('expected existing v6 candidate')
    images = f.objects_of('igImage')
    largest = max(images, key=lambda im: im.get(2) * im.get(3))
    if atlas.size != (largest.get(2), largest.get(3)):
        raise ValueError('source atlas dimensions differ from candidate base image')
    atlas = atlas.convert('RGBA')
    replacements = {}
    for im in images:
        w, h = im.get(2), im.get(3)
        raw = atlas.resize((w, h), Image.Resampling.LANCZOS).tobytes()
        for slot, value in ((11, 7), (12, len(raw)), (18, 32), (19, w * 4)):
            struct.pack_into('<i', f.buf, im.fields[slot].offset, value)
        ref = im.get(13)
        if ref in replacements and replacements[ref] != raw:
            raise ValueError('shared image block has incompatible dimensions')
        replacements[ref] = raw
    result = f.rebuilt(replacements)
    preservation(original, result)
    return result


def preservation(original, result):
    """Compare all protected bytes independently after rereading serialization."""
    a, b = IgbFile(original), IgbFile(result)
    images = {o.ref for o in a.objects_of('igImage')}
    pixels = {o.get(13) for o in a.objects_of('igImage')}
    if len(a.objects) != len(b.objects) or set(a.blocks) != set(b.blocks):
        raise ValueError('object/block identity changed')
    protected_objects = 0
    for old, new in zip(a.objects, b.objects):
        if (old.ref, old.name, old.size) != (new.ref, new.name, new.size):
            raise ValueError('object identity changed')
        if old.ref not in images:
            if a.data[old.offset:old.offset + old.size] != b.data[new.offset:new.offset + new.size]:
                raise ValueError(f'protected object changed: {old.ref}')
            protected_objects += 1
        else:
            for slot in old.fields:
                if slot not in {11, 12, 18, 19} and old.get(slot) != new.get(slot):
                    raise ValueError(f'protected image field changed: {old.ref}/{slot}')
    protected_blocks = 0
    for ref in a.blocks:
        if ref not in pixels:
            if block_bytes(a, ref) != block_bytes(b, ref):
                raise ValueError(f'protected memory block changed: {ref}')
            protected_blocks += 1
    return {'all_non_image_objects_byte_identical': True,
            'all_non_pixel_memory_blocks_byte_identical': True,
            'protected_objects': protected_objects, 'protected_blocks': protected_blocks,
            'only_image_slots_changed': [11, 12, 18, 19],
            'mesh_skeleton_bounds_uv_weights_indices_unchanged': True}


def visible_uv(model, view='back', height=1024, width=1024):
    """Atlas texel for each visible bind-pose pixel, matching round2 renderer projection."""
    axes = {'front': (1, 2, 0), 'back': (1, 2, 0), 'side': (0, 2, 1)}[view]
    p = np.asarray(model.pos)[:, axes].copy()
    if view in ('front', 'side'):
        p[:, 0] *= -1
    if view == 'back':
        p[:, 2] *= -1
    lo, hi = p[:, :2].min(0), p[:, :2].max(0)
    scale = min((width - 100) / max(hi[0] - lo[0], 1), (height - 100) / max(hi[1] - lo[1], 1))
    xy = p[:, :2].copy()
    xy[:, 0] = (xy[:, 0] - (lo[0] + hi[0]) / 2) * scale + width / 2
    xy[:, 1] = height - 40 - (xy[:, 1] - lo[1]) * scale
    depth_buffer = np.full((height, width), -np.inf)
    result = np.full((height, width, 2), np.nan)
    for tri in model.tri:
        pts = xy[tri]
        xmin, xmax = max(0, int(np.floor(pts[:, 0].min()))), min(width - 1, int(np.ceil(pts[:, 0].max())))
        ymin, ymax = max(0, int(np.floor(pts[:, 1].min()))), min(height - 1, int(np.ceil(pts[:, 1].max())))
        if xmin > xmax or ymin > ymax:
            continue
        a, b, c = pts
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-8:
            continue
        yy, xx = np.mgrid[ymin:ymax + 1, xmin:xmax + 1]
        xx, yy = xx + .5, yy + .5
        wa = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / den
        wb = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / den
        wc = 1 - wa - wb
        depth = wa * p[tri[0], 2] + wb * p[tri[1], 2] + wc * p[tri[2], 2]
        sub = depth_buffer[ymin:ymax + 1, xmin:xmax + 1]
        mask = (wa >= -1e-7) & (wb >= -1e-7) & (wc >= -1e-7) & (depth > sub)
        if mask.any():
            result[ymin:ymax + 1, xmin:xmax + 1][mask] = wa[mask, None] * model.uv[tri[0]] + wb[mask, None] * model.uv[tri[1]] + wc[mask, None] * model.uv[tri[2]]
            sub[mask] = depth[mask]
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=Path('extracted/storm/original/04XX (Skin).igb'))
    ap.add_argument('--candidate', type=Path, default=Path('output/storm/original/14401.IGB'))
    ap.add_argument('--output', type=Path, default=Path('output/rgba8-probe/storm-original'))
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = IgbFile(args.source.read_bytes())
    atlas = png8_texture(source).convert('RGBA')
    original = args.candidate.read_bytes()
    result = rgba8_candidate(original, atlas)
    destination = args.output / '14401.IGB'
    destination.write_bytes(result)
    shutil.copyfile('CREDITS.md', args.output / 'CREDITS.md')
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'round2'))
    from community_model import Model, texture
    from render import render
    model = Model(args.candidate)
    before = texture(model).convert('RGB')
    candidate_file = IgbFile(result)
    after_rgba = decode_rgba8(candidate_file, max(candidate_file.objects_of('igImage'), key=lambda im: im.get(2) * im.get(3)))
    after = after_rgba.convert('RGB')
    before.save(args.output / 'before-dxt3-atlas.png')
    atlas.save(args.output / 'source-exact-rgba-atlas.png')
    for view in ('front', 'back'):
        pair = Image.new('RGB', (2048, 1024))
        for index, (tex, title) in enumerate(((before, 'Existing BC2/DXT3 texture'), (after, 'Exact source RGBA8 texture'))):
            pair.paste(render(model, model.pos, tex, view, title, width=1024, outline=True), (index * 1024, 0))
        pair.save(args.output / f'before-after-{view}.png')
    # Project only currently visible back geometry: changed pixels cannot come from a geometry swap.
    uv = visible_uv(model)
    mask = np.isfinite(uv[:, :, 0])
    coords = (np.mod(uv[mask], 1) * [atlas.width, atlas.height]).astype(int)
    src = np.asarray(after)[coords[:, 1], coords[:, 0]].astype(int)
    dxt = np.asarray(before)[coords[:, 1], coords[:, 0]].astype(int)
    # Near-neutral source hair texels that become yellow/gold under BC2.
    hair_gold = (src.min(1) > 90) & (np.ptp(src, axis=1) < 30) & ((dxt[:, 0] - dxt[:, 2]) > 55) & ((dxt[:, 1] - dxt[:, 2]) > 40)
    samples = []
    used = set()
    xy = np.column_stack(np.nonzero(mask))[:, ::-1]
    for i in np.flatnonzero(hair_gold):
        tx, ty = map(int, coords[i])
        if (tx, ty) in used:
            continue
        used.add((tx, ty))
        samples.append({'atlas_xy': [tx, ty], 'uv': uv[mask][i].tolist(), 'screen_xy': xy[i].tolist(), 'source_rgb': src[i].tolist(), 'dxt3_rgb': dxt[i].tolist(), 'bc2_block_xy': [tx // 4, ty // 4]})
    report = {'source': str(args.source), 'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
              'original_candidate': str(args.candidate), 'original_sha256': hashlib.sha256(original).hexdigest(),
              'rgba8_candidate': str(destination), 'rgba8_sha256': hashlib.sha256(result).hexdigest(),
              'format': 7, 'depth': 32, 'source_rgba_base_pixel_exact': np.array_equal(np.asarray(after_rgba), np.asarray(atlas)),
              'preservation': preservation(original, result), 'visible_back_neutral_to_gold_pixels': int(hair_gold.sum()),
              'visible_back_neutral_to_gold_texels': samples, 'native_acceptance': 'pending; offline probe only'}
    (args.output / 'probe.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'visible_back_neutral_to_gold_texels'}, indent=2))


if __name__ == '__main__':
    main()
