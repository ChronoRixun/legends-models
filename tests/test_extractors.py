# SPDX-License-Identifier: MIT
"""Real extractor command lines against a synthetic RAR (skipped unless WinRAR's Rar.exe is installed
to create one). Checks the listing parser and the post-extraction checks of each available tool."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from legends_models import community
from legends_models.errors import ToolError

RAR = Path(r'C:\Program Files\WinRAR\Rar.exe')


@unittest.skipUnless(RAR.is_file(), 'Rar.exe not installed')
class LiveExtractors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        src = root / 'src'
        (src / 'Inner Folder').mkdir(parents=True)
        (src / '05XX (Skin).igb').write_bytes(b'synthetic skin')
        (src / 'Inner Folder' / 'readme.txt').write_text('synthetic readme')
        cls.archive = root / 'test.rar'
        subprocess.run([str(RAR), 'a', '-r', '-ep1', '-idq', str(cls.archive), str(src / '*')], check=True)
        data = cls.archive.read_bytes()
        cls.record = dict(archive='05XX_Test.rar', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          members=['05XX (Skin).igb', 'Inner Folder', 'Inner Folder/readme.txt'])
        cls.allowed = ['.igb', '.png', '.txt']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tools(self):
        found = [community.find_extractor(k) for k in ('unrar', '7z', 'tar')]
        return [f for f in found if f]

    def test_each_extractor_lists_and_extracts(self):
        tools = self.tools()
        self.assertTrue(tools)
        for i, ex in enumerate(tools):
            names = community.list_members(self.archive, ex)
            community.check_against_manifest(names, self.record, self.allowed)
            dest = Path(self.tmp.name) / f'out-{i}'
            community.extract(self.archive, dest, self.record, ex, self.allowed)
            self.assertEqual(community.skin_file(dest).read_bytes(), b'synthetic skin', ex)
            self.assertEqual((dest / 'Inner Folder' / 'readme.txt').read_text(), 'synthetic readme')

    def test_manifest_mismatch_refused_before_extraction(self):
        for i, ex in enumerate(self.tools()):
            rec = dict(self.record, members=['05XX (Skin).igb'])
            dest = Path(self.tmp.name) / f'refused-{i}'
            with self.assertRaises(ToolError):
                community.extract(self.archive, dest, rec, ex, self.allowed)
            self.assertFalse(dest.exists() and any(dest.iterdir()))

    def test_verify_archive(self):
        community.verify_archive(self.archive, self.record)
        with self.assertRaises(ToolError):
            community.verify_archive(self.archive, dict(self.record, sha256='0' * 64))


if __name__ == '__main__':
    unittest.main()
