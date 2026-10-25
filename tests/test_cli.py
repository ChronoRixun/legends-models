# SPDX-License-Identifier: MIT
"""CLI + build orchestration on fake installs, with the converters replaced by fakes (no game data)."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legends_models import cli, errors, gate, roster
from legends_models.engines import tiers

from synth import mua_bin
from test_gate import mua1_report


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(map(str, argv)))
    events = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith('{')]
    return code, events


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.port = r / 'port'
        (self.port / 'Actors').mkdir(parents=True)
        for h in roster.HEROES:
            (self.port / 'Actors' / f'{h.skin}.IGB').write_bytes(b'stock')
        for donor in ['0501', '0301', '0401']:
            (self.port / 'Actors' / f'{donor}.IGB').write_bytes(b'xml2')
        (self.port / 'libIGSg.dll').write_bytes(b'')
        self.mua1 = r / 'MUA'
        (self.mua1 / 'actors').mkdir(parents=True)
        for i in ['0301', '0302', '0303', '0401']:
            (self.mua1 / 'actors' / f'{i}.igb').write_bytes(b'mua1')
        self.mua2 = r / 'MUA2'
        self.mua2.mkdir()
        (self.mua2 / 'actors.bin').write_bytes(mua_bin([('0301.igz', b'x'), ('0601.igz', b'y')]))
        for n in ('materials.bin', 'textures.bin'):
            (self.mua2 / n).write_bytes(b'')
        self.work = r / 'work'
        self.base = ['--port', self.port, '--mua1', self.mua1, '--mua2', self.mua2, '--work', self.work, '--json',
                     '--steam', r / 'nosteam']

    def tearDown(self):
        self.tmp.cleanup()


class Commands(Fixture):
    def test_scan(self):
        code, ev = run_cli('scan', *self.base)
        self.assertEqual(code, 0)
        res = ev[-1]
        self.assertEqual(res['event'], 'result')
        self.assertTrue(res['installs']['port']['found'])
        self.assertEqual(res['installs']['mua1']['flavour'], '2006-loose')
        self.assertEqual(res['installs']['mua2']['flavour'], 'pc-bin')
        w = res['heroes']['wolverine']['tiers']
        self.assertTrue(w['mua2']['available'] and w['mua1']['available'] and w['xml2']['available'])
        self.assertFalse(res['heroes']['iceman']['tiers']['mua1']['available'])   # 0801 not in the fake MUA1
        self.assertEqual(res['heroes']['jubilee']['tiers']['xml2']['status'], 'none')

    def test_plan_upgraded(self):
        code, ev = run_cli('plan', '--mode', 'upgraded', *self.base)
        self.assertEqual(code, 0)
        rows = {r['hero']: r for r in ev[-1]['rows']}
        self.assertEqual((rows['wolverine']['tier'], rows['wolverine']['fallbacks']), ('mua2', ['mua1', 'community']))
        self.assertEqual(rows['storm']['tier'], 'mua1')
        self.assertEqual(rows['iceman']['tier'], 'community')
        self.assertEqual(rows['profxastral']['tier'], 'community')
        self.assertEqual(ev[-1]['pack'], 'Models-Upgraded')

    def test_plan_without_port_still_reports(self):
        code, ev = run_cli('plan', '--mode', 'sharpen', '--port', Path(self.tmp.name) / 'nope', '--work', self.work, '--json')
        self.assertEqual(code, 0)
        self.assertTrue(all(r['tier'] == 'stock' for r in ev[-1]['rows']))

    def test_usage_errors(self):
        self.assertEqual(run_cli('plan', '--mode', 'bogus')[0], errors.USAGE)
        self.assertEqual(run_cli('plan', '--hero', 'deadpool', *self.base)[0], errors.USAGE)
        self.assertEqual(run_cli('plan', '--mode', 'upgraded', '--costume', 'wolverine=mua1:9999', *self.base)[0], errors.USAGE)

    def test_build_without_port_is_missing_input(self):
        code, ev = run_cli('build', '--mode', 'sharpen', '--port', Path(self.tmp.name) / 'nope', '--work', self.work, '--json')
        self.assertEqual(code, errors.MISSING_INPUT)
        self.assertEqual(ev[-1]['event'], 'error')

    def test_remove_and_verify_unknown_pack(self):
        code, ev = run_cli('remove', '--pack', 'Models-Community', *self.base)
        self.assertEqual(code, 0)
        self.assertFalse(ev[-1]['folder_removed'])
        code, ev = run_cli('verify', '--pack', 'Models-Community', *self.base)
        self.assertEqual(code, errors.VERIFY_MISMATCH)


def fake_adapter(tmp, verdicts):
    """verdicts: {(hero, tier): 'pass'|'fail'|'error'}; writes a fake actor + a validation report bound to it."""
    def adapter(hero, choice, ctx):
        v = verdicts.get((hero.key, choice.tier), 'pass')
        if v == 'error':
            raise errors.ToolError('engine could not run', errors.MISSING_INPUT)
        d = Path(tmp) / f'{hero.key}-{choice.tier}'
        d.mkdir(parents=True, exist_ok=True)
        cand = d / f'{hero.skin}.IGB'
        cand.write_bytes(f'{hero.key}/{choice.tier}/{choice.source}'.encode())
        rep = mua1_report()
        rep['converted_sha256'] = gate.sha256(cand)
        if v == 'fail':
            rep['animations']['run']['models']['converted']['max_chain_distance'] = 11.0
        (d / 'validation.json').write_text(json.dumps(rep))
        return tiers.Candidate(cand, d / 'validation.json', 'deform', None, ['fake'])
    return adapter


class Build(Fixture):
    def build(self, verdicts, *extra):
        fake = fake_adapter(Path(self.tmp.name) / 'cand', verdicts)
        with mock.patch.dict(tiers.ADAPTERS, {t: fake for t in roster.TIERS}):
            return run_cli('build', *self.base, '--layout', 'pack', *extra)

    def test_upgraded_with_fallbacks_partial_result(self):
        verdicts = {('wolverine', 'mua2'): 'fail', ('storm', 'mua1'): 'error', ('storm', 'community'): 'fail',
                    ('beast', 'community'): 'pass'}
        code, ev = self.build(verdicts, '--mode', 'upgraded', '--hero', 'wolverine', '--hero', 'storm', '--hero', 'beast')
        self.assertEqual(code, errors.PARTIAL)
        res = ev[-1]
        self.assertEqual(res['characters']['wolverine']['tier'], 'mua1')          # MUA2 failed the gate -> MUA1
        self.assertEqual(res['characters']['wolverine']['fallback_from'][0]['tier'], 'mua2')
        self.assertEqual(res['characters']['beast']['tier'], 'community')
        self.assertIn('storm', res['refused'])
        pack = self.port / 'mods' / 'Models-Upgraded'
        self.assertEqual(sorted(p.name for p in (pack / 'Actors').iterdir()), ['14301.IGB', '14501.IGB'])
        self.assertFalse((pack / 'Actors' / '14401.IGB').exists())                 # refused hero stays stock
        self.assertIn('BaconWizard17', (pack / 'CREDITS.md').read_text())
        self.assertEqual((self.port / 'mods' / 'load-order.txt').read_text(), '+Models-Upgraded\n')
        gates = [e for e in ev if e['event'] == 'gate']
        self.assertEqual([g['passed'] for g in gates], [False, True, False, True])
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['ok'], ev[-1]['load_order']), (0, True, 'enabled'))

    def test_nothing_written_when_everything_fails(self):
        code, ev = self.build({('beast', 'community'): 'fail'}, '--mode', 'community', '--hero', 'beast')
        self.assertEqual(code, errors.NOTHING_WRITTEN)
        self.assertFalse((self.port / 'mods' / 'Models-Community').exists())

    def test_download_failure_is_reported_as_such(self):
        def offline(hero, choice, ctx):
            raise errors.ToolError('could not fetch 05XX_Classic.rar', errors.DOWNLOAD)
        with mock.patch.dict(tiers.ADAPTERS, {t: offline for t in roster.TIERS}):
            code, ev = run_cli('build', *self.base, '--mode', 'community', '--hero', 'beast')
        self.assertEqual(code, errors.DOWNLOAD)
        self.assertIn('could not fetch', ev[-1]['refused']['beast']['attempts'][0]['error'])

    def test_second_build_uses_the_result_cache(self):
        self.assertEqual(self.build({}, '--mode', 'upgraded', '--hero', 'storm')[0], 0)
        boom = lambda hero, choice, ctx: self.fail('converter re-ran for unchanged inputs')
        with mock.patch.dict(tiers.ADAPTERS, {t: boom for t in roster.TIERS}):
            code, ev = run_cli('build', *self.base, '--mode', 'upgraded', '--hero', 'storm')
        self.assertEqual(code, 0)
        self.assertTrue(any(e['event'] == 'cache' for e in ev))
        self.assertIn('cached result (same inputs)', ev[-1]['characters']['storm']['notes'])
        # a changed input (MUA1 file touched) misses the cache
        import os
        os.utime(self.mua1 / 'actors' / '0401.igb', (5, 5))
        calls = []
        fake = fake_adapter(Path(self.tmp.name) / 'cand2', {})
        with mock.patch.dict(tiers.ADAPTERS, {t: (lambda h, c, x: (calls.append(h.key), fake(h, c, x))[1]) for t in roster.TIERS}):
            self.assertEqual(run_cli('build', *self.base, '--mode', 'upgraded', '--hero', 'storm')[0], 0)
        self.assertEqual(calls, ['storm'])
        # --rebuild ignores the cache
        with mock.patch.dict(tiers.ADAPTERS, {t: (lambda h, c, x: (calls.append(h.key), fake(h, c, x))[1]) for t in roster.TIERS}):
            self.assertEqual(run_cli('build', *self.base, '--mode', 'upgraded', '--hero', 'storm', '--rebuild')[0], 0)
        self.assertEqual(calls, ['storm', 'storm'])

    def test_keep_existing_carries_unchanged_characters(self):
        self.assertEqual(self.build({}, '--mode', 'sharpen', '--hero', 'beast', '--hero', 'rogue')[0], 0)
        code, ev = self.build({}, '--mode', 'sharpen', '--hero', 'storm')      # --hero implies keep-existing
        self.assertEqual(code, 0)
        chars = ev[-1]['characters']
        self.assertEqual(set(chars), {'beast', 'rogue', 'storm'})
        self.assertTrue(chars['beast'].get('carried_over'))

    def test_owner_acceptance_is_by_exact_output_hash(self):
        fake = fake_adapter(Path(self.tmp.name) / 'cand', {})
        produced = {}

        def adapter(hero, choice, ctx):
            c = fake(hero, choice, ctx)
            produced['sha'] = gate.sha256(c.path)
            return c
        with mock.patch.dict(tiers.ADAPTERS, {t: adapter for t in roster.TIERS}):
            class Records(dict):
                def get(self, key, default=None):
                    return {'by': 'owner'} if key == produced.get('sha') else default
            with mock.patch('legends_models.build.acceptance_records', Records):
                code, ev = run_cli('build', *self.base, '--mode', 'upgraded', '--hero', 'wolverine')
        self.assertEqual(code, 0)
        self.assertTrue(ev[-1]['characters']['wolverine']['owner_accepted'])

    def test_remove_restores_stock(self):
        self.build({}, '--mode', 'community', '--hero', 'beast')
        code, ev = run_cli('remove', '--pack', 'Models-Community', *self.base)
        self.assertEqual((code, ev[-1]['folder_removed'], ev[-1]['load_order_entry_removed']), (0, True, True))
        self.assertFalse((self.port / 'mods' / 'Models-Community').exists())
        self.assertEqual((self.port / 'Actors' / '14501.IGB').read_bytes(), b'stock')

    def test_json_progress_stream_shape(self):
        code, ev = self.build({}, '--mode', 'community', '--hero', 'beast')
        kinds = [e['event'] for e in ev]
        self.assertEqual(kinds[0], 'plan')
        self.assertEqual(kinds[-1], 'result')
        self.assertIn('progress', kinds)
        self.assertTrue(all('t' in e for e in ev))


if __name__ == '__main__':
    unittest.main()


class PerHeroLayout(Fixture):
    def build(self, verdicts, *extra):
        fake = fake_adapter(Path(self.tmp.name) / 'cand', verdicts)
        with mock.patch.dict(tiers.ADAPTERS, {t: fake for t in roster.TIERS}):
            return run_cli('build', *self.base, '--layout', 'per-hero', *extra)

    def test_reference_layout_one_folder_per_hero(self):
        code, ev = self.build({}, '--mode', 'upgraded', '--hero', 'wolverine', '--hero', 'storm', '--hero', 'beast')
        self.assertEqual(code, 0)
        mods = self.port / 'mods'
        self.assertEqual(ev[-1]['folders'], ['Upgraded-Community-Beast', 'Upgraded-MUA-Storm', 'Upgraded-MUA2-Wolverine'])
        for folder, skin in [('Upgraded-MUA2-Wolverine', '14301'), ('Upgraded-MUA-Storm', '14401'), ('Upgraded-Community-Beast', '14501')]:
            self.assertTrue((mods / folder / 'Actors' / f'{skin}.IGB').is_file())
            mod = json.loads((mods / folder / 'mod.json').read_text())
            self.assertEqual((mod['name'], mod['skin'] != '', mod['port_skin']), (folder, True, skin))
            self.assertTrue((mods / folder / 'readme.txt').is_file() and (mods / folder / 'CREDITS.md').is_file())
        self.assertIn('BaconWizard17', (mods / 'Upgraded-Community-Beast' / 'CREDITS.md').read_text())
        self.assertNotIn('BaconWizard17', (mods / 'Upgraded-MUA-Storm' / 'CREDITS.md').read_text())
        self.assertEqual(sorted((mods / 'load-order.txt').read_text().split()), ['+Upgraded-Community-Beast', '+Upgraded-MUA-Storm', '+Upgraded-MUA2-Wolverine'])
        code, ev = run_cli('verify', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual((code, ev[-1]['ok'], len(ev[-1]['folders'])), (0, True, 3))

    def test_tier_change_replaces_the_hero_folder_and_remove_takes_the_group(self):
        self.build({}, '--mode', 'upgraded', '--hero', 'wolverine', '--hero', 'storm')
        self.build({('wolverine', 'mua2'): 'fail'}, '--mode', 'upgraded', '--hero', 'wolverine', '--rebuild')
        mods = self.port / 'mods'
        self.assertFalse((mods / 'Upgraded-MUA2-Wolverine').exists())
        self.assertTrue((mods / 'Upgraded-MUA-Wolverine' / 'Actors' / '14301.IGB').is_file())
        self.assertTrue((mods / 'Upgraded-MUA-Storm').is_dir())                      # --hero keeps the rest of the group
        code, ev = run_cli('remove', '--pack', 'Models-Upgraded', *self.base)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(ev[-1]['folders']), ['Upgraded-MUA-Storm', 'Upgraded-MUA-Wolverine'])
        self.assertEqual([p.name for p in mods.iterdir() if p.is_dir()], [])
        self.assertEqual((mods / 'load-order.txt').read_text(), '')

    def test_switching_group_disables_the_other_groups_folders(self):
        self.build({}, '--mode', 'upgraded', '--hero', 'storm')
        self.build({}, '--mode', 'sharpen', '--hero', 'storm')
        text = (self.port / 'mods' / 'load-order.txt').read_text()
        self.assertIn('-Upgraded-MUA-Storm', text)
        self.assertIn('+Sharpened-Storm', text)
