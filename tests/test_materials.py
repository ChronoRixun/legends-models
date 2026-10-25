# SPDX-License-Identifier: MIT
"""Material writer: the IGB graph writer, the gloss-specular stage and the normal-detail bake.

Synthetic data only, except the class that runs against a port install when LEGENDS_PORT points at one (skipped
otherwise) - it never writes into the install."""
import os
import struct
import unittest
from pathlib import Path

import numpy as np

from legends_models import materials as M
from legends_models.formats.igb import IgbError, IgbFile
from legends_models.formats.igb_writer import IgbWriter, encode_fields, meta_layout
import synth


class WriterTest(unittest.TestCase):
    def setUp(self):
        self.f = IgbFile(synth.igb(block=b'abcdefgh'))

    def test_round_trip_without_changes_is_identical(self):
        self.assertEqual(bytes(IgbWriter(self.f)), self.f.data)

    def test_add_meta_object_and_memory(self):
        w = IgbWriter(self.f)
        layout = {'name': 'igOther', 'major': 1, 'minor': 0, 'parent': 'igThing', 'slots': 6,
                  'fields': [('igIntMetaField', 2, 4), ('igStringMetaField', 3, 4), ('igFloatMetaField', 4, 4),
                             ('igIntMetaField', 5, 4)]}
        index = w.ensure_meta(layout)
        self.assertEqual(index, 3)
        self.assertEqual(w.ensure_meta(layout), 3)               # idempotent
        block = w.add_memory(b'\x01\x02\x03\x04\x05', 1)
        ref = w.add_object('igOther', {2: 42, 3: 'a longer name than before', 4: [2.5], 5: block})
        out = IgbFile(bytes(w))
        self.assertEqual(out.metas[3]['name'], 'igOther')
        self.assertEqual(out.metas[3]['parent'], out.meta_index['igThing'])
        o = out.obj(ref)
        self.assertEqual((o.name, o.get(2), o.get(3), o.get(4), o.get(5)), ('igOther', 42, 'a longer name than before',
                                                                               [2.5], block))
        off, size = out.block(block)
        self.assertEqual(out.data[off:off + size], b'\x01\x02\x03\x04\x05')
        self.assertTrue(out.isa(o, 'igThing'))
        # the original object and block are untouched
        self.assertEqual(out.obj(0).get(3), 'hello')
        off, size = out.block(1)
        self.assertEqual(out.data[off:off + size], b'abcdefgh')

    def test_clone_keeps_values_and_replace_resizes(self):
        w = IgbWriter(self.f)
        ref = w.clone_object(self.f.obj(0), {2: 9})
        w.replace_memory(1, b'xyz')
        out = IgbFile(bytes(w))
        self.assertEqual((out.obj(ref).get(2), out.obj(ref).get(3), out.obj(ref).get(4)), (9, 'hello', [1.5]))
        off, size = out.block(1)
        self.assertEqual(out.data[off:off + size], b'xyz')

    def test_rejections(self):
        w = IgbWriter(self.f)
        with self.assertRaises(IgbError):
            w.ensure_meta({'name': 'igX', 'major': 1, 'minor': 0, 'parent': 'igMissing', 'slots': 1, 'fields': []})
        with self.assertRaises(IgbError):
            w.ensure_meta({'name': 'igX', 'major': 1, 'minor': 0, 'parent': None, 'slots': 1,
                           'fields': [('igVec3fMetaField', 2, 12)]})        # field type not in the file
        with self.assertRaises(IgbError):
            w.ensure_meta({'name': 'igThing', 'major': 1, 'minor': 0, 'parent': None, 'slots': 1, 'fields': []})
        with self.assertRaises(IgbError):
            w.add_memory(b'1234', 0)                                    # object, not a memory block
        with self.assertRaises(IgbError):
            w.add_object('igNope', {})
        with self.assertRaises(IgbError):
            IgbWriter(IgbFile(synth.igb(version=0xF0000008)))

    def test_meta_layout_reads_back(self):
        lay = meta_layout(self.f, 'igThing')
        self.assertEqual(lay['fields'], [('igIntMetaField', 2, 4), ('igStringMetaField', 3, 4),
                                         ('igFloatMetaField', 4, 4)])
        self.assertIsNone(lay['parent'])

    def test_encode_fields_defaults(self):
        meta = {'name': 'm', 'fields': [('igObjectRefMetaField', 2, 4), ('igIntMetaField', 3, 4),
                                        ('igStringMetaField', 4, 4), ('igObjectRefArrayMetaField', 5, 24)]}
        raw = encode_fields(self.f, meta, {})
        self.assertEqual(struct.unpack_from('<2i', raw), (-1, 0))
        self.assertEqual(struct.unpack_from('<I', raw, 8)[0], 4)        # '' + NUL padded to 4
        self.assertEqual(len(raw), 8 + 8 + 24)
        with self.assertRaises(IgbError):
            encode_fields(self.f, meta, {5: b'short'})


class EnvironmentTest(unittest.TestCase):
    def test_face_directions_are_unit_and_on_their_axis(self):
        for face, (axis, sign) in enumerate([(0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)]):
            d = M._face_dirs(face, 8)
            self.assertTrue(np.allclose(np.linalg.norm(d, axis=-1), 1))
            self.assertTrue(np.all(np.sign(d[..., axis]) == sign))
            self.assertTrue(np.all(np.abs(d[..., axis]) >= np.abs(d).max(-1) - 1e-9))

    def test_studio_light_is_brightest_toward_the_key_light(self):
        faces = M.environment_faces(16)
        self.assertEqual(len(faces), 6)
        self.assertEqual(faces[0].shape, (16, 16, 4))
        means = [f[..., :3].mean() for f in faces]
        self.assertEqual(int(np.argmax(means)), 5)                       # slot 5 = +Z (up) face
        self.assertLess(means[4], 1.0)                                     # slot 4 = -Z: floor stays dark
        self.assertEqual(M.CUBE_SLOT_FACE, (1, 0, 3, 2, 5, 4))
        self.assertTrue(np.all(faces[2][..., 3] == 255))

    def test_sphere_fallback_shape(self):
        s = M.sphere_image(32)
        self.assertEqual(s.shape, (32, 32, 4))
        self.assertEqual(int(s[0, 0, :3].max()), 0)                       # outside the disc

    def test_dxt3_and_mips(self):
        img = (np.random.default_rng(1).random((16, 16, 4)) * 255).astype(np.uint8)
        chain = M.mip_chain(img)
        self.assertEqual([c.shape[0] for c in chain], [16, 8, 4])          # stops at one DXT block, like port actors
        self.assertEqual([len(M.encode_dxt3(c)) for c in M.mip_chain(img, smallest=1)], [256, 64, 16, 16, 16])


class BakeTest(unittest.TestCase):
    def quad(self, flip_u=False):
        pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
        norm = np.tile([0, 0, 1.0], (4, 1))
        uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
        if flip_u:
            uv[:, 0] = 1 - uv[:, 0]
        tri = np.array([[0, 1, 2], [0, 2, 3]])
        return pos, norm, uv, tri

    def test_tangents_follow_uv(self):
        pos, norm, uv, tri = self.quad()
        T, B = M.vertex_tangents(pos, norm, uv, tri)
        self.assertTrue(np.allclose(T, [1, 0, 0]))
        self.assertTrue(np.allclose(B, [0, 1, 0]))
        T, B = M.vertex_tangents(*self.quad(flip_u=True))              # mirrored island
        self.assertTrue(np.allclose(T, [-1, 0, 0]))

    def test_flat_normal_map_changes_nothing(self):
        frames, mask = M.rasterize_frames(*self.quad(), 8)
        self.assertGreater(mask.mean(), 0.9)
        flat = np.zeros((8, 8, 3))
        flat[..., 2] = 1
        r = M.shading_ratio(frames, mask, flat, light=(0, 0, 1))
        self.assertTrue(np.allclose(r, 1))

    def test_tilted_detail_toward_light_brightens(self):
        frames, mask = M.rasterize_frames(*self.quad(), 8)
        n = np.zeros((8, 8, 3))
        n[..., 0], n[..., 2] = 0.5, np.sqrt(0.75)                      # tilted toward +u (= +X here)
        lit = M.shading_ratio(frames, mask, n, light=(1, 0, 1))
        away = M.shading_ratio(frames, mask, n, light=(-1, 0, 1))
        self.assertTrue(np.all(lit[mask] > 1) and np.all(away[mask] < 1))

    def test_dxt5nm_decode(self):
        rgba = np.zeros((2, 2, 4), np.uint8)
        rgba[..., 1], rgba[..., 3] = 128, 255                            # x = +1 (alpha), y = 0 (green)
        n = M.decode_normal_map(rgba)
        self.assertTrue(np.allclose(n[0, 0], [1, 0, 0], atol=0.02))
        rgb = np.zeros((1, 1, 4), np.uint8)
        rgb[..., :3] = (128, 128, 255)                                    # plain RGB normal map: flat
        self.assertTrue(np.allclose(M.decode_normal_map(rgb)[0, 0], [0, 0, 1], atol=0.02))

    def test_green_convention_probe(self):
        n = np.zeros((32, 32, 3))
        yy, xx = np.mgrid[0:32, 0:32]
        h = np.exp(-((xx - 16) ** 2 + (yy - 16) ** 2) / 20.0)          # one bump
        gy, gx = np.gradient(h)
        n[..., 0], n[..., 1], n[..., 2] = -gx * 4, -gy * 4, 1
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        self.assertGreater(M.green_convention_score(n), 0)              # y along +v (D3D)
        n[..., 1] *= -1
        self.assertLess(M.green_convention_score(n), 0)

    def test_dilate_fills_neighbours(self):
        v = np.zeros((5, 5))
        m = np.zeros((5, 5), bool)
        v[2, 2], m[2, 2] = 3.0, True
        out = M.dilate(v, m, 1)
        self.assertEqual((out[1, 2], out[2, 1], out[0, 0]), (3.0, 3.0, 0.0))


PORT = os.environ.get('LEGENDS_PORT')


@unittest.skipUnless(PORT and Path(PORT, 'Actors/1001.IGB').is_file(), 'needs LEGENDS_PORT (an XML2-engine port install)')
class PortTest(unittest.TestCase):
    """Against the player's own files: layouts match XML2's, and a stock actor gains a working shader graph."""

    def test_layouts_match_stock_archangel(self):
        f = IgbFile(Path(PORT, 'Actors/1001.IGB').read_bytes())
        self.assertEqual(meta_layout(f, 'igEnvironmentMapShader2'), M.ENV_SHADER_LAYOUT)
        self.assertEqual(meta_layout(f, 'igTextureCubeAttr'), M.CUBE_ATTR_LAYOUT)

    def test_invariant_accepts_the_stage_and_catches_a_mesh_edit(self):
        data = Path(PORT, 'Actors/14301.IGB').read_bytes()
        out, _ = M.add_gloss_specular(data, np.full((64, 64, 4), 80, np.uint8), env_size=16)
        inv = M.material_invariant(data, out)
        self.assertTrue(inv['pass'], inv['failures'])
        self.assertTrue(inv['body_list_rewired'])
        f = IgbFile(out)
        mesh_block = struct.unpack_from('<20i', f.data, f.block(f.obj(next(
            f.obj(r) for r in f.list_items(M.body_geometry(f).get(8))).get(4)).get(2))[0])[0]
        off, _size = f.block(mesh_block)
        tampered = bytearray(out)
        tampered[off] ^= 0xFF
        bad = M.material_invariant(data, bytes(tampered))
        self.assertFalse(bad['pass'])
        self.assertTrue(any('memory block' in x for x in bad['failures']))


    def test_gloss_stage_graph_on_a_stock_hero(self):
        data = Path(PORT, 'Actors/14301.IGB').read_bytes()
        before = IgbFile(data)
        gloss = np.full((64, 64, 4), 80, np.uint8)
        out, summary = M.add_gloss_specular(data, gloss, env_size=16)
        f = IgbFile(out)
        (shader,) = f.objects_of('igEnvironmentMapShader2')
        geo = M.body_geometry(f)
        self.assertEqual(f.list_items(shader.get(7)), [geo.ref])
        self.assertEqual(f.obj(shader.get(9)).name, 'igTextureBindAttr')
        self.assertEqual(f.obj(shader.get(9)).get(5), 0)
        cube = f.obj(shader.get(8))
        self.assertEqual(cube.name, 'igTextureCubeAttr')
        faces = struct.unpack('<6i', cube.get(19))
        self.assertTrue(all(f.obj(r).name == 'igImage' and f.obj(r).get(2) == 16 for r in faces))
        gl = f.obj(shader.get(13))
        diffuse = f.obj(f.obj(shader.get(9)).get(4))
        size = f.obj(diffuse.get(12)).get(2)                        # gloss follows the diffuse size
        levels = size.bit_length() - 2                              # down to 4x4
        self.assertEqual((gl.name, f.obj(gl.get(12)).get(2), gl.get(15)), ('igTextureAttr', size, levels))
        self.assertEqual(len(f.list_items(gl.get(16))), levels - 1)
        parent = M._parent_of(f, shader.ref)
        self.assertNotIn(geo.ref, f.list_items(parent.get(7)))
        # no loose texture stages anywhere: every texture state/bind attr stays on unit 0
        for a in f.objects_of('igTextureStateAttr') + f.objects_of('igTextureBindAttr'):
            self.assertEqual(a.get(5), 0)
        # every original object keeps its type and every non-list field its value
        changed = {M._parent_of(before, M.body_geometry(before).ref).get(7)}
        for o in before.objects:
            n = f.obj(o.ref)
            self.assertEqual(n.name, o.name)
            if o.ref not in changed:
                for s, fl in o.fields.items():
                    self.assertEqual(n.get(s), fl.value, (o.name, s))
        with self.assertRaises(IgbError):
            M.add_gloss_specular(out, gloss)                               # never twice


if __name__ == '__main__':
    unittest.main()
