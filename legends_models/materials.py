"""Surface-detail material stages for converted actors (MUA normal + specular maps on the XML2 PC engine).

What the engine can draw (research: models-materials REPORT.md):

* Specular: XML2's Alchemy has a gloss-masked environment shader, igEnvironmentMapShader2, drawn on PC by
  igDx8EnvironmentMapShader. With pixel shaders it binds a fixed ps.1.1 program
      r0.rgb = v0 * t0 + t1 * t2      (vertex light * diffuse + environment(reflection) * gloss)
  t1 is a cube map looked up with the camera-space reflection vector turned into world space, t2 the gloss map.
  Stock XML2 skinned actors (Archangel's wings, two chrome skins) use the same node, so it works under
  igBlendMatrixSelect. `add_gloss_specular()` wraps the body geometry in that node: diffuse stays the actor's own
  bind, the gloss map is the MUA specular map, the environment is a generated "studio light" cube whose bright
  lobes act as the light sources of a Phong-like highlight. The shader binds and restores its own stages through
  its pass attribute lists (no loose stage-1/2 attributes in the actor's graph: that was the round-2 probe).

* Normal maps: there is no tangent-space path a skinned actor can use (igBumpMapShader computes DOT3 vertex
  colours on the CPU against one model matrix, with its own lights and lighting forced off). `bake_normal_detail()`
  folds the normal map's shading into the diffuse instead: per texel, the ratio of the lit detail normal to the lit
  geometric normal under a fixed key light, so the engine's own vertex lighting still provides the large-scale
  shading and only the authored surface detail is added.

Everything here runs on the player's PC against the player's own files; nothing is bundled.
"""
from __future__ import annotations

import io
import math
import struct

import numpy as np

from .formats.igb import IgbFile, IgbError
from .formats.igb_writer import IgbWriter

# Meta object layouts (format facts, identical in every XML2 PC file that carries them).
ENV_SHADER_LAYOUT = {
    'name': 'igEnvironmentMapShader2', 'major': 1, 'minor': 0, 'parent': 'igGroup', 'slots': 29,
    'fields': [('igStringMetaField', 2, 4), ('igObjectRefMetaField', 3, 4), ('igIntMetaField', 5, 4),
               ('igObjectRefMetaField', 7, 4), ('igObjectRefMetaField', 8, 4), ('igObjectRefMetaField', 9, 4),
               ('igIntMetaField', 10, 4), ('igFloatMetaField', 11, 4), ('igBoolMetaField', 12, 1),
               ('igObjectRefMetaField', 13, 4), ('igIntMetaField', 14, 4)]}
CUBE_ATTR_LAYOUT = {
    'name': 'igTextureCubeAttr', 'major': 1, 'minor': 0, 'parent': 'igTextureAttr', 'slots': 22,
    'fields': [('igShortMetaField', 2, 2), ('igUnsignedIntMetaField', 4, 4), ('igEnumMetaField', 5, 4),
               ('igEnumMetaField', 6, 4), ('igEnumMetaField', 7, 4), ('igEnumMetaField', 8, 4),
               ('igEnumMetaField', 10, 4), ('igEnumMetaField', 11, 4), ('igObjectRefMetaField', 12, 4),
               ('igBoolMetaField', 13, 1), ('igObjectRefMetaField', 14, 4), ('igIntMetaField', 15, 4),
               ('igObjectRefMetaField', 16, 4), ('igObjectRefArrayMetaField', 19, 24), ('igEnumMetaField', 20, 4),
               ('igIntMetaField', 21, 4)]}
# igEnvironmentMapShader2 slots: 2 name, 3 bound, 5 node flags, 7 child list, 8 environment (igTextureCubeAttr),
# 9 diffuse igTextureBindAttr, 10 diffuse uv set, 11 reflection amount, 12 transparency, 13 gloss igTextureAttr,
# 14 gloss uv set.  igImage: 2 width, 3 height, 11 pixel format (14 DXT1, 15 DXT3, 16 DXT5), 12 byte count,
# 13 pixel block, 19 row bytes (uncompressed equivalent), 22 name.  igTextureAttr: 12 image, 15 image count
# (top + mips), 16 igImageMipMapList.
_FOURCC = {14: b'DXT1', 15: b'DXT3', 16: b'DXT5'}

# Heroes whose converted actor was played in game with the gloss stage (models-materials REPORT.md: Wolverine 0301,
# Storm 0401, Iceman 0801, Colossus 7801, Dark Psylocke 3005; Nightcrawler's legacy 4701 source has no maps). The
# build adds the stage only for these; any other hero gets the bake only (and a warning) until it has been played.
SPEC_VERIFIED_HEROES = frozenset({'wolverine', 'storm', 'iceman', 'colossus', 'psylocke'})

# Default light rig, in the actor's model space (port actors: +Z up, +X forward).
KEY_LIGHT = (0.45, 0.25, 0.86)


# ---------------------------------------------------------------------------------------------------- textures
def _dds_header(w, h, size, fourcc):
    head = struct.pack('<7I', 124, 0x81007, h, w, size, 0, 1) + bytes(44)
    head += struct.pack('<II4s5I', 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack('<5I', 0x1000, 0, 0, 0, 0)
    return b'DDS ' + head


def decode_image(f: IgbFile, im, swap_rb=False) -> np.ndarray:
    """RGBA uint8 array of a DXT igImage. swap_rb: MUA files store R and B the other way round from D3D."""
    from PIL import Image
    w, h, fmt = im.get(2), im.get(3), im.get(11)
    if fmt not in _FOURCC:
        raise IgbError(f'image format {fmt} is not DXT1/3/5')
    off, size = f.block(im.get(13))
    data = f.data[off:off + size]
    rgba = np.asarray(Image.open(io.BytesIO(_dds_header(w, h, len(data), _FOURCC[fmt]) + data)).convert('RGBA')).copy()
    if swap_rb:
        rgba = rgba[:, :, [2, 1, 0, 3]].copy()
    return rgba


def encode_dxt3(rgba: np.ndarray) -> bytes:
    """DXT3 (Alchemy format 15) blocks of an RGBA image of any size (small mips padded to one block)."""
    from PIL import Image
    h, w = rgba.shape[:2]
    if w < 4 or h < 4:
        pad = np.zeros((max(h, 4), max(w, 4), 4), np.uint8)
        pad[:h, :w] = rgba
        pad[h:, :w] = rgba[-1:, :, :]
        pad[:, w:] = pad[:, w - 1:w]
        rgba = pad
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba, dtype=np.uint8), 'RGBA').save(buf, format='DDS', pixel_format='DXT3')
    return buf.getvalue()[128:]


def mip_chain(rgba: np.ndarray, smallest=4):
    """[top, half, ..., smallest] (Pillow LANCZOS). Port actors stop their DXT chains at 4x4 (one block); 2x2 and 1x1
    levels with sub-block row pitches crashed XMen2.exe at zone load (models-materials REPORT.md)."""
    from PIL import Image
    out = [rgba]
    img = Image.fromarray(rgba, 'RGBA')
    w, h = img.size
    while w > smallest and h > smallest:
        w, h = max(1, w // 2), max(1, h // 2)
        out.append(np.asarray(img.resize((w, h), Image.Resampling.LANCZOS)))
    return out


# ------------------------------------------------------------------------------------------------ environment
def _face_dirs(face, size):
    """Unit directions of the texels of D3D cube face `face` (+X, -X, +Y, -Y, +Z, -Z)."""
    c = (np.arange(size) + 0.5) / size * 2 - 1
    s, t = np.meshgrid(c, c)          # s: column (u), t: row (v)
    one = np.ones_like(s)
    d = {0: (one, -t, -s), 1: (-one, -t, s), 2: (s, one, t), 3: (s, -one, -t), 4: (s, -t, one), 5: (-s, -t, -one)}[face]
    v = np.stack(d, -1)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def studio_light(dirs: np.ndarray, lights=None, ambient=0.0) -> np.ndarray:
    """Radiance (0..1 RGB) of a dark studio with soft light lobes, for world directions `dirs` (..., 3), Z up.
    Each light: (direction, colour (r, g, b), exponent)."""
    if lights is None:
        # XML2's camera looks down on the party, so the reflections it sees point mostly upward and back toward
        # the camera: an overhead key (broad + hot spot) reads as a top light on shoulders, head, chest and thighs.
        lights = [((0.20, 0.15, 0.97), (0.85, 0.83, 0.80), 10.0),     # key: overhead, broad
                  ((0.20, 0.15, 0.97), (0.60, 0.60, 0.58), 60.0),     # key hot spot
                  ((-0.60, 0.50, 0.62), (0.35, 0.40, 0.50), 16.0),    # cool rim, high side
                  ((0.0, 0.0, 1.0), (0.10, 0.10, 0.11), 2.0)]         # sky fill; the floor stays black
    out = np.full(dirs.shape[:-1] + (3,), ambient, float)
    for d, colour, exponent in lights:
        d = np.asarray(d, float) / np.linalg.norm(d)
        lobe = np.clip(dirs @ d, 0, 1) ** exponent
        out += lobe[..., None] * np.asarray(colour)
    return np.clip(out, 0, 1)


# igTextureCubeAttr slot 19 holds the faces in the order -X, +X, -Y, +Y, -Z, +Z (D3D face index ^ 1): stock XML2
# files name slot i "<texture>.cube_<i ^ 1>", and an in-game calibration with six solid face colours
# (models-materials REPORT.md, shots/mm-mm-calib-*) showed camera-facing hunched chests on slot 4 (-Z), silhouettes
# on slot 0 (-X, away from a +X camera), screen-right on slot 3 (+Y): the lookup is the WORLD-space reflection
# vector, Z up, with that slot order.
CUBE_SLOT_FACE = (1, 0, 3, 2, 5, 4)


def environment_faces(size=64, lights=None):
    """Six RGBA faces of the generated studio environment, in igTextureCubeAttr slot order."""
    faces = []
    for slot in range(6):
        rgb = studio_light(_face_dirs(CUBE_SLOT_FACE[slot], size), lights)
        a = np.full(rgb.shape[:2] + (1,), 1.0)
        faces.append((np.concatenate([rgb, a], -1) * 255 + 0.5).astype(np.uint8))
    return faces


def sphere_image(size=64, lights=None):
    """Sphere-map fallback of the same environment (used only on devices without cube maps): viewer looking
    along -Y (world), Z up."""
    c = (np.arange(size) + 0.5) / size * 2 - 1
    x, y = np.meshgrid(c, -c)
    r2 = x * x + y * y
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    n = np.stack([x, y, z], -1)
    view = np.array([0, 0, 1.0])
    refl = 2 * (n @ view)[..., None] * n - view                 # view space (x right, y up, z toward viewer)
    world = np.stack([refl[..., 0], -refl[..., 2], refl[..., 1]], -1)
    rgb = studio_light(world / np.maximum(np.linalg.norm(world, axis=-1, keepdims=True), 1e-9), lights)
    rgb[r2 > 1] = 0
    a = np.ones(rgb.shape[:2] + (1,))
    return (np.concatenate([rgb, a], -1) * 255 + 0.5).astype(np.uint8)


# --------------------------------------------------------------------------------------------------- the graph
def _parent_of(f: IgbFile, child_ref):
    for o in f.objects_of('igGroup'):
        items = f.list_items(o.get(7))
        if items and child_ref in items:
            return o
    raise IgbError(f'no group holds node {child_ref}')


def body_geometry(f: IgbFile):
    """The skin's drawn (non-outline) igGeometry."""
    geos = [g for g in f.objects_of('igGeometry') if 'outline' not in (g.get(2) or '').lower()]
    if len(geos) != 1:
        raise IgbError(f'expected one non-outline body geometry, found {len(geos)}')
    return geos[0]


def _attrs(f, node):
    return [f.obj(r) for r in (f.list_items(node.get(8)) or [])]


def diffuse_bind(f: IgbFile, geometry):
    """The unit-0 igTextureBindAttr that textures `geometry` (its own attrs first, then its ancestors)."""
    node = geometry
    while True:
        for a in _attrs(f, node):
            if a is not None and a.name == 'igTextureBindAttr' and a.get(5) == 0:
                return a
        node = _parent_of(f, node.ref)


def _add_image(w: IgbWriter, template_image, rgba, name):
    data = encode_dxt3(rgba)
    h, wd = rgba.shape[:2]
    block = w.add_memory(data, template_image.get(13))
    return w.clone_object(template_image, {2: wd, 3: h, 11: 15, 12: len(data), 13: block, 19: max(wd, 4) * 4, 22: name})


def _add_list(w: IgbWriter, template_list, refs, template_block):
    """New igDataList-derived object (same type as template_list) holding refs."""
    block = w.add_memory(struct.pack(f'<{len(refs)}i', *refs), template_block)
    return w.clone_object(template_list, {2: len(refs), 3: len(refs), 4: block})


def _add_mip_texture(w: IgbWriter, template_texture, template_image, template_list, list_block, rgba, name,
                     cube_faces=None):
    """igTextureAttr (or igTextureCubeAttr when cube_faces is given) with a full mip chain of `rgba`."""
    chain = mip_chain(rgba)
    top = _add_image(w, template_image, chain[0], name)
    mips = [_add_image(w, template_image, m, f'{name}-{i}') for i, m in enumerate(chain[1:], 1)]
    mip_list = _add_list(w, template_list, mips, list_block)
    values = {s: fl.value for s, fl in template_texture.fields.items()}
    values.update({12: top, 15: len(chain), 16: mip_list})
    if cube_faces is not None:
        values.update({19: struct.pack('<6i', *cube_faces), 20: 1, 21: 0})
        return w.add_object('igTextureCubeAttr', values)
    return w.add_object('igTextureAttr', values)


def add_gloss_specular(data: bytes, gloss_rgba: np.ndarray, *, strength=1.0, env_size=64, lights=None,
                       reflection=1.0, name='mua_gloss') -> tuple[bytes, dict]:
    """Wrap the converted actor's body geometry in an igEnvironmentMapShader2 that adds environment * gloss.

    gloss_rgba: the MUA specular map (RGBA uint8, D3D channel order), any power-of-two size; its RGB is scaled by
    `strength` and becomes the gloss map (stage 2). Returns (new file bytes, summary)."""
    f = IgbFile(data)
    if 'igEnvironmentMapShader2' in f.meta_index and f.objects_of('igEnvironmentMapShader2'):
        raise IgbError('actor already has an environment shader')
    geo = body_geometry(f)
    parent = _parent_of(f, geo.ref)
    bind = diffuse_bind(f, geo)
    texture = f.obj(bind.get(4))
    image = f.obj(texture.get(12)) if texture is not None else None
    mip_list = f.obj(texture.get(16)) if texture is not None else None
    if texture is None or image is None or mip_list is None:
        raise IgbError('diffuse texture has no image / mip list to use as a template')
    child_list = f.obj(parent.get(7))
    child_block = child_list.get(4)
    mip_block = mip_list.get(4)
    w = IgbWriter(f)
    w.ensure_meta(ENV_SHADER_LAYOUT)
    w.ensure_meta(CUBE_ATTR_LAYOUT)

    size = image.get(2)
    if gloss_rgba.shape[0] != size or gloss_rgba.shape[1] != size:
        # the gloss map follows the diffuse's size (same UVs, no detail lost that the diffuse could show)
        from PIL import Image
        gloss_rgba = np.asarray(Image.fromarray(np.ascontiguousarray(gloss_rgba), 'RGBA').resize(
            (size, size), Image.Resampling.LANCZOS))
    gloss = gloss_rgba.astype(float)
    gloss[..., :3] = np.clip(gloss[..., :3] * strength, 0, 255)
    gloss[..., 3] = 255
    gloss = (gloss + 0.5).astype(np.uint8)
    gloss_tex = _add_mip_texture(w, texture, image, mip_list, mip_block, gloss, f'{name}_gloss')
    faces = [_add_image(w, image, face, f'{name}_env.cube_{i}')
             for i, face in enumerate(environment_faces(env_size, lights))]
    cube = _add_mip_texture(w, texture, image, mip_list, mip_block, sphere_image(env_size, lights), 'sphereImage',
                            cube_faces=faces)
    shader_children = _add_list(w, child_list, [geo.ref], child_block)
    shader = w.add_object('igEnvironmentMapShader2', {
        2: name, 3: -1, 5: 0, 7: shader_children, 8: cube, 9: bind.ref, 10: 0, 11: [float(reflection)], 12: 0,
        13: gloss_tex, 14: 0})
    # the body's attribute set now draws the shader, which draws the geometry (the layout verified in game)
    old = f.list_items(parent.get(7))
    w.replace_memory(child_block, struct.pack(f'<{len(old)}i', *[shader if r == geo.ref else r for r in old]))
    out = bytes(w)
    summary = {'specular': 'igEnvironmentMapShader2 gloss path (ps.1.1: v0*t0 + t1*t2)', 'gloss_size': gloss.shape[1],
               'gloss_strength': strength, 'environment': f'generated studio cube {env_size}px + sphere fallback',
               'reflection_amount': reflection, 'shader_ref': shader, 'geometry': geo.get(2)}
    return out, summary


def load_mesh(f: IgbFile):
    """pos, norm, uv (float arrays) and tri (int, n x 3) of the body geometry of a converted v6 actor."""
    geo = body_geometry(f)
    ga = next(f.obj(r) for r in f.list_items(geo.get(8)) if f.isa(f.obj(r), 'igGeometryAttr'))
    va = f.obj(ga.get(4))
    off, size = f.block(va.get(2))
    ind = struct.unpack_from('<20i', f.data, off)

    def arr(ref, width, dtype='<f4'):
        o, n = f.block(ref)
        return np.frombuffer(f.data[o:o + n], dtype=dtype).reshape(-1, width).astype(float)
    pos, norm, uv = arr(ind[0], 3), arr(ind[1], 3), arr(ind[11], 2)
    ia = f.obj(ga.get(5))
    o, n = f.block(ia.get(2))
    idx = np.frombuffer(f.data[o:o + n], dtype='<u2').astype(int)
    if ga.get(6) == 3:
        tri = idx[:len(idx) // 3 * 3].reshape(-1, 3)
    elif ga.get(6) == 4:
        tris, start = [], 0
        lengths = [len(idx)]
        if ga.get(13) is not None and ga.get(13) >= 0:
            lo, ln = f.block(f.obj(ga.get(13)).get(2))
            lengths = list(np.frombuffer(f.data[lo:lo + ln], dtype='<u4'))
        for length in lengths:
            strip = idx[start:start + length]
            start += length
            for j in range(len(strip) - 2):
                a, b, c = map(int, strip[j:j + 3])
                if j % 2:
                    a, b = b, a
                if len({a, b, c}) == 3:
                    tris.append([a, b, c])
        tri = np.array(tris, int)
    else:
        raise IgbError(f'primitive type {ga.get(6)} not supported')
    return {'pos': pos, 'norm': norm, 'uv': uv, 'tri': tri}


# ------------------------------------------------------------------------------------------------------ baking
def decode_normal_map(rgba: np.ndarray) -> np.ndarray:
    """Unit tangent-space normals (H, W, 3) from a MUA normal map: DXT5nm (x in alpha, y in green, R=B=0) or plain
    RGB. Returned in D3D channel order independent of the source R/B swap (x from alpha is unaffected)."""
    a = rgba.astype(float) / 255.0
    if a[..., 0].max() < 0.02 and a[..., 2].max() < 0.02:          # DXT5nm
        x, y = a[..., 3] * 2 - 1, a[..., 1] * 2 - 1
    else:
        x, y = a[..., 0] * 2 - 1, a[..., 1] * 2 - 1
    z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
    n = np.stack([x, y, z], -1)
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


def green_convention_score(tangent_normals: np.ndarray) -> float:
    """> 0 when the map's y axis runs along +v (D3D / image-down), < 0 when it runs along -v (OpenGL / image-up).

    For bumps and dents alike, d(x)/du and d(y)/dv have the same sign when y follows +v, so their correlation over
    the textured texels tells the convention without knowing the authoring tool."""
    dx = np.gradient(tangent_normals[..., 0], axis=1)
    dy = np.gradient(tangent_normals[..., 1], axis=0)
    m = (np.abs(dx) > 0.01) | (np.abs(dy) > 0.01)
    if m.sum() < 16:
        return 0.0
    return float(np.corrcoef(dx[m], dy[m])[0, 1])


def vertex_tangents(pos, norm, uv, tri):
    """Per-vertex (T, B) from UV derivatives (accumulated per triangle, Gram-Schmidt against the normal).
    B keeps its own sign (handedness), so mirrored UV islands bake correctly."""
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    w0, w1, w2 = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = w1 - w0, w2 - w0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) > 1e-12, 1.0 / np.where(det == 0, 1, det), 0.0)[:, None]
    t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    b = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    T = np.zeros_like(pos, dtype=float)
    B = np.zeros_like(pos, dtype=float)
    for k in range(3):
        np.add.at(T, tri[:, k], t)
        np.add.at(B, tri[:, k], b)
    n = norm / np.maximum(np.linalg.norm(norm, axis=1, keepdims=True), 1e-9)
    T = T - n * np.sum(n * T, 1, keepdims=True)
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
    B = B - n * np.sum(n * B, 1, keepdims=True) - T * np.sum(T * B, 1, keepdims=True)
    B /= np.maximum(np.linalg.norm(B, axis=1, keepdims=True), 1e-9)
    return T, B


def rasterize_frames(pos, norm, uv, tri, size, flip_v=False):
    """Per texel (size x size) interpolated (N, T, B) and a coverage mask, rasterising triangles in UV space."""
    T, B = vertex_tangents(pos, norm, uv, tri)
    N = norm / np.maximum(np.linalg.norm(norm, axis=1, keepdims=True), 1e-9)
    frames = np.zeros((size, size, 9))
    mask = np.zeros((size, size), bool)
    px = uv[:, 0] * size - 0.5
    py = ((1 - uv[:, 1]) if flip_v else uv[:, 1]) * size - 0.5
    attr = np.concatenate([N, T, B], 1)
    for a, b, c in tri:
        xs, ys = px[[a, b, c]], py[[a, b, c]]
        x0, x1 = int(max(0, math.floor(xs.min()))), int(min(size - 1, math.ceil(xs.max())))
        y0, y1 = int(max(0, math.floor(ys.min()))), int(min(size - 1, math.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        l2 = 1 - l0 - l1
        inside = (l0 >= -0.02) & (l1 >= -0.02) & (l2 >= -0.02)
        if not inside.any():
            continue
        val = l0[..., None] * attr[a] + l1[..., None] * attr[b] + l2[..., None] * attr[c]
        yy, xx = gy[inside], gx[inside]
        frames[yy, xx] = val[inside]
        mask[yy, xx] = True
    return frames, mask


def shading_ratio(frames, mask, tangent_normals, light=KEY_LIGHT, ambient=0.45, strength=1.0,
                  clamp=(0.55, 1.35)):
    """Per texel: (ambient + max(0, n_detail . L)) / (ambient + max(0, n_geom . L)), detail only (1 where the
    normal map is flat or the texel is uncovered)."""
    L = np.asarray(light, float)
    L /= np.linalg.norm(L)
    N, T, B = frames[..., 0:3], frames[..., 3:6], frames[..., 6:9]
    n = tangent_normals
    if n.shape[:2] != N.shape[:2]:
        raise IgbError('normal map and bake size differ')
    detail = T * n[..., 0:1] + B * n[..., 1:2] + N * n[..., 2:3]
    detail /= np.maximum(np.linalg.norm(detail, axis=-1, keepdims=True), 1e-9)
    Nn = N / np.maximum(np.linalg.norm(N, axis=-1, keepdims=True), 1e-9)
    lit_d = ambient + np.clip(detail @ L, 0, 1)
    lit_g = ambient + np.clip(Nn @ L, 0, 1)
    ratio = np.where(mask, lit_d / lit_g, 1.0)
    ratio = 1 + (ratio - 1) * strength
    return np.clip(ratio, *clamp)


def dilate(values, mask, steps=4):
    """Grow covered texels into uncovered neighbours (avoids seams from bilinear filtering / mips)."""
    v, m = values.copy(), mask.copy()
    for _ in range(steps):
        acc = np.zeros_like(v)
        cnt = np.zeros(m.shape)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            sv = np.roll(np.roll(v, dy, 0), dx, 1)
            sm = np.roll(np.roll(m, dy, 0), dx, 1)
            acc[sm & ~m] += sv[sm & ~m]
            cnt[sm & ~m] += 1
        grow = (cnt > 0) & ~m
        v[grow] = acc[grow] / cnt[grow][..., None] if v.ndim == 3 else acc[grow] / cnt[grow]
        m = m | grow
    return v


def bake_normal_detail(data: bytes, normal_rgba: np.ndarray, *, light=KEY_LIGHT, ambient=0.45, strength=1.0,
                       flip_v=False, green='auto') -> tuple[bytes, dict]:
    """Multiply the converted actor's diffuse (every mip) by the normal map's detail shading. Returns
    (new file bytes, summary). The diffuse images keep their sizes, formats and objects; only pixels change."""
    f = IgbFile(data)
    mesh = load_mesh(f)
    geo = body_geometry(f)
    bind = diffuse_bind(f, geo)
    texture = f.obj(bind.get(4))
    top = f.obj(texture.get(12))
    mips = [f.obj(r) for r in (f.list_items(texture.get(16)) or [])]
    size = top.get(2)
    tn = decode_normal_map(normal_rgba)
    if tn.shape[0] != size:
        from PIL import Image
        # resample the unit normals (component-wise) to the diffuse size, then renormalise
        comp = [np.asarray(Image.fromarray(((tn[..., k] + 1) * 127.5).astype(np.uint8)).resize(
            (size, size), Image.Resampling.LANCZOS)).astype(float) / 127.5 - 1 for k in range(3)]
        tn = np.stack(comp, -1)
        tn /= np.maximum(np.linalg.norm(tn, axis=-1, keepdims=True), 1e-9)
    score = green_convention_score(tn)
    flip_green = (score < 0) if green == 'auto' else (green == 'up')
    if flip_green:
        tn[..., 1] *= -1
    frames, mask = rasterize_frames(mesh['pos'], mesh['norm'], mesh['uv'], mesh['tri'], size, flip_v)
    ratio = shading_ratio(frames, mask, tn, light, ambient, strength)
    ratio = dilate(ratio, mask, 6)
    base = decode_image(f, top)
    rgb = np.clip(base[..., :3].astype(float) * ratio[..., None], 0, 255)
    baked = np.concatenate([rgb, base[..., 3:4].astype(float)], -1).round().astype(np.uint8)
    chain = mip_chain(baked)
    blocks = {}
    for im in [top] + mips:
        lvl = next((c for c in chain if c.shape[1] == im.get(2)), None)
        if lvl is None:
            raise IgbError(f'no mip of size {im.get(2)}')
        if im.get(11) != 15:
            raise IgbError('expected DXT3 diffuse images')
        blocks[im.get(13)] = encode_dxt3(lvl)
    out = f.rebuilt(blocks)
    IgbFile(out)
    covered = float(mask.mean())
    summary = {'normal_bake': 'directional detail ratio folded into diffuse', 'light': list(light), 'ambient': ambient,
               'strength': strength, 'uv_coverage': round(covered, 4),
               'ratio_range': [round(float(ratio[mask].min()), 3), round(float(ratio[mask].max()), 3)],
               'ratio_mean': round(float(ratio[mask].mean()), 4), 'mips_rebaked': 1 + len(mips),
               'green_convention_score': round(score, 3), 'green_flipped': bool(flip_green)}
    return out, summary


def mua_maps(source: IgbFile):
    """{'normal': rgba, 'specular': rgba} top-level (largest) MUA *_n / *_s maps of a MUA 2006/2016 skin."""
    found = {}
    for im in source.objects_of('igImage'):
        name = (im.get(22) or '').lower().replace('\\', '/').rsplit('/', 1)[-1]
        for key, suffix in (('normal', '_n.png'), ('specular', '_s.png')):
            if name.endswith(suffix) and (key not in found or im.get(2) > found[key].get(2)):
                found[key] = im
    return {k: decode_image(source, im, swap_rb=True) for k, im in found.items()}


# ------------------------------------------------------------------------------------------------ invariants
def material_invariant(before: bytes, after: bytes) -> dict:
    """Prove a material step changed only what it may: every original object keeps its type and every field
    value; every original memory block keeps its bytes, except (a) the child list of the body's attribute set
    (now naming the shader instead of the geometry) and (b) diffuse pixel blocks (the bake). Mesh, weights,
    skeleton, bind, bounds and animation data are therefore byte-identical."""
    a, b = IgbFile(before), IgbFile(after)
    geo = body_geometry(a)
    parent = _parent_of(a, geo.ref)
    allowed_blocks = {a.obj(parent.get(7)).get(4)}
    texture = a.obj(diffuse_bind(a, geo).get(4))
    images = [a.obj(texture.get(12))] + [a.obj(r) for r in (a.list_items(texture.get(16)) or [])]
    pixel_blocks = {im.get(13) for im in images}
    failures, pixels_changed, list_changed = [], 0, False
    for o in a.objects:
        n = b.obj(o.ref)
        if n is None or n.name != o.name:
            failures.append(f'object {o.ref} ({o.name}) replaced')
            continue
        for s, fl in o.fields.items():
            if n.get(s) != fl.value:
                failures.append(f'object {o.ref} ({o.name}) slot {s} changed')
    for ref, (off, size) in a.blocks.items():
        if ref not in b.blocks:
            failures.append(f'block {ref} gone')
            continue
        o2, s2 = b.blocks[ref]
        same = a.data[off:off + size] == b.data[o2:o2 + s2]
        if same:
            continue
        if ref in pixel_blocks and s2 == size:
            pixels_changed += 1
        elif ref in allowed_blocks:
            list_changed = True
        else:
            failures.append(f'memory block {ref} changed')
    shaders = b.objects_of('igEnvironmentMapShader2') if 'igEnvironmentMapShader2' in b.meta_index else []
    loose_stages = [x.ref for x in b.objects_of('igTextureStateAttr') + b.objects_of('igTextureBindAttr')
                    if x.get(5) not in (0, None)]
    if loose_stages:
        failures.append(f'loose texture stage attributes on units > 0: {loose_stages}')
    return {'pass': not failures, 'failures': failures[:20], 'diffuse_blocks_rebaked': pixels_changed,
            'body_list_rewired': list_changed, 'shaders_added': len(shaders),
            'mesh_weights_skeleton_bind_bounds_identical': not failures}


def apply(candidate: bytes, source: IgbFile, steps=('spec',), spec_strength=3.0, bake_strength=1.5):
    """Run the requested material steps ('bake' first, then 'spec') on a gated candidate. Returns
    (bytes, summary); summary['invariant'] must pass before the result is used."""
    maps = mua_maps(source)
    data, summary = candidate, {'steps': []}
    if 'bake' in steps:
        if 'normal' not in maps:
            summary['bake'] = 'skipped: source has no *_n normal map'
        else:
            data, s = bake_normal_detail(data, maps['normal'], strength=bake_strength)
            summary['bake'] = s
            summary['steps'].append('bake')
    if 'spec' in steps:
        if 'specular' not in maps:
            summary['spec'] = 'skipped: source has no *_s specular map'
        else:
            data, s = add_gloss_specular(data, maps['specular'], strength=spec_strength)
            summary['spec'] = s
            summary['steps'].append('spec')
    summary['invariant'] = material_invariant(candidate, data)
    return data, summary
