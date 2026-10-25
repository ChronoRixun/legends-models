# SPDX-License-Identifier: MIT
"""Heads, HUD heads and select portraits: routing, renaming, install through the build (no game files)."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legends_models import heads, roster
from legends_models.engines import tiers
from legends_models.engines.tiers import Candidate
import test_cli
from test_cli import run_cli


def igb_bytes(version=0x80000004, extra=b''):
    head = bytearray(48)
    struct.pack_into('<II', head, 40, 0xFADA, version)
    return bytes(head) + extra


def field(text, plen=None):
    """An IGB v4/v6 string field: <u32 padded length><bytes, NUL, zero padded to 4>."""
    raw = text.encode() + b'\0'
    plen = plen or (len(raw) + 3) // 4 * 4
    return struct.pack('<I', plen) + raw + b'\0' * (plen - len(raw))


class Ids(unittest.TestCase):
    def test_paths_follow_the_builder_namespaces(self):
        beast = roster.BY_KEY['beast']
        self.assertEqual(heads.port_paths(beast), {'hud': 'HUD/hud_head_14501.IGB', 'hud3d': 'UI/hud/characters/14501.IGB',
                                                   'select': 'UI/models/characters/14501.IGB'})

    def test_professor_x_astral_portrait_is_variant_01(self):
        px = roster.BY_KEY['profxastral']
        paths = heads.port_paths(px)
        self.assertEqual(paths['hud'], 'HUD/hud_head_15104.IGB')
        self.assertEqual(paths['select'], 'UI/models/characters/15101.IGB')

    def test_every_hero_has_a_four_digit_plus_prefix_portrait(self):
        for h in roster.HEROES:
            self.assertEqual(len(heads.portrait_id(h)), 5)
            self.assertTrue(heads.portrait_id(h).endswith('01'))


class Rename(unittest.TestCase):
    def test_exact_fields_renamed_in_place(self):
        data = b'xx' + field('0501') + field('0501_outline') + field('0501_skel') + field('0502') + field('other0501')
        out, n, problems = heads.rename_ids(data, '05', '14501')
        self.assertEqual((n, problems, len(out)), (4, [], len(data)))
        self.assertIn(field('14501', 8), out)
        self.assertIn(field('14501_outline', 16), out)
        self.assertNotIn(b'other14501', out)          # only whole fields

    def test_not_a_length_prefixed_field_is_left_alone(self):
        data = struct.pack('<I', 3) + b'0501\0'     # wrong length prefix
        out, n, problems = heads.rename_ids(data, '05', '14501')
        self.assertEqual((out, n, problems), (data, 0, []))

    def test_name_that_does_not_fit_is_reported(self):
        self.assertEqual(heads.rename_ids(field('0501'), '05', '14501')[1], 1)
        out, n, problems = heads.rename_ids(field('0501'), '05', '14501999')      # 8 chars + NUL > padded 8
        self.assertEqual((out, n, len(problems)), (field('0501'), 0, 1))


class Check(unittest.TestCase):
    def test_header_checks(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / 'v4.igb').write_bytes(igb_bytes())
            (d / 'v6.igb').write_bytes(igb_bytes(0xF0000006))
            (d / 'v8.igb').write_bytes(igb_bytes(0xF0000008))
            (d / 'junk.igb').write_bytes(b'x' * 100)
            (d / 'empty.igb').write_bytes(b'')
            self.assertIsNone(heads.check_head_file(d / 'v4.igb'))
            self.assertIsNone(heads.check_head_file(d / 'v6.igb'))
            self.assertIn('version', heads.check_head_file(d / 'v8.igb'))
            self.assertEqual(heads.check_head_file(d / 'junk.igb'), 'not an IGB file')
            self.assertIn('missing', heads.check_head_file(d / 'empty.igb'))
            self.assertIn('missing', heads.check_head_file(d / 'nope.igb'))


class CommunityRoutes(unittest.TestCase):
    def test_roles_villain_skipped_and_3d_head_renamed(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / 'src'
            d.mkdir()
            (d / 'hud_head_05XX.igb').write_bytes(igb_bytes())
            (d / 'hud_head_05XX (Villain).igb').write_bytes(igb_bytes(extra=b'villain'))
            (d / '05XX (3D Head).igb').write_bytes(igb_bytes(extra=field('0501') + field('0501_outline')))
            (d / '05XX (Character Select Portrait).igb').write_bytes(igb_bytes())
            (d / '05XX (Skin).igb').write_bytes(igb_bytes())
            out = Path(t) / 'out'
            routes, notes = heads.community_routes(d, roster.BY_KEY['beast'], out_dir=out)
            self.assertEqual(set(routes), {'HUD/hud_head_14501.IGB', 'UI/hud/characters/14501.IGB', 'UI/models/characters/14501.IGB'})
            self.assertEqual(routes['HUD/hud_head_14501.IGB'].name, 'hud_head_05XX.igb')       # not the villain border
            renamed = routes['UI/hud/characters/14501.IGB']
            self.assertEqual(renamed.parents[3], out)                                  # written beside, source untouched
            self.assertIn(field('14501', 8), renamed.read_bytes())
            self.assertIn(b'0501', (d / '05XX (3D Head).igb').read_bytes())
            self.assertTrue(any('renamed to 14501' in n for n in notes))

    def test_missing_role_stays_stock_and_bad_file_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / 'hud_head_05XX.igb').write_bytes(b'not an igb at all' * 10)
            (d / '05XX (3D Head).igb').write_bytes(igb_bytes())
            routes, notes = heads.community_routes(d, roster.BY_KEY['beast'])
            self.assertEqual(set(routes), {'UI/hud/characters/14501.IGB'})
            self.assertTrue(any(n.startswith('hud: ') and 'refused' in n for n in notes))
            self.assertTrue(any(n.startswith('select: ') and 'stock kept' in n for n in notes))

    def test_platform_sets_prefer_the_chosen_skins_folder(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for sub in ('for XML1 (GC)', 'for XML2 (PC, PS2, and Xbox)'):
                (d / sub).mkdir()
                (d / sub / '36XX (3D Head).igb').write_bytes(igb_bytes(extra=sub.encode()))
                (d / sub / '36XX (Skin).igb').write_bytes(igb_bytes())
            near = d / 'for XML2 (PC, PS2, and Xbox)' / '36XX (Skin).igb'
            routes, notes = heads.community_routes(d, roster.BY_KEY['magma'], near=near)
            self.assertEqual(routes['UI/hud/characters/15801.IGB'].parent.name, 'for XML2 (PC, PS2, and Xbox)')
            self.assertFalse(any('several files' in n for n in notes))   # platform_root picks the XML2 PC set first


class Driver(unittest.TestCase):
    def test_head_driver_compiles(self):
        import py_compile
        path = Path(heads.__file__).parent / 'engines' / 'drivers' / 'heads_mua1.py'
        with tempfile.TemporaryDirectory() as t:
            py_compile.compile(str(path), cfile=str(Path(t) / 'x.pyc'), doraise=True)


class Collect(unittest.TestCase):
    def ctx(self, root):
        return mock.Mock(work=Path(root), cel=True, mua1=None)

    def test_tiers_without_equivalents_keep_stock(self):
        with tempfile.TemporaryDirectory() as t:
            for tier in ('mua2', 'xml2', 'sharpen'):
                routes, notes = heads.collect(roster.BY_KEY['wolverine'], mock.Mock(tier=tier, source='x'), self.ctx(t))
                self.assertEqual(routes, {})
                self.assertIn('stock kept', notes[0])

    def test_an_unusable_source_never_raises(self):
        with tempfile.TemporaryDirectory() as t:
            routes, notes = heads.collect(roster.BY_KEY['wolverine'], mock.Mock(tier='mua1', source='0303'), self.ctx(t))
            self.assertEqual(routes, {})
            self.assertIn('MUA1', notes[0])


class Installed(test_cli.Build):
    """The heads travel with the hero folder: same file, marker hashes, verify, credits."""

    def with_heads(self, tmp):
        src = Path(tmp) / 'headsrc'
        src.mkdir()
        files = {'HUD/hud_head_14501.IGB': igb_bytes(extra=b'hud'), 'UI/models/characters/14501.IGB': igb_bytes(extra=b'sel')}
        for rel, data in files.items():
            (src / rel.replace('/', '_')).write_bytes(data)
        routes = {rel: src / rel.replace('/', '_') for rel in files}
        return mock.patch.object(heads, 'collect', lambda hero, choice, ctx: (routes if hero.key == 'beast' else {}, ['n']))

    def test_actor_and_heads_published_together_per_hero(self):
        with tempfile.TemporaryDirectory() as t, self.with_heads(t):
            code, ev = self.build({}, '--mode', 'community', '--hero', 'beast', '--layout', 'per-hero')
            self.assertEqual(code, 0, ev[-1])
            folder = self.port / 'mods' / 'Community-Beast'
            self.assertTrue((folder / 'Actors' / '14501.IGB').is_file())
            self.assertTrue((folder / 'HUD' / 'hud_head_14501.IGB').is_file())
            self.assertTrue((folder / 'UI' / 'models' / 'characters' / '14501.IGB').is_file())
            marker = json.loads((folder / 'legends-models.json').read_text())
            self.assertEqual(set(marker['characters']['beast']['heads']), {'HUD/hud_head_14501.IGB', 'UI/models/characters/14501.IGB'})
            self.assertIn('HUD/hud_head_14501.IGB', marker['files'])
            self.assertNotIn('head_files', marker['characters']['beast'])
            code, ev = run_cli('verify', '--pack', 'Models-Community', *self.base)
            self.assertEqual((code, ev[-1]['ok']), (0, True))

    def test_no_heads_flag(self):
        with tempfile.TemporaryDirectory() as t, self.with_heads(t):
            code, ev = self.build({}, '--mode', 'community', '--hero', 'beast', '--no-heads')
            self.assertEqual(code, 0)
            self.assertFalse((self.port / 'mods' / 'Models-Community' / 'HUD').exists())

    def test_heads_only_writes_no_actor_and_converts_nothing(self):
        calls = []
        adapter = lambda h, c, x: calls.append(h.key)
        with tempfile.TemporaryDirectory() as t, self.with_heads(t):
            with mock.patch.dict(tiers.ADAPTERS, {k: adapter for k in roster.TIERS}):
                code, ev = run_cli('build', *self.base, '--mode', 'community', '--hero', 'beast', '--heads-only')
            self.assertEqual((code, calls), (0, []))
            folder = self.port / 'mods' / 'Community-Beast'
            self.assertFalse((folder / 'Actors').exists())
            self.assertTrue((folder / 'HUD' / 'hud_head_14501.IGB').is_file())

    def test_heads_only_without_any_head_is_refused_not_written(self):
        with tempfile.TemporaryDirectory() as t, self.with_heads(t):
            code, ev = run_cli('build', *self.base, '--mode', 'community', '--hero', 'storm', '--heads-only')
            self.assertNotEqual(code, 0)
            self.assertFalse((self.port / 'mods' / 'Community-Storm').exists())
            self.assertIn('storm', ev[-1]['refused'])


if __name__ == '__main__':
    unittest.main()
