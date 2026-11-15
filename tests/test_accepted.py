# SPDX-License-Identifier: MIT
"""Accepted bundles must bypass the optimizer and remain bound to owned input bytes."""
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from legends_models import build, gate
from legends_models.engines import tiers
from legends_models.plan import Choice, Row
from legends_models.progress import Reporter
from legends_models.roster import BY_KEY
from test_cli import Fixture
from test_gate import mua1_report
from synth import mua_bin


class AcceptedFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.hero = BY_KEY['wolverine']
        self.choice = Choice('mua2', '0301', 'accepted', True, 'fixture')
        self.port.joinpath('Actors/x1_03_wolverine.IGB').write_bytes(b'animation')
        self.port.joinpath('Actors/common.IGB').write_bytes(b'common')
        self.record = dict(hero='wolverine', tier='mua2', source='0301',
                           source_sha256=hashlib.sha256(b'x').hexdigest(),
                           template_sha256=hashlib.sha256(b'stock').hexdigest(),
                           output_sha256=hashlib.sha256(b'accepted actor').hexdigest(), by='owner')
        self.bundle_root = self.work / 'accepted'
        self.bundle = self.bundle_root / self.record['output_sha256']
        self.bundle.mkdir(parents=True)
        (self.bundle / 'actor.IGB').write_bytes(b'accepted actor')
        rep = mua1_report()
        rep['converted_sha256'] = self.record['output_sha256']
        rep['layer_check'] = {'pairs': 0, 'bind_crossings': 0}
        for row in rep['animations'].values(): row['models']['converted']['layer_crossings'] = 0
        (self.bundle / 'report.json').write_text(json.dumps(rep))
        refs = [('source', None, b'x'), ('port', 'Actors/14301.IGB', b'stock'),
                ('port', 'libIGSg.dll', b''), ('port', 'Actors/x1_03_wolverine.IGB', b'animation'),
                ('port', 'Actors/common.IGB', b'common'), ('mua2', 'materials.bin', b''),
                ('mua2', 'textures.bin', b'')]
        self.manifest = dict(schema=1, hero='wolverine', tier='mua2', source='0301',
                             output_sha256=self.record['output_sha256'],
                             inputs=[dict(root=r, path=p, sha256=hashlib.sha256(b).hexdigest()) for r,p,b in refs])
        self.save_manifest()

    def save_manifest(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def run_build(self, rebuild=False, adapter=None, materials=()):
        stream = io.StringIO()
        ctx = tiers.Context(self.port, self.work / 'ports/test', Reporter(True, stream),
                            self.mua1, self.mua2, materials=materials, heads=False)
        ctx.accepted_cache = self.bundle_root
        adapter = adapter or (lambda *a: self.fail('accepted inputs must never re-run optimizer'))
        with mock.patch('legends_models.build.acceptance_records', return_value={self.record['output_sha256']: self.record}), \
             mock.patch.dict(tiers.ADAPTERS, {'mua2': adapter}):
            code, result = build.run_build({}, [Row('wolverine', 'Wolverine', '14301', self.choice)], ctx,
                                           self.port, 'Accepted-test', 'upgraded', ctx.report, rebuild=rebuild)
        return code, result, [json.loads(s) for s in stream.getvalue().splitlines()]


class AcceptedBuild(AcceptedFixture):
    def test_accepted_bundle_bypasses_refit_even_on_rebuild(self):
        for rebuild in (False, True):
            code, result, events = self.run_build(rebuild)
            self.assertEqual(code, 0)
            self.assertEqual((self.port / 'mods/Accepted-test/Actors/14301.IGB').read_bytes(), b'accepted actor')
            self.assertTrue(result['characters']['wolverine']['owner_accepted'])
            self.assertTrue(any(e['event'] == 'cache' and e.get('kind') == 'accepted' and e['hit'] for e in events))

    def test_same_size_mtime_animation_change_requires_refit_event(self):
        p = self.port / 'Actors/x1_03_wolverine.IGB'
        stat = p.stat()
        p.write_bytes(b'animatioN')
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        def refuse(*args):
            from legends_models.errors import ToolError
            raise ToolError('synthetic refit reached')
        code, result, events = self.run_build(adapter=refuse)
        self.assertFalse(result['written'])
        self.assertTrue(any(e['event'] == 'cache' and e.get('reason') == 'inputs_changed' and
                            'Actors/x1_03_wolverine.IGB' in str(e.get('changed_inputs')) for e in events))
        self.assertIn('synthetic refit reached', str(result))

    def test_cancel_before_publication_keeps_previous_pack(self):
        self.run_build()
        target = self.port/'mods/Accepted-test/Actors/14301.IGB'
        before = target.read_bytes()
        flag = self.work/'cancel-request'
        from legends_models import accepted
        original = accepted.lookup
        def request_after_lookup(*args):
            candidate = original(*args)
            flag.write_text('cancel')
            return candidate
        with mock.patch.dict(os.environ, {'LEGENDS_MODELS_CANCEL': str(flag)}), mock.patch.object(accepted, 'lookup', request_after_lookup):
            with self.assertRaises(KeyboardInterrupt): self.run_build(rebuild=True)
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(build.packs.verify(target.parent.parent)['ok'])

    def test_cancel_between_publication_renames_completes_consistent_pack(self):
        self.run_build()
        flag = self.work/'cancel-request'
        original = os.replace
        requested = []
        def replace(src, dst):
            if Path(dst).name.startswith('.legends-models-old-'):
                flag.write_text('cancel')
                requested.append(True)
            return original(src, dst)
        with mock.patch.dict(os.environ, {'LEGENDS_MODELS_CANCEL': str(flag)}), mock.patch.object(os, 'replace', replace):
            code, result, _ = self.run_build(rebuild=True)
        self.assertEqual(requested, [True])
        self.assertEqual(code, 0)
        self.assertTrue(result['written'])
        self.assertTrue(build.packs.verify(self.port/'mods/Accepted-test')['ok'])
        from legends_models import loadorder
        self.assertTrue(loadorder.is_enabled(loadorder.read(self.port/'mods'), 'Accepted-test'))

    def test_missing_accepted_asset_refuses_without_refitting_same_inputs(self):
        os.remove((self.bundle / 'actor.IGB').resolve())
        code, result, events = self.run_build()
        self.assertFalse(result['written'])
        self.assertTrue(any(e['event'] == 'cache' and e.get('reason') == 'accepted_unavailable' for e in events))

    def test_owner_accepted_exact_hash_survives_later_gate_failure(self):
        rep = json.loads((self.bundle / 'report.json').read_text())
        rep['animations']['run']['models']['converted']['max_chain_distance'] = 11.
        (self.bundle / 'report.json').write_text(json.dumps(rep))
        code, result, events = self.run_build()
        self.assertTrue(result['written'])
        self.assertTrue(any(e.get('owner_accepted') and e.get('acceptance_reason') for e in events))
        self.assertFalse(gate.check_report(self.bundle/'report.json', self.bundle/'actor.IGB')['numeric_pass'])

    def test_later_regional_arm_refusal_preserves_exact_owner_decision_and_reason(self):
        failure = 'run: left upper-arm regional width exceeds stock'
        later_gate = dict(numeric_pass=False, failures=[failure], candidate_sha256=self.record['output_sha256'], kind='mua2')
        with mock.patch.object(gate, 'check_report', return_value=later_gate):
            code, result, events = self.run_build(rebuild=True)
        self.assertEqual(code, 0)
        event = next(e for e in events if e['event'] == 'gate')
        self.assertTrue(event['passed'])
        self.assertTrue(event['owner_accepted'])
        self.assertFalse(event['numeric_pass'])
        self.assertEqual(event['reason'], 'owner_accepted_exact_output_hash')
        self.assertEqual(event['failures'], [failure])

    def test_owner_approval_cannot_follow_material_changes_to_another_hash(self):
        self.choice = Choice('mua1', '0301', 'accepted', True, 'fixture')
        self.record.update(tier='mua1', source_sha256=hashlib.sha256(b'mua1').hexdigest())
        self.manifest['tier'] = 'mua1'
        self.manifest['inputs'] = [r for r in self.manifest['inputs'] if r['root'] != 'mua2']
        next(r for r in self.manifest['inputs'] if r['root'] == 'source')['sha256'] = self.record['source_sha256']
        self.save_manifest()
        rep = json.loads((self.bundle/'report.json').read_text())
        rep['animations']['run']['models']['converted']['max_chain_distance'] = 11.
        (self.bundle/'report.json').write_text(json.dumps(rep))
        def changed_material(ctx, hero, choice, cand, result, report):
            path = self.work/'new-materials.IGB'
            path.write_bytes(b'new unaccepted bytes')
            cand.path = path
            return cand, dict(result, candidate_sha256=gate.sha256(path)), {}
        with mock.patch.object(build, '_apply_materials', changed_material):
            code, result, events = self.run_build(materials=('spec',))
        self.assertFalse(result['written'])
        self.assertTrue(any(e['event'] == 'gate' and not e['passed'] for e in events))

    def test_material_processing_writes_work_folder_not_accepted_bundle(self):
        ctx = tiers.Context(self.port, self.work/'private-work', Reporter(True, io.StringIO()), self.mua1, self.mua2, materials=('bake',))
        choice = Choice('mua1', '0301', 'accepted', True, 'fixture')
        candidate = tiers.Candidate(self.bundle/'actor.IGB', self.bundle/'report.json', 'deform')
        result = dict(candidate_sha256=self.record['output_sha256'], numeric_pass=True, failures=[], owner_accepted=True)
        with mock.patch('legends_models.materials.apply', return_value=(b'material variant', {'invariant': {'pass': True}, 'steps': ['bake']})), mock.patch('legends_models.formats.igb.IgbFile', return_value=object()):
            candidate, _, _ = build._apply_materials(ctx, self.hero, choice, candidate, result, ctx.report)
        self.assertTrue(candidate.path.is_relative_to(ctx.work))
        self.assertEqual(candidate.path.read_bytes(), b'material variant')
        self.assertEqual((self.bundle/'actor.IGB').read_bytes(), b'accepted actor')
        self.assertEqual(sorted(p.name for p in self.bundle.iterdir()), ['actor.IGB', 'manifest.json', 'report.json'])

    def test_report_hash_mismatch_never_waived(self):
        rep = json.loads((self.bundle/'report.json').read_text())
        rep['converted_sha256'] = '0'*64
        (self.bundle/'report.json').write_text(json.dumps(rep))
        code, result, events = self.run_build()
        self.assertFalse(result['written'])

    def test_fresh_fit_still_refused_on_current_gate_failure(self):
        (self.port/'Actors/x1_03_wolverine.IGB').write_bytes(b'changed')
        def fresh(hero, choice, ctx):
            actor, report = self.work/'bad.IGB', self.work/'bad.json'
            actor.write_bytes(b'fresh bad actor')
            data = mua1_report()
            data['converted_sha256'] = gate.sha256(actor)
            data['animations']['run']['models']['converted']['max_chain_distance'] = 11.
            report.write_text(json.dumps(data))
            return tiers.Candidate(actor, report, 'deform')
        code, result, events = self.run_build(adapter=fresh)
        self.assertFalse(result['written'])
        self.assertTrue(any(e['event'] == 'gate' and not e['passed'] for e in events))

    def test_manifest_cannot_omit_animation_binding(self):
        self.manifest['inputs'] = self.manifest['inputs'][:3]
        self.save_manifest()
        code, result, events = self.run_build()
        self.assertFalse(result['written'])

    def test_manifest_cannot_omit_report_attachment_input(self):
        rep = json.loads((self.bundle/'report.json').read_text())
        rep['attachments'] = {'files': {'claw_left.IGB': '0'*64}}
        (self.bundle/'report.json').write_text(json.dumps(rep))
        code, result, events = self.run_build()
        self.assertFalse(result['written'])

    def test_changed_source_actor_requires_refit_even_with_same_archive_stat(self):
        p = self.mua2 / 'actors.bin'
        st = p.stat()
        p.write_bytes(mua_bin([('0301.igz', b'X'), ('0601.igz', b'y')]))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        def refuse(*args):
            from legends_models.errors import ToolError
            raise ToolError('changed source refit reached')
        code, result, events = self.run_build(adapter=refuse)
        self.assertIn('changed source refit reached', str(result))
        self.assertTrue(any(e.get('reason') == 'inputs_changed' and e.get('refit') for e in events))

    def test_changed_texture_archive_requires_refit(self):
        (self.mua2 / 'textures.bin').write_bytes(b'changed material inputs')
        def refuse(*args):
            from legends_models.errors import ToolError
            raise ToolError('changed texture refit reached')
        code, result, events = self.run_build(adapter=refuse)
        self.assertIn('changed texture refit reached', str(result))
        self.assertTrue(any('mua2:textures.bin' in e.get('changed_inputs', []) for e in events))

    def test_corrupt_actor_never_published_or_refitted(self):
        (self.bundle / 'actor.IGB').write_bytes(b'corrupt')
        code, result, events = self.run_build()
        self.assertFalse(result['written'])
        self.assertTrue(any(e.get('reason') == 'accepted_unavailable' for e in events))

    def test_changed_inputs_fit_once_then_reuse_content_bound_numeric_result(self):
        (self.port / 'Actors/x1_03_wolverine.IGB').write_bytes(b'animatioN')
        def fresh(hero, choice, ctx):
            actor, report = self.work/'new.IGB', self.work/'new.json'
            actor.write_bytes(b'new numeric actor')
            data = mua1_report()
            data['converted_sha256'] = gate.sha256(actor)
            data['layer_check'] = {'pairs': 0, 'bind_crossings': 0}
            for row in data['animations'].values(): row['models']['converted']['layer_crossings'] = 0
            report.write_text(json.dumps(data))
            return tiers.Candidate(actor, report, 'deform')
        code, result, events = self.run_build(adapter=fresh)
        self.assertEqual(code, 0)
        self.assertFalse(result['characters']['wolverine']['owner_accepted'])
        code, result, events = self.run_build()
        self.assertEqual(code, 0)
        self.assertFalse(result['characters']['wolverine']['owner_accepted'])
        self.assertTrue(any(e.get('event') == 'cache' and e.get('hit') and e.get('kind') != 'accepted' for e in events))

    def test_seed_round_trip_checks_catalogue_hash_and_full_inputs(self):
        from legends_models import accepted
        ctx = tiers.Context(self.port, self.work / 'unused', Reporter(True, io.StringIO()), self.mua1, self.mua2)
        target = self.work / 'seeded'
        folder = accepted.seed(self.hero, self.choice, ctx, self.record, self.bundle / 'actor.IGB',
                               self.bundle / 'report.json', {'clips': [['idle', 'x1_03_wolverine.IGB']]}, target)
        ctx.accepted_cache = target
        candidate, force = accepted.lookup(self.hero, self.choice, ctx, {self.record['output_sha256']: self.record})
        self.assertFalse(force)
        self.assertEqual(candidate.path.read_bytes(), b'accepted actor')
        # Compare resolved paths: GitHub runners' TEMP can be an 8.3 short name (RUNNER~1).
        self.assertEqual(folder.resolve(), candidate.path.parent.resolve())
        bad = dict(self.record, output_sha256='0'*64)
        with self.assertRaises(ValueError):
            accepted.seed(self.hero, self.choice, ctx, bad, self.bundle / 'actor.IGB',
                          self.bundle / 'report.json', {}, target)


class BundleAvailability(AcceptedFixture):
    def available(self):
        from legends_models import accepted
        return accepted.bundle_available(self.hero, self.choice, self.bundle_root,
                                         {self.record['output_sha256']: self.record})

    def test_complete_bundle_is_verified_without_reading_owned_installs(self):
        from legends_models import accepted
        with mock.patch.object(accepted, 'input_hash', side_effect=AssertionError('scan must not rehash large owned archives')):
            self.assertTrue(self.available())

    def test_missing_corrupt_or_incomplete_bundle_is_not_available(self):
        for name in ('actor.IGB', 'manifest.json', 'report.json'):
            p = self.bundle / name
            data = p.read_bytes()
            p.write_bytes(b'corrupt')
            self.assertFalse(self.available(), name)
            p.write_bytes(data)
        self.manifest['inputs'].pop()
        self.save_manifest()
        self.assertFalse(self.available())

    def test_report_must_bind_the_approved_actor(self):
        raw = json.loads((self.bundle / 'report.json').read_text())
        raw['converted_sha256'] = '0' * 64
        (self.bundle / 'report.json').write_text(json.dumps(raw))
        self.assertFalse(self.available())

    def test_unknown_input_root_is_not_available(self):
        self.manifest['inputs'].append(dict(root='other', path='file', sha256='a' * 64))
        self.save_manifest()
        self.assertFalse(self.available())

    def test_pending_records_and_extra_files_are_not_available(self):
        self.record['status'] = 'pending'
        self.assertFalse(self.available())
        self.record['status'] = 'accepted'
        (self.bundle / 'unexpected').write_text('extra')
        self.assertFalse(self.available())


class ApprovedPlanning(AcceptedFixture):
    def cli(self, command, *extra):
        from test_cli import run_cli
        with mock.patch('legends_models.build.acceptance_records', return_value={self.record['output_sha256']: self.record}):
            mode = [] if command == 'scan' else ['--mode', 'upgraded']
            return run_cli(command, *self.base, *mode, '--hero', 'wolverine', *extra)

    def test_matching_bundle_and_inputs_keep_exact_approved_bytes_even_on_rebuild(self):
        from test_cli import run_cli
        for extra in ((), ('--rebuild',)):
            original_hash = gate.sha256
            def small_files_only(path):
                self.assertNotIn(Path(path).name, ('materials.bin', 'textures.bin'))
                return original_hash(path)
            with mock.patch.object(gate, 'sha256', side_effect=small_files_only):
                code, events = self.cli('plan')
            self.assertEqual(events[-1]['rows'][0]['tier'], 'mua2')
            with mock.patch.dict(tiers.ADAPTERS, {'mua2': lambda *a: self.fail('approved inputs must never refit')}):
                code, events = self.cli('build', '--no-heads', *extra)
            self.assertEqual(code, 0)
            self.assertEqual(events[-1]['characters']['wolverine']['sha256'], self.record['output_sha256'])
            self.assertTrue(events[-1]['characters']['wolverine']['owner_accepted'])

    def test_original_source_without_bundle_plans_the_actual_lower_tier(self):
        import shutil
        shutil.rmtree(str(self.bundle.absolute()))
        code, events = self.cli('plan')
        self.assertEqual((events[-1]['rows'][0]['tier'], events[-1]['rows'][0]['source']), ('mua1', '0303'))

    def test_scan_does_not_offer_original_approved_costume_when_bundle_is_missing(self):
        import shutil
        shutil.rmtree(str(self.bundle.absolute()))
        code, events = self.cli('scan')
        tier = events[-1]['heroes']['wolverine']['tiers']['mua2']
        self.assertFalse(tier['available'])
        self.assertEqual(tier['costumes'], [])

    def test_changed_source_without_bundle_keeps_the_fresh_fit_path(self):
        import shutil
        shutil.rmtree(str(self.bundle.absolute()))
        (self.mua2 / 'actors.bin').write_bytes(mua_bin([('0301.igz', b'changed source')]))
        code, events = self.cli('plan')
        self.assertEqual(events[-1]['rows'][0]['tier'], 'mua2')

    def test_unverifiable_original_input_is_not_promised(self):
        from legends_models import accepted
        with mock.patch.object(accepted, 'input_hash', side_effect=ValueError('source unreadable')):
            code, events = self.cli('plan')
        self.assertEqual(events[-1]['rows'][0]['tier'], 'mua1')


if __name__ == '__main__':
    unittest.main()
