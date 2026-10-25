# SPDX-License-Identifier: MIT
import json
import tempfile
import unittest
from pathlib import Path

from legends_models import loadorder, packs
from legends_models.errors import UNSAFE, ToolError


class Packs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mods = self.root / 'game' / 'mods'
        self.src = self.root / 'actor.IGB'
        self.src.write_bytes(b'converted actor')

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, name='Models-Community', data=b'converted actor', **kw):
        self.src.write_bytes(data)
        with packs.Staging(self.mods, name) as st:
            st.add('Actors/14501.IGB', self.src)
            st.write_text('CREDITS.md', '# Credits\n')
            return st.publish('community', {'beast': {'file': 'Actors/14501.IGB'}}, **kw)

    def test_publish_writes_pack_marker_and_load_order(self):
        marker = self.publish()
        pack = self.mods / 'Models-Community'
        self.assertEqual((pack / 'Actors/14501.IGB').read_bytes(), b'converted actor')
        self.assertEqual(packs.read_marker(pack)['generator'], 'legends-models')
        self.assertEqual(set(marker['files']), {'Actors/14501.IGB', 'CREDITS.md'})
        self.assertEqual(loadorder.read(self.mods), '+Models-Community\n')
        self.assertEqual([p.name for p in self.mods.iterdir() if p.name.startswith('.')], [])

    def test_republish_replaces_and_keeps_user_entries(self):
        self.mods.mkdir(parents=True)
        (self.mods / 'load-order.txt').write_text('# mine\n+Better HUD\n')
        self.publish()
        self.publish(data=b'second build')
        self.assertEqual((self.mods / 'Models-Community/Actors/14501.IGB').read_bytes(), b'second build')
        self.assertEqual(loadorder.read(self.mods), '# mine\n+Better HUD\n+Models-Community\n')

    def test_switching_mode_disables_the_other_owned_pack(self):
        self.publish('Models-Community')
        self.publish('Models-Upgraded')
        text = loadorder.read(self.mods)
        self.assertEqual(loadorder.is_enabled(text, 'Models-Community'), False)
        self.assertEqual(loadorder.is_enabled(text, 'Models-Upgraded'), True)
        self.assertEqual(packs.owned_packs(self.mods), ['Models-Community', 'Models-Upgraded'])

    def test_disabled_publish(self):
        self.publish(enable=False)
        self.assertEqual(loadorder.read(self.mods), '-Models-Community\n')

    def test_refuses_to_replace_or_delete_foreign_folders(self):
        foreign = self.mods / 'Someones Skin'
        foreign.mkdir(parents=True)
        (foreign / 'file.txt').write_text('x')
        with self.assertRaises(ToolError) as e:
            self.publish('Someones Skin')
        self.assertEqual(e.exception.code, UNSAFE)
        with self.assertRaises(ToolError):
            packs.remove(self.mods, 'Someones Skin')
        self.assertTrue((foreign / 'file.txt').exists())

    def test_failed_build_leaves_previous_pack_untouched(self):
        self.publish()
        with self.assertRaises(RuntimeError):
            with packs.Staging(self.mods, 'Models-Community') as st:
                st.write_text('Actors/14501.IGB', 'half written')
                raise RuntimeError('cancelled')
        self.assertEqual((self.mods / 'Models-Community/Actors/14501.IGB').read_bytes(), b'converted actor')
        self.assertEqual([p.name for p in self.mods.iterdir() if p.name.startswith('.')], [])
        self.assertTrue(packs.verify(self.mods / 'Models-Community')['ok'])

    def test_stale_staging_from_a_killed_run_is_cleaned(self):
        self.mods.mkdir(parents=True)
        (self.mods / '.legends-models-staging-Models-Community-1-1').mkdir()
        self.publish()
        self.assertFalse((self.mods / '.legends-models-staging-Models-Community-1-1').exists())

    def test_staging_paths_cannot_escape(self):
        with self.assertRaises(ToolError):
            with packs.Staging(self.mods, 'P') as st:
                st.add('../../outside.IGB', self.src)

    def test_remove(self):
        self.mods.mkdir(parents=True)
        (self.mods / 'load-order.txt').write_text('+A\n')
        self.publish()
        r = packs.remove(self.mods, 'Models-Community')
        self.assertEqual(r, dict(pack='Models-Community', folder_removed=True, load_order_entry_removed=True))
        self.assertFalse((self.mods / 'Models-Community').exists())
        self.assertEqual(loadorder.read(self.mods), '+A\n')
        self.assertFalse(packs.remove(self.mods, 'Models-Community')['folder_removed'])

    def test_verify(self):
        self.publish()
        pack = self.mods / 'Models-Community'
        self.assertTrue(packs.verify(pack)['ok'])
        (pack / 'Actors/14501.IGB').write_bytes(b'changed')
        (pack / 'extra.txt').write_text('x')
        (pack / 'CREDITS.md').unlink()
        r = packs.verify(pack)
        self.assertEqual((r['ok'], r['changed'], r['extra'], r['missing']),
                         (False, ['Actors/14501.IGB'], ['extra.txt'], ['CREDITS.md']))

    def test_verify_against_external_manifest(self):
        self.publish()
        pack = self.mods / 'Models-Community'
        good = self.root / 'm.json'
        good.write_text(json.dumps({'Actors/14501.IGB': packs.sha256(pack / 'Actors/14501.IGB'),
                                    'CREDITS.md': packs.sha256(pack / 'CREDITS.md')}))
        self.assertTrue(packs.verify(pack, good)['ok'])
        good.write_text(json.dumps({'files': {'Actors/14501.IGB': {'sha256': '0' * 64}}}))
        self.assertFalse(packs.verify(pack, good)['ok'])

    def test_output_location_guard(self):
        mua = self.root / 'MUA'
        mua.mkdir()
        self.assertEqual(packs.check_output_location(self.root / 'game', [mua, None]), (self.root / 'game' / 'mods').resolve())
        with self.assertRaises(ToolError):
            packs.check_output_location(mua / 'sub', [mua])


if __name__ == '__main__':
    unittest.main()


class Workspace(unittest.TestCase):
    def test_port_work_resets_when_the_port_changes(self):
        import os
        from legends_models import roster, workspace
        with tempfile.TemporaryDirectory() as t:
            port = Path(t) / 'port'
            (port / 'Actors').mkdir(parents=True)
            skin = port / 'Actors' / f'{roster.HEROES[0].skin}.IGB'
            skin.write_bytes(b'v1')
            work = Path(t) / 'work'
            a = workspace.port_work(work, port)
            (a / 'xml2' / 'cache').mkdir(parents=True)
            self.assertEqual(workspace.port_work(work, port), a)
            self.assertTrue((a / 'xml2' / 'cache').exists())          # unchanged port keeps caches
            skin.write_bytes(b'v2 longer')
            os.utime(skin, (1, 1))
            self.assertEqual(workspace.port_work(work, port), a)
            self.assertFalse((a / 'xml2').exists())                   # changed port wipes them
            other = Path(t) / 'port2'
            (other / 'Actors').mkdir(parents=True)
            self.assertNotEqual(workspace.port_work(work, other), a)
