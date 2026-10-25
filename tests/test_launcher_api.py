# SPDX-License-Identifier: MIT
"""The launcher-page API additions (0.3.0): costume display data, download hashes, --offline, group-qualified
folder names + migration, port-build fingerprint / stale verify, and the frozen-exe Python shim."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legends_models import build, community, errors, loadorder, packs, portbuild, roster
from legends_models.engines import tiers

from test_cli import Fixture, fake_adapter, run_cli


class PerHero(Fixture):
    def build(self, verdicts, *extra):
        fake = fake_adapter(Path(self.tmp.name) / 'cand', verdicts)
        with mock.patch.dict(tiers.ADAPTERS, {t: fake for t in roster.TIERS}):
            return run_cli('build', *self.base, '--layout', 'per-hero', *extra)


class Costumes(Fixture):
    def test_scan_sends_labels_authors_and_credit(self):
        code, ev = run_cli('scan', *self.base, '--hero', 'wolverine', '--hero', 'beast')
        self.assertEqual(code, 0)
        w = ev[-1]['heroes']['wolverine']['tiers']
        mua1 = {c['id']: c for c in w['mua1']['costume_info']}
        self.assertEqual(sorted(mua1), ['0301', '0302', '0303'])
        self.assertEqual(mua1['0303']['label'], 'Classic yellow/blue')
        self.assertEqual(mua1['0303']['author'], '')
        self.assertIn('your own copy', mua1['0303']['credit'])
        comm = {c['id']: c for c in w['community']['costume_info']}
        self.assertEqual(sorted(comm), ['Original', 'Ultimate XML1'])
        self.assertEqual(comm['Original']['author'], 'BaconWizard17')
        self.assertEqual(w['community']['author'], 'BaconWizard17')
        self.assertTrue(comm['Original']['source_url'].startswith('https://marvelmods.com/'))
        self.assertNotIn('preview', comm['Original'])          # nothing extracted on this PC yet
        self.assertEqual([c['id'] for c in w['mua1']['costume_info']], w['mua1']['costumes'])

    def test_preview_comes_from_an_extracted_archive_only(self):
        from legends_models import costumes
        from legends_models.workspace import port_work_path
        pw = port_work_path(self.work, self.port)
        folder = pw / 'community' / 'extracted' / 'beast' / 'classic'
        folder.mkdir(parents=True)
        (folder / '05XX (Character Select Portrait).png').write_bytes(b'\x89PNG')
        self.assertIsNone(costumes.community_preview(pw, 'beast', 'Classic'))     # not verified -> no preview
        (folder / '.verified').write_text('x')
        code, ev = run_cli('scan', *self.base, '--hero', 'beast')
        row = ev[-1]['heroes']['beast']['tiers']['community']['costume_info'][0]
        self.assertTrue(row['preview'].endswith('(Character Select Portrait).png'))


class Downloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.data = b'Rar!' + b'x' * 60
        import hashlib
        self.rec = dict(character='Beast', costume='Classic', archive='05XX_Classic.rar', bytes=len(self.data),
                        sha256=hashlib.sha256(self.data).hexdigest(),
                        url='https://www.mediafire.com/file/abc/05XX_Classic.rar/file')

    def tearDown(self):
        self.tmp.cleanup()

    def test_download_event_carries_sha256(self):
        events = []

        class Rep:
            def emit(self, event, **kw):
                events.append(dict(kw, event=event))
        community.fetch(self.rec, self.cache, get=lambda url, limit: self.data, report=Rep())
        dl = [e for e in events if e['event'] == 'download']
        self.assertEqual(len(dl), 1)
        self.assertEqual(dl[0]['sha256'], self.rec['sha256'])
        self.assertEqual((dl[0]['bytes'], dl[0]['character'], dl[0]['costume']), (len(self.data), 'Beast', 'Classic'))

    def test_offline_uses_the_cache_and_refuses_downloads(self):
        def no_net(url, limit):
            raise AssertionError('network used while offline')
        with self.assertRaises(errors.ToolError) as e:
            community.fetch(self.rec, self.cache, get=no_net, offline=True)
        self.assertEqual(e.exception.code, errors.DOWNLOAD)
        community.fetch(self.rec, self.cache, get=lambda url, limit: self.data)          # now cached
        self.assertTrue(community.fetch(self.rec, self.cache, get=no_net, offline=True).is_file())


class Offline(Fixture):
    def test_plan_offline_drops_uncached_community_costumes(self):
        code, ev = run_cli('plan', '--mode', 'community', '--hero', 'beast', '--offline', *self.base)
        self.assertEqual(code, 0)
        row = ev[-1]['rows'][0]
        self.assertEqual(row['tier'], 'stock')
        self.assertIn('download cache', row['reason'])
        code, ev = run_cli('plan', '--mode', 'community', '--hero', 'beast', *self.base)
        self.assertEqual(ev[-1]['rows'][0]['tier'], 'community')

    def test_fetch_offline_exits_20(self):
        code, ev = run_cli('fetch', '--hero', 'beast', '--offline', *self.base)
        self.assertEqual(code, errors.DOWNLOAD)


class FolderNames(PerHero):
    def test_names(self):
        self.assertEqual(build.hero_folder('beast', 'community', 'Models-Community'), 'Community-Beast')
        self.assertEqual(build.hero_folder('beast', 'community', 'Models-Upgraded'), 'Upgraded-Community-Beast')
        self.assertEqual(build.hero_folder('phoenix', 'mua1', 'Models-Upgraded'), 'Upgraded-MUA-Jean')
        self.assertEqual(build.hero_folder('storm', 'xml2', 'Models-XML2-Look'), 'XML2-Storm')
        self.assertEqual(build.hero_folder('storm', 'sharpen', 'Models-Sharpened'), 'Sharpened-Storm')
        self.assertEqual(build.hero_folder('storm', 'community', 'MyPack'), 'MyPack-Community-Storm')

    def test_community_and_upgraded_never_touch_each_other(self):
        mods = self.port / 'mods'
        self.build({}, '--mode', 'community', '--hero', 'beast')
        self.build({}, '--mode', 'upgraded', '--hero', 'beast')       # Beast has no MUA model: community tier
        self.assertEqual(packs.group_of(mods / 'Community-Beast'), 'Models-Community')
        self.assertEqual(packs.group_of(mods / 'Upgraded-Community-Beast'), 'Models-Upgraded')
        text = loadorder.read(mods)
        self.assertEqual((loadorder.is_enabled(text, 'Community-Beast'), loadorder.is_enabled(text, 'Upgraded-Community-Beast')),
                         (False, True))
        code, ev = run_cli('remove', '--pack', 'Models-Community', *self.base)
        self.assertEqual(code, 0)
        self.assertFalse((mods / 'Community-Beast').exists())
        self.assertTrue((mods / 'Upgraded-Community-Beast' / 'Actors' / '14501.IGB').is_file())
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['ok']), (0, True))

    def test_migration_of_a_0_2_install(self):
        """0.2.x wrote Upgraded's Beast as Community-Beast; a 0.3 build renames it before writing anything."""
        mods = self.port / 'mods'
        self.build({}, '--mode', 'upgraded', '--hero', 'beast', '--hero', 'storm')
        for new, old in [('Upgraded-Community-Beast', 'Community-Beast'), ('Upgraded-MUA-Storm', 'MUA-Storm')]:
            os.replace(mods / new, mods / old)
            (mods / 'load-order.txt').write_text((mods / 'load-order.txt').read_text().replace(new, old))
        (mods / 'load-order.txt').write_text('+Other-Mod\n' + (mods / 'load-order.txt').read_text())
        before = {f: packs.verify(mods / f)['ok'] for f in ('Community-Beast', 'MUA-Storm')}
        self.assertEqual(before, {'Community-Beast': True, 'MUA-Storm': True})
        code, ev = self.build({}, '--mode', 'community', '--hero', 'beast')
        self.assertEqual(code, 0)
        self.assertEqual(packs.group_of(mods / 'Community-Beast'), 'Models-Community')
        self.assertEqual(packs.group_of(mods / 'Upgraded-Community-Beast'), 'Models-Upgraded')
        self.assertEqual(packs.group_of(mods / 'Upgraded-MUA-Storm'), 'Models-Upgraded')
        self.assertFalse((mods / 'MUA-Storm').exists())
        lines = (mods / 'load-order.txt').read_text().split()
        self.assertEqual(lines[0], '+Other-Mod')                                   # the player's line kept, first
        self.assertIn('-Upgraded-Community-Beast', lines)
        self.assertIn('-Upgraded-MUA-Storm', lines)
        self.assertIn('+Community-Beast', lines)
        self.assertNotIn('-MUA-Storm', lines)
        self.assertTrue(any('renamed MUA-Storm to Upgraded-MUA-Storm' in e.get('message', '') for e in ev))
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['ok'], sorted(ev[-1]['folders'])), (0, True, ['Upgraded-Community-Beast', 'Upgraded-MUA-Storm']))

    def test_a_folder_of_another_group_is_never_taken_over(self):
        mods = self.port / 'mods'
        self.build({}, '--mode', 'community', '--hero', 'beast')
        with self.assertRaises(errors.ToolError) as e:
            packs.check_same_group(mods, 'Community-Beast', 'Models-Upgraded')
        self.assertEqual(e.exception.code, errors.UNSAFE)
        packs.check_same_group(mods, 'Community-Beast', 'Models-Community')


class PortBuild(PerHero):
    def test_marker_records_the_port_and_verify_reports_stale(self):
        mods = self.port / 'mods'
        self.build({}, '--mode', 'upgraded', '--hero', 'storm')
        marker = packs.read_marker(mods / 'Upgraded-MUA-Storm')
        pb = marker['port_build']
        self.assertIn('Actors/14401.IGB', pb['files'])
        self.assertIn('libIGSg.dll', pb['files'])
        self.assertEqual(len(pb['fingerprint']), 64)
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['stale']), (0, False))
        # a port rebuild that rewrites the same bytes is not stale
        (self.port / 'Actors' / '14401.IGB').write_bytes(b'stock')
        self.assertEqual(run_cli('verify', '--pack', 'Models-Upgraded', *self.base)[0], 0)
        # one that changes the hero's stock skin is
        (self.port / 'Actors' / '14401.IGB').write_bytes(b'stoCk')
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['ok'], ev[-1]['stale']), (errors.STALE, True, True))
        self.assertEqual(ev[-1]['stale_folders'], ['Upgraded-MUA-Storm'])
        self.assertEqual(ev[-1]['port_changed'], ['Upgraded-MUA-Storm: Actors/14401.IGB'])
        # a damaged pack is a mismatch first
        (mods / 'Upgraded-MUA-Storm' / 'Actors' / '14401.IGB').write_bytes(b'broken')
        self.assertEqual(run_cli('verify', '--pack', 'Models-Upgraded', *self.base)[0], errors.VERIFY_MISMATCH)

    def test_pack_layout_records_too_and_old_markers_are_unknown(self):
        mods = self.port / 'mods'
        fake = fake_adapter(Path(self.tmp.name) / 'cand', {})
        with mock.patch.dict(tiers.ADAPTERS, {t: fake for t in roster.TIERS}):
            run_cli('build', *self.base, '--layout', 'pack', '--mode', 'upgraded', '--hero', 'storm')
        self.assertIn('port_build', packs.read_marker(mods / 'Models-Upgraded'))
        (self.port / 'libIGSg.dll').write_bytes(b'new engine')
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['stale']), (errors.STALE, True))
        self.assertEqual(portbuild.check(self.port, None)['stale'], None)

    def test_builder_version_is_recorded_when_the_port_says_it(self):
        (self.port / '_build').mkdir()
        (self.port / '_build' / 'manifest.json').write_text(json.dumps({'builder': '0.4.0-beta.5', 'files': {}}))
        rec = portbuild.record(self.port, {'Actors/14401.IGB': {}}, ['storm'])
        self.assertEqual(rec['builder'], '0.4.0-beta.5')


class FrozenShim(unittest.TestCase):
    def test_python_shim_runs_a_script_and_c_code(self):
        from legends_models import __main__ as entry
        with tempfile.TemporaryDirectory() as t:
            script = Path(t) / 's.py'
            script.write_text('import sys, helper\nopen(sys.argv[1], "w").write(helper.X + ":" + __name__)\n')
            (Path(t) / 'helper.py').write_text('X = "ok"\n')
            out = Path(t) / 'out.txt'
            saved = (list(sys.argv), list(sys.path))
            try:
                self.assertEqual(entry.python_shim(['-B', str(script), str(out)]), 0)
            finally:
                sys.argv[:], sys.path[:] = saved
                sys.modules.pop('helper', None)
            self.assertEqual(out.read_text(), 'ok:__main__')
            self.assertIsNone(entry.python_shim(['scan', '--json']))      # tool arguments: not a shim call


if __name__ == '__main__':
    unittest.main()
