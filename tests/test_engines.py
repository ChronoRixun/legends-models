# SPDX-License-Identifier: MIT
"""Vendored converters: provenance, path hygiene, materialisation (no conversion run)."""
import ast
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legends_models import engines
from legends_models.errors import ToolError

VENDOR = engines.VENDOR
RESEARCH_PATH = re.compile(r"[A-Z]:[/\\](?:Projects|Games)")


class Vendor(unittest.TestCase):
    def test_every_vendored_file_parses(self):
        files = list(VENDOR.rglob('*.py')) + list(engines.DRIVERS.glob('*.py'))
        self.assertGreater(len(files), 60)
        for p in files:
            ast.parse(p.read_text(encoding='utf-8'), str(p))

    def test_no_research_pc_paths_left_in_code(self):
        for p in VENDOR.rglob('*.py'):
            self.assertIsNone(RESEARCH_PATH.search(p.read_text(encoding='utf-8')), p)

    def test_patch_list_matches_files(self):
        patches = json.loads((VENDOR / 'PATCHES.json').read_text())
        self.assertGreaterEqual(len(patches), 17)
        for patch in patches:
            self.assertIn(patch['new'].split('  #')[0], (VENDOR / patch['file']).read_text(encoding='utf-8'), patch['file'])

    def test_entry_points_the_adapters_call_exist(self):
        need = {'mua1': ['convert.py', 'validate.py', 'fit_weights.py', 'mua_bin.py'],
                'mua2': ['convert_mua2.py', 'refine_bind.py', 'validate_mua2.py', 'mua_bin.py', 'igz.py'],
                'xml2': ['inventory.py', 'build_xml2.py', 'fit_roster.py', 'upscale.py', 'validate_roster.py'],
                'community': ['community_convert.py', 'refine_candidates.py', 'rgba8_probe.py', 'fix_mip_stride.py',
                              'round2/validate.py', 'round2/fit_weights.py']}
        for engine, files in need.items():
            for f in files:
                self.assertTrue((VENDOR / engine / f).is_file(), f'{engine}/{f}')

    def test_mua1_roster_ids_match_the_vendored_converter(self):
        from legends_models import roster
        src = (VENDOR / 'mua1' / 'characters.py').read_text(encoding='utf-8')
        table = ast.literal_eval(src.split('CHARACTERS=', 1)[1].split('\n}', 1)[0] + '\n}')
        for h in roster.HEROES:
            r = h.recipe('mua1')
            if r and r.status != 'none':
                name = roster.MUA1_NAMES.get(h.key, h.key)
                self.assertIn(name, table)
                self.assertEqual(table[name][1], h.skin)
                self.assertEqual(table[name][2], h.animation_db + '.IGB')


class Materialise(unittest.TestCase):
    def test_copies_engine_and_sampler_and_refreshes(self):
        with tempfile.TemporaryDirectory() as t:
            work = Path(t)
            (work / 'bin').mkdir()
            (work / 'bin' / engines.SAMPLER).write_bytes(b'MZ fake sampler')
            root = engines.materialise('community', work)
            self.assertTrue((root / 'tools' / 'community_convert.py').is_file())
            self.assertTrue((root / 'tools' / 'round2' / engines.SAMPLER).is_file())
            self.assertTrue((root / 'tools' / engines.SAMPLER).is_file())
            (root / 'tools' / 'community_convert.py').write_text('tampered')
            (root / 'tools' / '.legends-models-engine').write_text('old')
            engines.materialise('community', work)
            self.assertNotEqual((root / 'tools' / 'community_convert.py').read_text(), 'tampered')

    def test_missing_sampler_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'LEGENDS_MODELS_SAMPLER': ''}):
            with mock.patch.object(engines, 'sampler_path', lambda work=None: None):
                with self.assertRaises(ToolError):
                    engines.materialise('mua1', Path(t))
                engines.materialise('xml2', Path(t), needs_sampler=False)


if __name__ == '__main__':
    unittest.main()
