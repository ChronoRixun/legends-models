# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path

from legends_models import loadorder as lo


class LoadOrder(unittest.TestCase):
    def test_reads_like_the_loader(self):
        text = '﻿# comment\n+Better HUD\n-Unused Mod  \n\nrandom line\n+My Skin\r\n'
        self.assertEqual(lo.entries(text), [('Better HUD', True), ('Unused Mod', False), ('My Skin', True)])

    def test_legacy_space_form_is_what_the_loader_reads_and_gets_normalised(self):
        self.assertEqual(lo.entries('+ MUA-Wolverine\n'), [(' MUA-Wolverine', True)])
        out = lo.set_entry('# keep\n+ MUA-Wolverine\n+Other\n', 'MUA-Wolverine')
        self.assertEqual(out, '# keep\n+MUA-Wolverine\n+Other\n')

    def test_set_keeps_position_and_other_lines(self):
        text = '# order\n+A\n-Models-Community\n+B\n'
        self.assertEqual(lo.set_entry(text, 'Models-Community'), '# order\n+A\n+Models-Community\n+B\n')
        self.assertEqual(lo.set_entry(text, 'models-community', enabled=False), '# order\n+A\n-models-community\n+B\n')
        self.assertEqual(lo.set_entry('+A\n', 'New'), '+A\n+New\n')
        self.assertEqual(lo.set_entry('', 'New'), '+New\n')

    def test_duplicates_collapse_to_first_position(self):
        self.assertEqual(lo.set_entry('+X\n+A\n-x\n', 'X'), '+X\n+A\n')

    def test_bom_and_crlf_preserved(self):
        out = lo.set_entry('﻿+A\r\n+B\r\n', 'C')
        self.assertTrue(out.startswith('﻿'))
        self.assertEqual(out, '﻿+A\r\n+B\r\n+C\r\n')

    def test_remove(self):
        self.assertEqual(lo.remove_entry('# c\n+A\n- B\n+C\n', 'B'), '# c\n+A\n+C\n')
        self.assertEqual(lo.remove_entry('+A\n', 'A'), '')
        self.assertEqual(lo.remove_entry('+A\n', 'Z'), '+A\n')

    def test_is_enabled(self):
        self.assertTrue(lo.is_enabled('+A\n', 'a'))
        self.assertFalse(lo.is_enabled('-A\n', 'A'))
        self.assertIsNone(lo.is_enabled('+B\n', 'A'))

    def test_effective_order(self):
        folders = ['Zed', 'B', 'A', '.legends-models-staging-x', 'Disabled']
        text = '+B\n-Disabled\n+Missing\n+A\n'
        self.assertEqual(lo.effective_order(text, folders), ['B', 'A', 'Zed'])

    def test_unsafe_names(self):
        for bad in ['', ' lead', 'trail ', '.hidden', 'a/b', 'a\\b', 'c:d', 'x*']:
            with self.assertRaises(ValueError):
                lo.set_entry('', bad)

    def test_atomic_write_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            mods = Path(d) / 'mods'
            lo.write(mods, lo.set_entry(lo.read(mods), 'P'))
            self.assertEqual((mods / 'load-order.txt').read_bytes(), b'+P\n')
            self.assertEqual(sorted(p.name for p in mods.iterdir()), ['load-order.txt'])


if __name__ == '__main__':
    unittest.main()
