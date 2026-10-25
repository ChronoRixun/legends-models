# SPDX-License-Identifier: MIT
"""Layered-cloth and loose-geometry rules (engines/vendor/_shared/layers.py) on synthetic meshes."""
import importlib.util
import unittest
from pathlib import Path

import numpy as np

_PATH = Path(__file__).resolve().parents[1] / 'legends_models' / 'engines' / 'vendor' / '_shared' / 'layers.py'
_spec = importlib.util.spec_from_file_location('layers', _PATH)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def sheet(nx, ny, z, flip=False, offset=0):
    """Grid sheet in the x/y plane at height z (normal +z, or -z when flipped)."""
    xs, ys = np.meshgrid(np.linspace(0, 4, nx), np.linspace(0, 4, ny))
    pos = np.column_stack([xs.ravel(), ys.ravel(), np.full(nx * ny, float(z))])
    tri = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, d = j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1
            tri += [[a, b, d], [a, d, c]]
    tri = np.array(tri)
    if flip:
        tri = tri[:, ::-1]
    norm = np.tile([0, 0, -1. if flip else 1.], (len(pos), 1))
    return pos, tri + offset, norm


def two_layer_cloth(gap=0.5):
    """Outer coat at z=1 (normal up), lining at z=1-gap facing the body; bones run along z<0."""
    po, to, no = sheet(5, 5, 1.0)
    pi, ti, ni = sheet(5, 5, 1.0 - gap, flip=True, offset=len(po))
    pos = np.r_[po, pi]; tri = np.r_[to, ti]; norm = np.r_[no, ni]
    segments = [(np.array([-10., 2, -2]), np.array([10., 2, -2]))]
    return pos, tri, norm, segments, len(po)


class Detection(unittest.TestCase):
    def test_lining_pairs_with_the_outer_coat(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth()
        lay = L.find_layers(pos, tri, norm, seg)
        self.assertEqual(sorted(lay['vertex'].tolist()), list(range(n_outer, len(pos))))   # every lining vertex
        self.assertTrue(np.all(lay['tri_vertices'] < n_outer))                             # outer triangles only
        np.testing.assert_allclose(np.abs(lay['offset']), 0.5, atol=1e-9)
        self.assertTrue(np.all(lay['offset'] < 0))                                         # lining behind the coat

    def test_single_sheet_and_far_layers_are_not_layers(self):
        pos, tri, norm = sheet(6, 6, 0.0)
        self.assertEqual(len(L.find_layers(pos, tri, norm)['vertex']), 0)
        pos, tri, norm, seg, _ = two_layer_cloth(gap=2.0)
        self.assertEqual(len(L.find_layers(pos, tri, norm, seg)['vertex']), 0)

    def test_independent_parts_with_disjoint_skinning_are_not_coupled(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth()
        n = len(pos)
        bi = np.zeros((n, 4), int); bw = np.zeros((n, 4)); bw[:, 0] = 1
        bi[n_outer:, 0] = 1                                    # e.g. left vs right thigh
        self.assertEqual(len(L.find_layers(pos, tri, norm, seg, skin=(bi, bw))['vertex']), 0)
        bi[n_outer:, 0] = 0                                    # same garment: same bones
        self.assertEqual(len(L.find_layers(pos, tri, norm, seg, skin=(bi, bw))['vertex']), len(pos) - n_outer)


    def test_hands_and_feet_are_self_contact_not_layers(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth()
        n = len(pos)
        bi = np.zeros((n, 4), int); bw = np.zeros((n, 4)); bw[:, 0] = 1
        names = ['Bip01 R Finger1', 'Bip01 Spine']
        self.assertEqual(len(L.find_layers(pos, tri, norm, seg, skin=(bi, bw), bone_names=names)['vertex']), 0)
        bi[:, 0] = 1                                            # the same sheets on the spine: a real layer
        self.assertEqual(len(L.find_layers(pos, tri, norm, seg, skin=(bi, bw), bone_names=names)['vertex']), n - n_outer)


class BindPlacement(unittest.TestCase):
    def test_inner_shell_is_put_back_behind_a_bent_outer_shell(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth()
        lay = L.find_layers(pos, tri, norm, seg)
        refit = pos.copy()
        refit[:n_outer, 2] += 0.3 * np.sin(pos[:n_outer, 0])      # the skeleton refit bends the coat...
        refit[n_outer:, 2] += 1.0                                  # ...and pushes the lining through it
        self.assertEqual(L.crossings(refit, lay, tri)[0], len(pos) - n_outer)
        placed, moved = L.place_inner_shells(refit, pos, lay, len(pos))
        self.assertEqual(moved, len(pos) - n_outer)
        self.assertEqual(L.crossings(placed, lay, tri)[0], 0)
        np.testing.assert_array_equal(placed[:n_outer], refit[:n_outer])   # the outer shell is untouched


class Penalty(unittest.TestCase):
    def test_offset_constraint_gradient_and_count(self):
        rng = np.random.default_rng(3)
        posed = rng.normal(size=(10, 4, 3))
        checks = dict(rows=np.array([5, 6]), tri_rows=np.array([[0, 1, 2], [2, 3, 4]]), bary=np.array([[.2, .3, .5], [.4, .4, .2]]),
                      sign=np.array([1., -1]), offset=np.array([.5, -.3]))
        loss, grad, count, _ = L.layer_penalty(posed, checks, margin=5)
        self.assertGreater(loss, 0)
        num = np.zeros_like(posed)
        for idx in np.ndindex(posed.shape):
            e = np.zeros_like(posed); e[idx] = 1e-6
            num[idx] = (L.layer_penalty(posed + e, checks, 5)[0] - L.layer_penalty(posed - e, checks, 5)[0]) / 2e-6
        np.testing.assert_allclose(grad, num, atol=1e-6)        # exact, including the face-normal term

    def test_intact_cloth_costs_nothing(self):
        pos, tri, norm, seg, _ = two_layer_cloth()
        checks = L.check_plan(L.find_layers(pos, tri, norm, seg), np.arange(len(pos)))
        loss, grad, count, ratio = L.layer_penalty(np.stack([pos, pos + [0, 0, 3]], 1), checks)
        self.assertEqual((loss, count), (0., 0))
        self.assertAlmostEqual(ratio, 1.0)


class Crossing(unittest.TestCase):
    def setUp(self):
        self.pos, self.tri, self.norm, seg, self.n_outer = two_layer_cloth()
        self.lay = L.find_layers(self.pos, self.tri, self.norm, seg)

    def test_rigid_motion_never_crosses(self):
        t = np.radians(70)
        R = np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]])
        self.assertEqual(L.crossings(self.pos @ R.T + [3, -1, 7], self.lay, self.tri)[0], 0)

    def test_lining_through_the_coat_is_counted(self):
        p = self.pos.copy()
        p[self.n_outer + 12, 2] = 1.2                          # one lining vertex pokes through
        n, ratio = L.crossings(p, self.lay, self.tri)
        self.assertEqual(n, 1)
        self.assertLess(ratio, 0)
        p[self.n_outer + 12, 2] = 1.0                          # touching counts as crossing too
        self.assertEqual(L.crossings(p, self.lay, self.tri)[0], 1)


class Coupling(unittest.TestCase):
    def test_tie_gradient_matches_finite_differences(self):
        rng = np.random.default_rng(1)
        n, B = 9, 7
        lanes = np.array([rng.choice(B, 4, replace=False) for _ in range(n)])
        lay = dict(vertex=np.array([5, 6, 7, 8]), tri_vertices=np.array([[0, 1, 2], [2, 3, 4], [5, 1, 0], [7, 6, 0]]),
                   bary=np.array([[.2, .3, .5], [.6, .2, .2], [.5, .25, .25], [.3, .3, .4]]))
        plan = L.coupling_plan(lay, None, n)
        self.assertEqual(len(plan['levels']), 3)               # 5,6 then 7 then 8 (chained shells)
        w0 = rng.random((n, 4)); w0 /= w0.sum(1, keepdims=True)
        lanes, w0 = L.tie_lanes(plan, w0, lanes, B)
        tie = L.WeightTie(plan, lanes, B)
        G = rng.normal(size=(n, 4))
        tie.forward(w0); g = tie.backward(G)
        num = np.zeros_like(w0)
        for i in range(n):
            for k in range(4):
                e = np.zeros_like(w0); e[i, k] = 1e-6
                num[i, k] = ((tie.forward(w0 + e) * G).sum() - (tie.forward(w0 - e) * G).sum()) / 2e-6
        np.testing.assert_allclose(g, num, atol=1e-7)
        self.assertTrue(np.all(g[plan['rows']] == 0))           # tied rows are not free

    def test_tied_lining_follows_the_coat_blend(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth()
        lay = L.find_layers(pos, tri, norm, seg)
        plan = L.coupling_plan(lay, None, len(pos))
        dense = np.zeros((len(pos), 3)); dense[:, 0] = 1
        dense[:n_outer, :] = np.column_stack([1 - pos[:n_outer, 0] / 4, pos[:n_outer, 0] / 4, 0 * pos[:n_outer, 0]])
        dense[n_outer:, 2] = 1                                  # lining authored on another bone
        blended = L.blend_dense(dense, plan)
        # every lining row now carries exactly its outer triangle's interpolated weights
        for k, v in enumerate(lay['vertex']):
            np.testing.assert_allclose(blended[v], lay['bary'][k] @ dense[lay['tri_vertices'][k]], atol=1e-12)


class Repair(unittest.TestCase):
    def test_residual_crossing_is_closed_by_a_shared_influence(self):
        pos, tri, norm, seg, n_outer = two_layer_cloth(gap=0.05)
        lay = L.find_layers(pos, tri, norm, seg)
        checks = L.check_plan(lay, np.arange(len(pos)))
        # Two bones: bone 1 lifts by +1 along z. The coat is on bone 0, one lining vertex is on bone 1.
        dense = np.zeros((len(pos), 2)); dense[:, 0] = 1
        culprit = n_outer + 12
        dense[culprit] = [0, 1]
        frames = [np.zeros(3), np.array([0, 0, 1.])]

        def posed_fn(D, rows):
            return np.stack([pos[rows] + D[rows, 1:2] * f for f in frames], 1)
        fixed, report = L.repair_crossings(dense, checks, posed_fn)
        self.assertGreaterEqual(report['crossing_pairs_before'], 1)
        self.assertEqual(report['crossing_pairs_after'], 0)
        k = int(np.flatnonzero(checks['rows'] == culprit)[0])
        patch = [culprit] + checks['tri_rows'][k].tolist()
        self.assertTrue(all(np.array_equal(fixed[patch[0]], fixed[r]) for r in patch))


class Loose(unittest.TestCase):
    def test_strand_past_its_leaf_bone_finds_the_body_bone_it_rests_on(self):
        # spine (0->1) up the back, head 2 on top, ponytail 3 -> leaf 4 hanging behind; hand 5 off to the side.
        names = ['Bip01 Spine', 'Bip01 Spine2', 'Bip01 Head', 'Bip01 Ponytail1', 'Bip01 Ponytail11', 'Bip01 L Hand']
        parent = np.array([-1, 0, 1, 2, 3, 1])
        origins = np.array([[0, 0, 40.], [0, 0, 50], [0, 0, 60], [-3, 0, 62], [-5, 0, 55], [6, -8, 47]])
        world = np.tile(np.eye(4), (6, 1, 1)); world[:, 3, :3] = origins
        pos = np.array([[-5, 0, 54.], [-5.5, 0, 45], [0, 3, 62]])      # strand root, strand tip, a face vertex
        bi = np.array([[4, 3, 2, 1], [4, 3, 2, 1], [2, 1, 0, 0]])
        bw = np.array([[.95, .04, .0095, .0005], [.95, .04, .0095, .0005], [1, 0, 0, 0]])
        rows, rest, dist = L.loose_vertices(pos, bi, bw, names, parent, world, 8.0, exclude=(' Hand',), anchor_weight=.1)
        self.assertEqual(rows.tolist(), [1])                               # only the tip is too far from its chain
        self.assertEqual(names[rest[0]], 'Bip01 Spine')                    # it rests on the back, not the hand
        self.assertGreater(dist[0], 8.0)



class CoatOneLeg(unittest.TestCase):
    def test_far_leg_weight_moves_to_the_mirrored_bone(self):
        names = ['Bip01 Pelvis', 'Bip01 L Thigh', 'Bip01 L Calf', 'Bip01 R Thigh', 'Bip01 R Calf']
        idx = np.array([[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]])
        w = np.array([[.4, .3, .2, .1], [.4, .3, .2, .1], [.4, .3, .2, .1]])
        coat = np.array([True, True, False])
        left = np.array([True, False, True])
        nw, ni, rep = L.coat_one_leg(names, coat, left, w, idx)
        dense = L.to_dense(ni, nw, len(names))
        np.testing.assert_allclose(dense[0], [0, .6, .4, 0, 0])      # L side: all on the L leg
        np.testing.assert_allclose(dense[1], [0, 0, 0, .6, .4])      # R side: all on the R leg
        np.testing.assert_allclose(dense[2], [0, .4, .3, .2, .1])    # not coat: unchanged
        self.assertEqual(rep['rows_changed'], 2)
        self.assertAlmostEqual(rep['weight_moved'], 1.0)


if __name__ == '__main__':
    unittest.main()
