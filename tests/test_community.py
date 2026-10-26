# SPDX-License-Identifier: MIT
"""Community download / quarantine / allow-list logic with fake transports (no network)."""
import os
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legends_models import community, roster
from legends_models.errors import DOWNLOAD, ToolError

ARCHIVE = b'Rar!\x1a\x07\x00' + b'synthetic archive bytes' * 10


def record(data=ARCHIVE, **kw):
    r = dict(character='Beast', costume='Classic', archive='05XX_Classic.rar', bytes=len(data),
             sha256=hashlib.sha256(data).hexdigest(), url='https://www.mediafire.com/file_premium/abc123/05XX_Classic.rar/file',
             members=['readme.txt', 'folder', 'folder/05XX (Skin).igb', '05XX (Skin).png'])
    r.update(kw)
    return r


PAGE = b'<html><a class="input" id="downloadButton" href="https://download1234.mediafire.com/x/05XX_Classic.rar">Download</a></html>'


class Manifest(unittest.TestCase):
    def test_shipped_manifest_is_well_formed(self):
        data = community.manifest()
        self.assertEqual(data['author'], 'BaconWizard17')
        self.assertEqual(len(data['archives']), 35)
        for r in data['archives']:
            self.assertRegex(r['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(community.AUTHOR_URL.fullmatch(r['url']), r['url'])
            community.archive_name(r)
            community.check_member_names(r['members'], data['allowed_member_suffixes'])
            self.assertTrue(any(m.lower().endswith('(skin).igb') for m in r['members']), r)
            self.assertIn(r['character'], community.NAMES)

    def test_every_community_recipe_has_its_archives(self):
        data = community.manifest()
        for h in roster.HEROES:
            r = h.recipe('community')
            if r and r.status != 'none':
                for costume in set(r.alternatives) | {r.source}:
                    community.entry(h.key, costume, data)

    def test_credits_name_every_character(self):
        text = community.credits_text()
        self.assertIn('BaconWizard17', text)
        for name in {r['character'] for r in community.manifest()['archives']}:
            self.assertRegex(text, r'(?m)^## ' + re.escape(name) + r'\s*$')


class AllowList(unittest.TestCase):
    def test_unsafe_paths_and_extensions(self):
        ok = ['a.igb', 'dir', 'dir/', 'dir/b.png', 'readme.txt']
        community.check_member_names(ok, ['.igb', '.png', '.txt'])
        for bad in ['../x.igb', '/abs.igb', 'C:/x.igb', 'a/../b.igb', 'trail./x.igb', 'run.exe', 'lib.dll', 'x.bat',
                    'script.py', 'a.lnk']:
            with self.assertRaises(ToolError, msg=bad):
                community.check_member_names([bad], ['.igb', '.png', '.txt'])
        with self.assertRaises(ToolError):
            community.check_member_names([], ['.igb'])

    def test_members_must_equal_manifest(self):
        r = record()
        community.check_against_manifest(['readme.txt', 'folder', 'folder\\05XX (Skin).igb', '05XX (Skin).png'], r, ['.igb', '.png', '.txt'])
        with self.assertRaises(ToolError):
            community.check_against_manifest(r['members'] + ['extra.png'], r, ['.igb', '.png', '.txt'])
        with self.assertRaises(ToolError):
            community.check_against_manifest(r['members'][:-1], r, ['.igb', '.png', '.txt'])


class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mediafire_page_to_direct_link(self):
        self.assertEqual(community.resolve_mediafire(PAGE), 'https://download1234.mediafire.com/x/05XX_Classic.rar')
        for page in [b'<a id="downloadButton" href="https://evil.example/x.rar">', b'<a id="downloadButton" href="http://download.mediafire.com/x">',
                     b'<html>no button</html>']:
            with self.assertRaises(ToolError):
                community.resolve_mediafire(page)

    def test_author_page_then_archive_verified_and_moved_out_of_quarantine(self):
        calls = []

        def get(url, limit):
            calls.append(url)
            return PAGE if 'file_premium' in url else ARCHIVE
        p = community.fetch(record(), self.cache, get=get)
        self.assertEqual(p.read_bytes(), ARCHIVE)
        self.assertEqual(p.parent.name, 'archives')
        self.assertEqual(list((self.cache / 'quarantine').iterdir()), [])
        self.assertEqual(len(calls), 2)
        # cached: no further transport calls
        community.fetch(record(), self.cache, get=lambda u, l: self.fail('refetched'))

    def test_direct_archive_response_is_accepted(self):
        p = community.fetch(record(), self.cache, get=lambda u, l: ARCHIVE)
        self.assertTrue(p.is_file())

    def test_hash_or_size_mismatch_refused_and_quarantine_cleaned(self):
        bad = ARCHIVE[:-1] + b'X'
        with self.assertRaises(ToolError) as e:
            community.fetch(record(), self.cache, get=lambda u, l: bad)
        self.assertEqual(e.exception.code, DOWNLOAD)
        self.assertFalse(any((self.cache / 'archives').glob('*')) if (self.cache / 'archives').exists() else False)
        self.assertEqual(list((self.cache / 'quarantine').glob('*.part')), [])
        with self.assertRaises(ToolError):
            community.fetch(record(), self.cache, get=lambda u, l: ARCHIVE + b'more')

    # --- author's link first, the GitHub backup only on failure (fake HTTP, no network)
    MIRROR = 'https://github.com/ChronoRixun/legends-models/releases/download/community-skins-backup-1'

    def fake_http(self, author, mirror):
        """A transport: author/mirror are bytes to serve or an exception to raise. Records every URL asked."""
        seen = []

        def get(url, limit):
            seen.append(url)
            if url.startswith('https://github.com/'):
                answer = mirror
            elif 'file_premium' in url:   # the author's MediaFire page: errors and a page without a button come from it
                answer = author if isinstance(author, BaseException) or author.startswith(b'<html') else PAGE
            else:                          # the direct link from the page
                answer = author
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return seen, get

    def events(self):
        got = []

        class Rep:
            def emit(self, event, **kw):
                got.append(dict(kw, event=event))
        return got, Rep()

    def test_author_ok_mirror_never_asked(self):
        seen, get = self.fake_http(ARCHIVE, AssertionError('mirror asked'))
        got, rep = self.events()
        p = community.fetch(record(), self.cache, mirror=self.MIRROR, get=get, report=rep)
        self.assertEqual(community.sha256_file(p), record()['sha256'])
        self.assertFalse(any(u.startswith('https://github.com/') for u in seen), seen)
        dl = [e for e in got if e['event'] == 'download']
        self.assertEqual([(e['source'], e['source_label']) for e in dl], [('author', "author's link")])
        self.assertNotIn('author_error', dl[0])

    def test_author_fails_then_github_backup(self):
        import io
        import socket
        import urllib.error
        bad = ARCHIVE[:-1] + b'X'
        for why, author in [('http 404', urllib.error.HTTPError('https://www.mediafire.com/x', 404, 'Not Found', {}, io.BytesIO(b''))),
                            ('timeout', socket.timeout('timed out')), ('timeout', TimeoutError('timed out')),
                            ('link removed', b'<html>no button</html>'), ('hash mismatch', bad),
                            ('truncated', ARCHIVE[:-5])]:
            with self.subTest(why=why), tempfile.TemporaryDirectory() as t:
                cache = Path(t)
                seen, get = self.fake_http(author, ARCHIVE)
                got, rep = self.events()
                p = community.fetch(record(), cache, mirror=self.MIRROR + '/', get=get, report=rep)
                self.assertEqual(p.read_bytes(), ARCHIVE)
                self.assertTrue(seen[0].startswith('https://www.mediafire.com/'), seen)   # author first
                self.assertEqual(seen[-1], self.MIRROR + '/05XX_Classic.rar')
                dl = [e for e in got if e['event'] == 'download']
                self.assertEqual(len(dl), 1)
                self.assertEqual((dl[0]['source'], dl[0]['source_label']), ('mirror', 'GitHub backup'))
                self.assertEqual(dl[0]['sha256'], record()['sha256'])
                self.assertTrue(dl[0]['author_error'])
                self.assertEqual(list((cache / 'quarantine').glob('*.part')), [])

    def test_github_backup_hash_mismatch_refused(self):
        bad = ARCHIVE[:-1] + b'X'
        seen, get = self.fake_http(OSError('author down'), bad)
        got, rep = self.events()
        with self.assertRaises(ToolError) as e:
            community.fetch(record(), self.cache, mirror=self.MIRROR, get=get, report=rep)
        self.assertEqual(e.exception.code, DOWNLOAD)
        self.assertIn("author's link: author down", str(e.exception))
        self.assertIn('GitHub backup: SHA-256', str(e.exception))
        self.assertEqual(len(seen), 2)
        self.assertEqual([x for x in got if x['event'] == 'download'], [])
        self.assertFalse((self.cache / 'archives').exists() and any((self.cache / 'archives').iterdir()))
        self.assertEqual(list((self.cache / 'quarantine').glob('*')), [])
        # a wrong-size backup file is refused the same way
        with self.assertRaises(ToolError):
            community.fetch(record(), self.cache, mirror=self.MIRROR, get=self.fake_http(OSError('x'), ARCHIVE + b'!')[1])

    def test_no_mirror_means_author_only(self):
        seen, get = self.fake_http(OSError('author down'), ARCHIVE)
        with self.assertRaises(ToolError):
            community.fetch(record(), self.cache, mirror=community.resolve_mirror(None, {'mirror': self.MIRROR}, disabled=True),
                            get=get)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith('https://www.mediafire.com/'))

    def test_mirror_url_checks_and_labels(self):
        with self.assertRaises(ToolError):
            community.source_urls(record(), mirror='http://insecure.example/')
        self.assertEqual([k for k, _ in community.source_urls(record(), mirror=self.MIRROR)], ['author', 'mirror'])
        self.assertEqual(community.source_urls(record())[0][0], 'author')
        self.assertEqual(community.source_label('author'), "author's link")
        self.assertEqual(community.source_label('mirror', self.MIRROR + '/05XX_Classic.rar'), 'GitHub backup')
        self.assertEqual(community.source_label('mirror', 'https://mirror.example/r/05XX_Classic.rar'), 'backup mirror')

    def test_shipped_mirror_is_the_github_backup_release(self):
        with mock.patch.dict('os.environ', {}, clear=False):
            os.environ.pop('LEGENDS_MODELS_MIRROR', None)
            self.assertEqual(community.resolve_mirror(), self.MIRROR)
            self.assertIsNone(community.resolve_mirror(disabled=True))

    def test_resolve_mirror_order(self):
        with mock.patch.dict('os.environ', {}, clear=False):
            os.environ.pop('LEGENDS_MODELS_MIRROR', None)
            self.assertIsNone(community.resolve_mirror(None, {}))
            self.assertEqual(community.resolve_mirror(None, {'mirror': 'https://m.example/a'}), 'https://m.example/a')
            os.environ['LEGENDS_MODELS_MIRROR'] = 'https://env.example/b'
            self.assertEqual(community.resolve_mirror(None, {'mirror': 'https://m.example/a'}), 'https://env.example/b')
            self.assertEqual(community.resolve_mirror('https://arg.example/c', {}), 'https://arg.example/c')
            self.assertIsNone(community.resolve_mirror('https://arg.example/c', {}, disabled=True))
            os.environ['LEGENDS_MODELS_MIRROR'] = 'off'
            self.assertIsNone(community.resolve_mirror(None, {'mirror': 'https://m.example/a'}))
            os.environ.pop('LEGENDS_MODELS_MIRROR')
            self.assertIsNone(community.resolve_mirror('  ', {'mirror': ''}))
            self.assertIsNone(community.resolve_mirror('OFF', {'mirror': 'https://m.example/a'}))

    def test_unapproved_urls_and_names_refused(self):
        for r in [record(url='https://example.com/05XX_Classic.rar'), record(archive='..\\evil.rar'),
                  record(archive='payload.exe')]:
            with self.assertRaises(ToolError):
                community.fetch(r, self.cache, get=lambda u, l: ARCHIVE)

    def test_size_limit_enforced_by_transport(self):
        with mock.patch('urllib.request.urlopen') as op:
            op.return_value.__enter__.return_value.read.return_value = b'x' * 11
            with self.assertRaises(ToolError):
                community.http_get('https://www.mediafire.com/x', 10)


class Extract(unittest.TestCase):
    def test_extract_checks_listing_then_landed_files(self):
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / 'out'
            r = record()

            def fake_run(cmd):
                if cmd[1] == '-tf':
                    return '\n'.join(r['members'])
                (dest / 'folder').mkdir(parents=True, exist_ok=True)
                (dest / 'folder' / '05XX (Skin).igb').write_bytes(b'x')
                (dest / 'readme.txt').write_text('hi')
                return ''
            with mock.patch.object(community, '_run', fake_run):
                community.extract(Path(t) / 'a.rar', dest, r, ('tar', 'tar.exe'), ['.igb', '.png', '.txt'])
            self.assertEqual(community.skin_file(dest), dest / 'folder' / '05XX (Skin).igb')

            def sneaky_run(cmd):
                if cmd[1] == '-tf':
                    return '\n'.join(r['members'])
                (dest2 / 'surprise.dll').write_bytes(b'x')
                return ''
            dest2 = Path(t) / 'out2'
            with mock.patch.object(community, '_run', sneaky_run):
                with self.assertRaises(ToolError):
                    community.extract(Path(t) / 'a.rar', dest2, r, ('tar', 'tar.exe'), ['.igb', '.png', '.txt'])

    def test_refuses_non_empty_destination_and_bad_listing(self):
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t)
            (dest / 'old.txt').write_text('x')
            r = record()
            with mock.patch.object(community, '_run', lambda cmd: '\n'.join(r['members'])):
                with self.assertRaises(ToolError):
                    community.extract(dest / 'a.rar', dest, r, ('tar', 't'), ['.igb', '.png', '.txt'])
            with mock.patch.object(community, '_run', lambda cmd: 'evil.exe'):
                with self.assertRaises(ToolError):
                    community.extract(dest / 'a.rar', dest / 'new', r, ('tar', 't'), ['.igb', '.png', '.txt'])


if __name__ == '__main__':
    unittest.main()
