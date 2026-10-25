# SPDX-License-Identifier: MIT
import copy
import json
import tempfile
import unittest
from pathlib import Path

from legends_models import gate

STATS = {'max_edge_ratio': 4., 'max_chain_distance': 10., 'max_seam_separation': 0.}


def mua1_report():
    row = {'models': {k: copy.deepcopy(STATS) for k in ['stock', 'stock_outline', 'converted', 'converted_outline']},
           'hand_head_motion_matrix_delta': 0., 'claw_hand_local_transform_error': 0., 'foot_height_fraction': .02}
    row['models']['converted']['layer_crossings'] = 0
    return {'gameplay_structure_unchanged': True, 'converted_sha256': None, 'layer_check': {'pairs': 12, 'bind_crossings': 0},
            'animations': {n: copy.deepcopy(row) for n in gate.REFERENCE_CLIPS}}


def xml2_report():
    clip = {'models': {'stock': dict(STATS), 'converted': dict(STATS)}, 'hand_head_motion_matrix_delta': 0.0}
    clips = {n: copy.deepcopy(clip) for n in gate.REFERENCE_CLIPS}
    clips['idle']['idle_feet_fraction'] = 0.004
    return {'pass_numeric': True, 'candidate_sha256': 'x', 'clips': clips, 'errors': [],
            'invariant': {'skeleton_and_bind_bytes_identical': True}}


def mua2_report():
    m = {k: dict(STATS) for k in ['stock', 'converted', 'stock_outline', 'converted_outline']}
    clips = {n: {'models': copy.deepcopy(m), 'hand_head_motion_matrix_delta': 0.0, 'layer_crossings': 0} for n in gate.REFERENCE_CLIPS}
    clips['idle']['idle_feet_stock_height_fraction'] = 0.0062
    return {'numeric_available_clips_pass': True, 'candidate_sha256': 'y', 'clips': clips,
            'layer_check': {'pairs': 2372, 'bind_crossings': 0},
            'preservation': {'skeleton_arrays_identical': True, 'all_noncosmetic_objects_identical': True,
                             'igAABox': {'count': 1, 'objects_identical': True}}}


def ev(report):
    return gate.evaluate(gate.normalize(report))


class Gate(unittest.TestCase):
    def test_equal_to_stock_passes_in_all_three_formats(self):
        for r in (mua1_report(), xml2_report(), mua2_report()):
            self.assertTrue(ev(r)['numeric_pass'], ev(r)['failures'])
        self.assertTrue(gate.stock_relative_gate(mua1_report())['numeric_pass'])

    def test_layer_crossings_fail_in_any_clip_or_the_bind(self):
        for mutate in [lambda d: d['animations']['run']['models']['converted'].update(layer_crossings=1),
                       lambda d: d['layer_check'].update(bind_crossings=3),
                       lambda d: d['animations']['pain_high']['models']['converted'].pop('layer_crossings')]:
            d = mua1_report(); mutate(d)
            self.assertFalse(ev(d)['numeric_pass'])
            self.assertFalse(gate.stock_relative_gate(d)['numeric_pass'] and 'layer_crossings' in
                             d['animations']['pain_high']['models']['converted'])
        d = mua2_report(); d['clips']['power_2']['layer_crossings'] = 41
        self.assertFalse(ev(d)['numeric_pass'])
        self.assertIn('power_2: converted layer crossings (41)', ev(d)['failures'])

    def test_layer_check_required_for_mua_tiers_only(self):
        for r in (mua1_report(), mua2_report()):
            r.pop('layer_check')
            self.assertTrue(gate.evaluate(gate.normalize(r))['numeric_pass'])          # community/xml2 style
            res = gate.evaluate(gate.normalize(r), require_layers=True)
            self.assertFalse(res['numeric_pass'])
            self.assertIn('layer-crossing check not measured', res['failures'])
        self.assertEqual(ev(xml2_report())['layer_check'], 'not measured')

    def test_kinds(self):
        self.assertEqual([gate.detect(r) for r in (mua1_report(), xml2_report(), mua2_report())], ['mua1', 'xml2', 'mua2'])
        with self.assertRaises(ValueError):
            gate.detect({'something': 1})

    def test_each_rule_fails_independently(self):
        cases = [
            lambda d: d['animations']['idle']['models']['converted'].update(max_edge_ratio=4.000001),
            lambda d: d['animations']['run']['models']['converted_outline'].update(max_chain_distance=10.0001),
            lambda d: d['animations']['power_2']['models']['converted'].update(max_seam_separation=1e-14),
            lambda d: d['animations']['pain_high'].update(hand_head_motion_matrix_delta=1e-14),
            lambda d: d['animations']['attack_light1'].update(claw_hand_local_transform_error=1e-9),
            lambda d: d['animations']['idle'].update(foot_height_fraction=.020001),
            lambda d: d['animations'].pop('f_heavy_attack_4'),
            lambda d: d['animations']['run']['models']['converted'].update(max_edge_ratio=float('nan')),
            lambda d: d.update(gameplay_structure_unchanged=False),
        ]
        for i, mutate in enumerate(cases):
            d = mua1_report()
            mutate(d)
            self.assertFalse(ev(d)['numeric_pass'], i)
            self.assertFalse(gate.stock_relative_gate(d)['numeric_pass'], i)

    def test_one_clip_cannot_hide_behind_another(self):
        d = mua1_report()
        d['animations']['idle']['models']['stock']['max_edge_ratio'] = 1.0
        d['animations']['idle']['models']['converted']['max_edge_ratio'] = 1.5   # < run's stock 4.0
        self.assertFalse(ev(d)['numeric_pass'])

    def test_missing_measurements_fail(self):
        d = xml2_report()
        del d['clips']['run']['models']['stock']
        self.assertFalse(ev(d)['numeric_pass'])
        d = xml2_report()
        del d['clips']['idle']['idle_feet_fraction']
        self.assertFalse(ev(d)['numeric_pass'])
        d = mua2_report()
        d['preservation']['skeleton_arrays_identical'] = False
        self.assertFalse(ev(d)['numeric_pass'])
        d = xml2_report()
        d['errors'] = ['boom']
        self.assertFalse(ev(d)['numeric_pass'])

    def test_report_is_bound_to_the_candidate_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            cand = Path(t) / 'c.IGB'
            cand.write_bytes(b'candidate')
            d = mua1_report()
            d['converted_sha256'] = gate.sha256(cand)
            rp = Path(t) / 'v.json'
            rp.write_text(json.dumps(d))
            self.assertTrue(gate.check_report(rp, cand)['numeric_pass'])
            cand.write_bytes(b'tampered')
            r = gate.check_report(rp, cand)
            self.assertFalse(r['numeric_pass'])
            self.assertIn('hash mismatch', ' '.join(r['failures']))
            d['converted_sha256'] = None
            rp.write_text(json.dumps(d))
            self.assertFalse(gate.check_report(rp, cand)['numeric_pass'])

    def test_texture_only_gate(self):
        ok = {'nontexture_fields_and_blocks_identical': True, 'mesh_weights_bind_bounds_identical': True,
              'hand_head_motion_matrix_delta': 0}
        self.assertTrue(gate.texture_only_gate(ok)['numeric_pass'])
        for k, v in [('nontexture_fields_and_blocks_identical', False), ('hand_head_motion_matrix_delta', 1)]:
            bad = dict(ok, **{k: v})
            self.assertFalse(gate.texture_only_gate(bad)['numeric_pass'])
        self.assertFalse(gate.texture_only_gate({})['numeric_pass'])


if __name__ == '__main__':
    unittest.main()
