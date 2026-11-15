# SPDX-License-Identifier: MIT
"""Provisioning and public-package privacy; synthetic bundles only."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import freeze
import private_bundle_guard as guard
import provision_accepted as provision
import verify_dist
from test_accepted import AcceptedFixture
from test_release import fake_dist


class Provision(AcceptedFixture):
    # Reuse the existing realistic input/report fixture and its build checks.
    def setUp(self):
        super().setUp()
        self.receipt = {k: self.record[k] for k in ('hero', 'tier', 'source', 'output_sha256')}
        self.receipt['files'] = {n: hashlib.sha256((self.bundle / n).read_bytes()).hexdigest() for n in provision.FILES}
        self.records = {self.record['output_sha256']: self.record}
        self.destination = self.work / 'shared'
        self.target = self.destination / 'accepted' / self.record['output_sha256']

    def stage(self, **kwargs):
        return provision.provision(self.bundle, self.destination, self.receipt, records=self.records, **kwargs)

    def test_stage_then_build_from_shared_root_without_engine(self):
        self.assertEqual(self.stage()['status'], 'staged')
        for name in provision.FILES:
            self.assertEqual((self.bundle / name).read_bytes(), (self.target / name).read_bytes())
        self.bundle_root = self.destination / 'accepted'
        for rebuild in (False, True):
            code, result, events = self.run_build(rebuild)
            self.assertEqual(code, 0)
            self.assertTrue(result['characters']['wolverine']['owner_accepted'])

    def test_idempotent_and_check_only(self):
        self.assertEqual(self.stage(check_only=True)['status'], 'verified_not_staged')
        self.assertFalse(self.destination.exists())
        self.stage()
        before = {p.name: p.stat().st_mtime_ns for p in self.target.iterdir()}
        self.assertEqual(self.stage()['status'], 'already_present')
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in self.target.iterdir()})

    def test_each_file_tamper_refuses_before_destination_write(self):
        for name in provision.FILES:
            with self.subTest(name=name):
                p = self.bundle / name
                data = p.read_bytes()
                p.write_bytes(data + b' ')
                with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                    self.stage()
                self.assertFalse(self.destination.exists())
                p.write_bytes(data)

    def test_corrupt_existing_target_is_preserved_and_refused(self):
        self.stage()
        p = self.target / 'actor.IGB'
        p.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            self.stage()
        self.assertEqual(p.read_bytes(), b'corrupt')

    def test_copy_failure_never_publishes_partial_bundle(self):
        original = provision.shutil.copyfile
        count = []
        def fail_second(src, dst):
            count.append(src)
            if len(count) == 2:
                raise OSError('synthetic disk failure')
            return original(src, dst)
        with mock.patch.object(provision.shutil, 'copyfile', fail_second):
            with self.assertRaises(OSError):
                self.stage()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_modified_copy_is_reverified_before_publication(self):
        original = provision.shutil.copyfile
        def corrupt_copy(src, dst):
            original(src, dst)
            if Path(src).name == 'manifest.json':
                Path(dst).write_text('{}')
        with mock.patch.object(provision.shutil, 'copyfile', corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                self.stage()
        self.assertFalse(self.target.exists())

    def test_unknown_catalogue_output_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'absent'):
            provision.provision(self.bundle, self.destination, self.receipt, records={})
        self.assertFalse(self.destination.exists())

    def test_missing_input_bindings_and_report_hash_refuse_even_with_new_receipt(self):
        self.manifest['inputs'].pop()
        self.save_manifest()
        self.receipt['files']['manifest.json'] = hashlib.sha256((self.bundle / 'manifest.json').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'omits'):
            self.stage()
        self.setUp_manifest_for_report_test()
        raw = json.loads((self.bundle / 'report.json').read_text())
        raw['converted_sha256'] = '0' * 64
        (self.bundle / 'report.json').write_text(json.dumps(raw))
        self.receipt['files']['report.json'] = hashlib.sha256((self.bundle / 'report.json').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'actor/report hash'):
            self.stage()

    def setUp_manifest_for_report_test(self):
        # Restore required inputs from the otherwise unchanged accepted fixture.
        self.manifest['inputs'].append(dict(root='mua2', path='textures.bin', sha256=hashlib.sha256(b'').hexdigest()))
        self.save_manifest()
        self.receipt['files']['manifest.json'] = hashlib.sha256((self.bundle / 'manifest.json').read_bytes()).hexdigest()

    def test_extra_file_or_directory_refused(self):
        (self.bundle / 'unexpected').mkdir()
        with self.assertRaisesRegex(ValueError, 'exactly'):
            self.stage()

    def test_destination_inside_source_bundle_refused(self):
        with self.assertRaisesRegex(ValueError, 'inside the source bundle'):
            provision.provision(self.bundle, self.bundle / 'shared', self.receipt, records=self.records)
        self.assertFalse((self.bundle / 'shared').exists())

    def test_later_numeric_failure_is_visible_but_does_not_revoke_exact_approval(self):
        raw = json.loads((self.bundle / 'report.json').read_text())
        raw.pop('layer_check')
        (self.bundle / 'report.json').write_text(json.dumps(raw))
        self.receipt['files']['report.json'] = hashlib.sha256((self.bundle / 'report.json').read_bytes()).hexdigest()
        result = self.stage()
        self.assertFalse(result['numeric_pass'])
        self.assertTrue(result['owner_accepted'])
        self.assertTrue(result['failures'])

    def test_shared_accepted_root_symlink_or_junction_refused(self):
        self.destination.mkdir()
        with mock.patch.object(provision.Path, 'is_junction', lambda p: p == self.destination / 'accepted'):
            with self.assertRaisesRegex(ValueError, 'link/junction'):
                self.stage()

    def test_cli_json_refusal_and_no_write(self):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as stream:
            code = provision.main(['--bundle', str(self.bundle), '--work', str(self.destination),
                                   '--output-sha256', '0' * 64])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'refused')
        self.assertFalse(self.destination.exists())

    def pending(self):
        self.record['status'] = self.receipt['status'] = 'pending'

    def test_pending_requires_explicit_opt_in_even_for_check_only(self):
        self.pending()
        for check_only in (False, True):
            with self.assertRaisesRegex(ValueError, '--allow-pending'):
                self.stage(check_only=check_only)
        self.assertFalse(self.destination.exists())
        result = self.stage(allow_pending=True)
        self.assertEqual(result['status'], 'staged')
        self.assertFalse(result['owner_accepted'])
        self.assertTrue(result['numeric_pass'])
        self.assertEqual(self.stage(allow_pending=True)['status'], 'already_present')
        with self.assertRaisesRegex(ValueError, '--allow-pending'):
            self.stage()

    def test_pending_cannot_claim_accepted_receipt_status(self):
        self.pending()
        self.receipt['status'] = 'accepted'
        with self.assertRaisesRegex(ValueError, 'review status mismatch'):
            self.stage(allow_pending=True)
        self.assertFalse(self.destination.exists())

    def test_pending_numeric_failure_refuses_even_with_opt_in(self):
        self.pending()
        raw = json.loads((self.bundle / 'report.json').read_text())
        raw.pop('layer_check')
        (self.bundle / 'report.json').write_text(json.dumps(raw))
        self.receipt['files']['report.json'] = hashlib.sha256((self.bundle / 'report.json').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'numeric PASS'):
            self.stage(allow_pending=True)
        self.assertFalse(self.destination.exists())

    def test_incomplete_original_provenance_cannot_be_overridden(self):
        self.receipt['input_provenance'] = 'incomplete'
        for pending in (False, True):
            if pending:
                self.pending()
            with self.assertRaisesRegex(ValueError, 'original input provenance incomplete'):
                self.stage(allow_pending=True)
        self.assertFalse(self.destination.exists())

    def test_pending_cli_flag_does_not_grant_acceptance(self):
        self.pending()
        path = self.work / 'receipt.json'
        path.write_text(json.dumps(dict(schema=1, bundles=[self.receipt])))
        args = ['--bundle', str(self.bundle), '--work', str(self.destination),
                '--output-sha256', self.record['output_sha256'], '--receipt', str(path)]
        with mock.patch.object(provision, 'provisioning_records', return_value=self.records):
            for extra, expected in (([], 1), (['--allow-pending'], 0)):
                with mock.patch('sys.stdout', new_callable=io.StringIO) as stream:
                    self.assertEqual(provision.main(args + extra), expected)
                result = json.loads(stream.getvalue())
                if expected == 0:
                    self.assertFalse(result['owner_accepted'])
                else:
                    self.assertIn('--allow-pending', result['error'])


class Receipt(unittest.TestCase):
    def test_pinned_receipt_matches_actor_and_rejects_duplicates(self):
        rows = provision.read_receipt(provision.DEFAULT_RECEIPT)
        self.assertEqual(len(rows), 4)
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'receipt.json'
            path.write_text(json.dumps(dict(schema=1, bundles=rows + rows)))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                provision.read_receipt(path)

    def test_owner_approved_jean_outputs_are_available_for_exact_reuse(self):
        records = provision.acceptance_records()
        for digest in ('d2628a6ef1d8d26abf2b4671e0e233d5065082a8d8d1d053392a7b913ad69bd3',
                       'a2c7e18985f1eb6fd9774b662c091aeb1bdff1e5fb7f9a61c0c34fa15d0ca905'):
            self.assertEqual(records[digest]['hero'], 'phoenix')
            self.assertEqual(records[digest]['by'], 'owner (2026-10-27)')
            self.assertIn(digest, guard.private_hashes())


class PrivatePackaging(unittest.TestCase):
    def check(self, members, hashes=None):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'public.zip'
            with zipfile.ZipFile(path, 'w') as z:
                for name, data in members.items():
                    z.writestr(name, data)
            return guard.check_zip(path, hashes=hashes)

    def test_bundle_anywhere_in_tool_or_launcher_zip_refuses(self):
        for prefix in ('', '_internal/', 'resources/', 'launcher/cache/'):
            with self.subTest(prefix=prefix):
                self.assertTrue(self.check({prefix + 'accepted/hash/actor.IGB': b'private fixture'}))

    def test_renamed_actor_by_digest_and_signature_refuses(self):
        data = b'private accepted fixture'
        self.assertTrue(self.check({'resources/cache.dat': data}, {hashlib.sha256(data).hexdigest()}))
        data = b'\0' * 0x28 + b'\xda\xfa\0\0' + b'\0' * 4
        self.assertTrue(self.check({'resources/unknown.dat': data}, set()))

    def test_renamed_manifest_and_path_case_or_backslash_refuse(self):
        data = json.dumps(dict(schema=1, hero='wolverine', tier='mua2', source='0301', output_sha256='a'*64, inputs=[]))
        self.assertTrue(self.check({'innocent.dat': data}))
        self.assertTrue(self.check({'CACHE\\AcCePtEd\\hash\\report.json': '{}'}))

    def test_public_code_and_hash_metadata_are_allowed(self):
        self.assertEqual(self.check({'tools/accepted-bundles.json': provision.DEFAULT_RECEIPT.read_bytes(),
                                     'data/acceptance.json': b'{"records": []}', 'ui/icon.png': b'PNG fixture'}), [])

    def test_verify_dist_rejects_bundle_outside_package_data(self):
        with tempfile.TemporaryDirectory() as t:
            dist = fake_dist(Path(t), extra={'_internal/cache/accepted/hash/actor.IGB': b'private'})
            errors = verify_dist.verify(dist, None, None)
            self.assertTrue(any('private bundle' in e for e in errors), errors)

    def test_freeze_never_replaces_public_archive_with_private_zip(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t) / 'folder'
            (folder / 'cache/accepted/hash').mkdir(parents=True)
            (folder / 'cache/accepted/hash/actor.IGB').write_bytes(b'private')
            archive = Path(t) / 'release.zip'
            archive.write_bytes(b'previous public artifact')
            with self.assertRaisesRegex(SystemExit, 'private accepted'):
                freeze.make_zip(folder, archive)
            self.assertEqual(archive.read_bytes(), b'previous public artifact')
            self.assertFalse(archive.with_suffix('.zip.partial').exists())
            with self.assertRaisesRegex(SystemExit, 'private accepted'):
                freeze.guard_bundle(folder, False)

    def test_corrupt_zip_refuses(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'broken.zip'
            path.write_bytes(b'not a zip')
            self.assertTrue(guard.check_zip(path))
