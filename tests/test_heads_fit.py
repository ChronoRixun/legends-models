# SPDX-License-Identifier: MIT
"""Community select portrait / 3D head fitting (drivers/heads_community_fit.py, heads.community_fit)."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from legends_models import heads, roster
from legends_models.engines import VENDOR

DRIVER = Path(heads.__file__).parent / 'engines' / 'drivers' / 'heads_community_fit.py'
# Optional real-file check: the extracted community Beast archive folder (never shipped with the repo).
BEAST = os.environ.get('LEGENDS_TEST_COMMUNITY_BEAST')


def driver_module():
    os.environ.setdefault('LEGENDS_ENGINE_TOOLS', str(VENDOR / 'xml2'))
    spec = importlib.util.spec_from_file_location('heads_community_fit', DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Frame(unittest.TestCase):
    def test_art_sits_inside_a_block_wide_rim_of_the_stock_colour(self):
        d = driver_module()
        stock = Image.new('RGBA', (64, 64), (200, 200, 200, 255))
        a = np.asarray(stock).copy()
        a[:2] = a[-2:] = a[:, :2] = a[:, -2:] = (10, 10, 10, 255)
        stock = Image.fromarray(a)
        out = np.asarray(d.framed(Image.new('RGBA', (128, 128), (255, 128, 0, 255)), stock)).astype(int)
        self.assertEqual(out.shape, (64, 64, 4))
        rim = np.concatenate([out[:4].reshape(-1, 4), out[-4:].reshape(-1, 4), out[:, :4].reshape(-1, 4),
                              out[:, -4:].reshape(-1, 4)])
        self.assertTrue((rim[:, :3].max(0) < 120).all())          # dark everywhere in the four edge block rows
        self.assertEqual(tuple(out[32, 32][:3]), (255, 128, 0))   # the art fills the interior
        self.assertEqual(tuple(out[4, 4][:3]), (255, 128, 0))

    @unittest.skipUnless(BEAST, 'set LEGENDS_TEST_COMMUNITY_BEAST to an extracted community Beast folder')
    def test_beast_head_back_hair_pulled_in(self):
        d = driver_module()
        data = (Path(BEAST) / '05XX (3D Head).igb').read_bytes()
        out, moved, (lo, hi) = d.flatten_back(data, -6.0, 0.3)
        self.assertEqual(len(out), len(data))
        self.assertGreater(moved, 0)
        self.assertLess(hi[1], 1.0)
        self.assertAlmostEqual(lo[1], -16.18, places=1)          # the face is untouched


class Routing(unittest.TestCase):
    def test_fitted_files_replace_the_authors_and_failures_keep_them(self):
        hero = roster.BY_KEY['beast']
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            port = t / 'port'
            (port / 'UI/models/characters').mkdir(parents=True)
            (port / 'UI/models/characters/14501.IGB').write_bytes(b'x')
            routes = {'UI/models/characters/14501.IGB': t / 'sel.igb', 'UI/hud/characters/14501.IGB': t / '3d.igb',
                      'HUD/hud_head_14501.IGB': t / 'hud.igb'}
            ctx = mock.Mock(work=t / 'work', port=port, env={}, report=None)
            seen = {}

            def fake_run(engine, root, script, args, env, log, report, driver=False):
                seen['args'] = [str(a) for a in args]
                rep = Path(args[args.index('--report') + 1])
                rep.parent.mkdir(parents=True, exist_ok=True)
                rep.write_text(json.dumps({'select': {'status': 'fitted', 'file': str(t / 'fit-sel.igb')},
                                           'hud3d': {'status': 'failed', 'reason': 'boom'}}))
                return 0
            with mock.patch.object(heads, 'run', fake_run), mock.patch.object(heads, 'materialise', lambda *a, **k: t):
                new, notes = heads.community_fit(hero, routes, ctx, t / 'out')
            self.assertEqual(new['UI/models/characters/14501.IGB'], t / 'fit-sel.igb')
            self.assertEqual(new['UI/hud/characters/14501.IGB'], t / '3d.igb')       # failed fit: author's file
            self.assertEqual(new['HUD/hud_head_14501.IGB'], t / 'hud.igb')
            self.assertIn('--depth-start', seen['args'])
            self.assertTrue(any('fit failed' in n for n in notes))

    def test_heroes_without_a_3d_fit_only_fit_the_portrait(self):
        hero = roster.BY_KEY['storm']
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            routes = {'UI/hud/characters/14401.IGB': t / '3d.igb'}
            ctx = mock.Mock(work=t, port=t, env={}, report=None)
            with mock.patch.object(heads, 'run', side_effect=AssertionError('no driver call expected')):
                new, notes = heads.community_fit(hero, routes, ctx, t / 'out')
            self.assertEqual((new, notes), (routes, []))

    def test_driver_compiles(self):
        import py_compile
        with tempfile.TemporaryDirectory() as t:
            py_compile.compile(str(DRIVER), cfile=str(Path(t) / 'x.pyc'), doraise=True)


if __name__ == '__main__':
    unittest.main()
