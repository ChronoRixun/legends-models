# SPDX-License-Identifier: MIT
"""Release policy (tags, versions, notes), the release-file checker and repository hygiene (no game data needed)."""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
import release_metadata as R  # noqa: E402
import verify_dist  # noqa: E402

import legends_models  # noqa: E402


class Tags(unittest.TestCase):
    def test_tags_and_flags(self):
        for tag, base, pre in [('v0.3.0', '0.3.0', False), ('v0.3.0-beta.1', '0.3.0', True), ('v1.2.10-rc.3', '1.2.10', True)]:
            with self.subTest(tag=tag):
                meta = R.metadata(tag)
                self.assertEqual((meta['version'], meta['base'], meta['prerelease']), (tag[1:], base, pre))
                self.assertEqual(meta['flags'], ['--prerelease', '--latest=false'] if pre else [])
                self.assertEqual(R.release_version(tag, base), tag[1:])
        self.assertEqual(R.release_version(None, '0.3.0'), '0.3.0')
        self.assertEqual(R.release_version('', '0.3.0'), '0.3.0')
        for tag in ['malformed', '0.3.0', 'v00.3.0', 'v0.3', 'v0.3.0-beta', 'v0.3.0-beta.02', 'v0.3.0-alpha.1',
                    'v0.3.0+build', 'v0.3.0-rc.1\n', 'v0.3.0-beta.1/x']:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                R.metadata(tag)
        with self.assertRaises(ValueError):
            R.release_version('v0.3.1-beta.1', '0.3.0')

    def test_the_source_version_is_numeric_and_matches_pyproject(self):
        self.assertRegex(legends_models.__version__, r'^\d+\.\d+\.\d+$')
        toml = (REPO / 'pyproject.toml').read_text(encoding='utf-8')
        self.assertIn(f'version = "{legends_models.__version__}"', toml)

    def test_notes(self):
        changelog = '# Changelog\n\n## v0.4.0\n\nLater.\n\n## v0.3.0 - 2026-10-24\n\nNew.\n### Fixed\nDetail.\n\n## v0.2.0\nOld.\n'
        manifest = {'api': 1, 'content_version': 1, 'min_launcher': '0.5.0-0'}
        beta = R.release_notes('v0.3.0-beta.1', manifest, changelog, 'abc  legends-models-0.3.0-beta.1-win64.zip\n')
        self.assertTrue(beta.startswith('Beta - test build\n\n'))
        self.assertIn('legends-models v0.3.0-beta.1', beta)
        self.assertIn('New.\n### Fixed\nDetail.', beta)
        self.assertNotIn('Later.', beta)
        self.assertNotIn('Old.', beta)
        self.assertIn('abc  legends-models-0.3.0-beta.1-win64.zip', beta)
        self.assertIn('no game data', beta)
        self.assertIn('Ultimate Legends 0.5.0 (or a beta of it) or newer', beta)
        self.assertFalse(R.release_notes('v0.3.0', manifest, changelog, '').startswith('Beta'))
        R.release_notes('v9.0.0', manifest, changelog, '')
        with self.assertRaises(ValueError):
            R.release_notes('v9.0.0-beta.1', manifest, changelog, '')

    def test_the_changelog_has_the_current_version(self):
        text = (REPO / 'CHANGELOG.md').read_text(encoding='utf-8')
        self.assertTrue(re.search(rf'^## v{re.escape(legends_models.__version__)}( |$)', text, re.M))

    def test_cli_and_freeze_tag_check(self):
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'release_metadata.py'), '--tag', 'v0.3.0-beta.1'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['flags'], ['--prerelease', '--latest=false'])
        p = subprocess.run([sys.executable, str(REPO / 'tools' / 'release_metadata.py'), '--tag', 'nope'],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        v = legends_models.__version__      # freeze.py --check-tag needs neither PyInstaller nor NumPy
        for tag, code in [(f'v{v}', 0), (f'v{v}-beta.1', 0), (f'v{v}-rc.2', 0), ('v99.0.0-beta.1', 1), (f'v{v}-beta', 1)]:
            with self.subTest(tag=tag):
                p = subprocess.run([sys.executable, str(REPO / 'tools' / 'freeze.py'), '--check-tag', tag],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, code, p.stdout + p.stderr)


class FrozenVersion(unittest.TestCase):
    def test_build_info_version_only_when_frozen_and_same_base(self):
        with tempfile.TemporaryDirectory() as t:
            pkg = Path(t) / 'legends_models'
            (pkg / 'data').mkdir(parents=True)
            fake_init = str(pkg / '__init__.py')
            info = pkg / 'data' / 'build-info.json'
            fn = legends_models._frozen_version
            with mock.patch.dict(fn.__globals__, {'__file__': fake_init}):
                info.write_text(json.dumps({'version': '0.3.0-beta.1'}))
                self.assertIsNone(fn('0.3.0'))                          # not frozen: the source version
                with mock.patch.object(sys, 'frozen', True, create=True):
                    self.assertEqual(fn('0.3.0'), '0.3.0-beta.1')
                    self.assertIsNone(fn('0.4.0'))                      # a stale build-info never wins
                    info.write_text('{bad')
                    self.assertIsNone(fn('0.3.0'))
                    info.unlink()
                    self.assertIsNone(fn('0.3.0'))


def fake_dist(root: Path, version='0.3.0-beta.1', sampler=True, extra=None):
    dist = root / 'dist'
    dist.mkdir()
    archive = dist / f'legends-models-{version}-win64.zip'
    exe = b'MZ synthetic exe'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('legends-models.exe', exe)
        z.writestr('_internal/legends_models/data/build-info.json', json.dumps({'version': version}))
        z.writestr('_internal/legends_models/data/community.json', '{}')
        if sampler:
            z.writestr('_internal/legends_models/bin/sample_enbaya.exe', b'MZ')
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {'version': version, 'content_version': 1, 'zip': archive.name, 'size': archive.stat().st_size,
                'sha256': digest, 'min_launcher': '0.5.0-0', 'api': 1, 'sampler_bundled': sampler}
    (dist / 'legends-models.json').write_text(json.dumps(manifest))
    (dist / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n{hashlib.sha256(exe).hexdigest()}  '
                                         'legends-models/legends-models.exe\n')
    return dist


class VerifyDist(unittest.TestCase):
    def test_a_consistent_dist_passes(self):
        with tempfile.TemporaryDirectory() as t:
            dist = fake_dist(Path(t))
            self.assertEqual(verify_dist.verify(dist, '0.3.0-beta.1', True), [])

    def test_mismatches_are_reported(self):
        with tempfile.TemporaryDirectory() as t:
            dist = fake_dist(Path(t))
            self.assertTrue(verify_dist.verify(dist, '0.3.0', True))            # wrong version
            self.assertTrue(verify_dist.verify(dist, '0.3.0-beta.1', False))    # sampler flag
            m = json.loads((dist / 'legends-models.json').read_text())
            m['sha256'] = '0' * 64
            (dist / 'legends-models.json').write_text(json.dumps(m))
            self.assertTrue(any('sha256' in e for e in verify_dist.verify(dist, None, None)))
        with tempfile.TemporaryDirectory() as t:
            dist = fake_dist(Path(t), extra={'_internal/legends_models/data/14301.IGB': b'x', 'stray.txt': b'x'})
            errors = verify_dist.verify(dist, None, None)
            self.assertTrue(any('file types' in e for e in errors), errors)
            self.assertTrue(any('outside' in e for e in errors), errors)


class Hygiene(unittest.TestCase):
    """The public repository holds no path of the research PC and no game or skin file."""
    # a projects folder, a Steam library under a games folder, a user profile, the research work folders
    PATH = re.compile(r'[A-Za-z]:[\\/]+Projects[\\/]|[A-Za-z]:[\\/]+Games[\\/]+[^\\/\s]+[\\/]+steamapps'
                      r'|[\\/]Users[\\/]+(?!Public\b)[A-Za-z]|' + 'agent' + '-work|' + 'xml1' + r'-port[\\/]')

    def tracked(self):
        try:
            out = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-z'], capture_output=True, check=True).stdout
            files = [REPO / f for f in out.decode('utf-8').split('\0') if f]
        except (OSError, subprocess.CalledProcessError):
            files = []
        if not files:     # not a git checkout (a source zip): every file but caches
            files = [p for p in REPO.rglob('*') if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts
                     and not {'build', 'dist'} & set(p.relative_to(REPO).parts[:1])]
        return [p for p in files if p.is_file()]

    def test_no_research_pc_paths(self):
        hits = []
        for p in self.tracked():
            if p.suffix.lower() in ('.exe', '.dll', '.pyc'):
                continue
            for i, line in enumerate(p.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if self.PATH.search(line):
                    hits.append(f'{p.relative_to(REPO)}:{i}: {line.strip()[:120]}')
        self.assertEqual(hits, [])

    def test_no_game_or_skin_files(self):
        bad = [str(p.relative_to(REPO)) for p in self.tracked() if p.suffix.lower() in verify_dist.FORBIDDEN | {'.exe', '.dll'}]
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
