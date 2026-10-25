# SPDX-License-Identifier: MIT
"""Costume display names come from the games' own stat tables (costume_names.py)."""
import struct
import tempfile
import unittest
from pathlib import Path

from legends_models import costume_names, costumes, roster


def xmlb(nodes):
    """Tiny XMLB writer for tests: nodes = [(tag, {attr: value})] as children of one <characters> root."""
    strings, table = {}, bytearray()
    sizes = [16] + [16 + 8 * len(a) for _, a in nodes]
    base = 8 + sum(sizes)

    def s(t):
        if t not in strings:
            strings[t] = base + len(table)
            table.extend(t.encode('latin-1') + b'\0')
        return strings[t]
    out = bytearray(struct.pack('<II', 0x11B1, 1))
    offs, pos = [], 8 + 16
    for size in sizes[1:]:
        offs.append(pos)
        pos += size
    out += struct.pack('<IIII', s('characters'), 0xFFFFFFFF, offs[0] if offs else 0xFFFFFFFF, 0)
    for i, (tag, attrs) in enumerate(nodes):
        nxt = offs[i + 1] if i + 1 < len(offs) else 0xFFFFFFFF
        out += struct.pack('<IIII', s(tag), nxt, 0xFFFFFFFF, len(attrs))
        for k, v in attrs.items():
            out += struct.pack('<II', s(k), s(v))
    return bytes(out + table)


class Readers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mua1_loose_herostat_maps_suffixes_not_order(self):
        d = self.root / 'mua1' / 'data'
        d.mkdir(parents=True)
        (d / 'herostat.engb').write_bytes(xmlb([('stats', {
            'name': 'Iceman', 'charactername': 'Iceman', 'skin': '0801', 'skin_01_name': 'Classic',
            'skin_02': '03', 'skin_02_name': 'Spiky', 'skin_03': '02', 'skin_03_name': 'Bobby Drake'})]))
        (d / 'npcstat.engb').write_bytes(xmlb([
            ('stats', {'name': 'DarkPsylocke', 'charactername': 'Dark Psylocke', 'skin': '3005'}),
            ('stats', {'name': 'IcemanSimple', 'charactername': 'Iceman', 'skin': '0801'})]))
        names = costume_names.read_mua1(self.root / 'mua1')
        self.assertEqual(names['0801'], dict(name='Classic', character='Iceman', kind='hero'))   # hero row wins
        self.assertEqual(names['0803']['name'], 'Spiky')
        self.assertEqual(names['0802']['name'], 'Bobby Drake')
        self.assertEqual(names['3005']['kind'], 'npc')

    def test_xml2_style_keys(self):
        d = self.root / 'xml2' / 'Data'
        d.mkdir(parents=True)
        (d / 'herostat.engb').write_bytes(xmlb([('stats', {'name': 'Rogue', 'skin': '0703', 'skin_aoa': '02',
                                                           'skin_astonishing': '01'})]))
        names = costume_names.read_xml2(self.root / 'xml2')
        self.assertEqual(names['0701']['name'], 'Astonishing')
        self.assertEqual(names['0702']['name'], 'Age of Apocalypse')
        self.assertEqual(names['0703']['name'], costume_names.XML2_DEFAULT)

    def test_unreadable_table_gives_no_names(self):
        d = self.root / 'bad' / 'data'
        d.mkdir(parents=True)
        (d / 'herostat.engb').write_bytes(b'not xmlb')
        self.assertEqual(costume_names.read_mua1(self.root / 'bad'), {})

    def test_install_names_win_over_the_shipped_table(self):
        hero = roster.hero('iceman')
        derived = {'mua1': {'0801': dict(name='Frosty', character='Iceman', kind='hero')}}
        self.assertEqual(costumes.info(hero, 'mua1', ['0801'], names=derived)[0]['label'], 'Frosty')
        self.assertEqual(costumes.info(hero, 'mua1', ['0801'])[0]['label'], 'Classic')


class EveryOfferedCostumeIsNamed(unittest.TestCase):
    def test_no_raw_ids(self):
        for h in roster.HEROES:
            for t in ('mua1', 'mua2', 'xml2', 'sharpen'):
                r = h.recipe(t)
                if not (r and r.source):
                    continue
                ids = sorted(set(r.alternatives) | {r.source})
                for row in costumes.info(h, t, ids):
                    self.assertNotIn(row['id'], row['label'], f'{h.key} {t} {row}')
                    self.assertNotIn('costume', row['label'].lower(), f'{h.key} {t} {row}')
                    self.assertNotIn('model ' + row['id'], row['label'], f'{h.key} {t} {row}')

    def test_story_models(self):
        self.assertEqual(costumes.info(roster.hero('psylocke'), 'mua1', ['3005'])[0]['label'], 'Dark Psylocke')
        self.assertEqual(costumes.info(roster.hero('nightcrawler'), 'mua1', ['4701'])[0]['label'],
                         'Story character model')
        self.assertEqual(costumes.info(roster.hero('phoenix'), 'mua1', ['12301'])[0]['label'],
                         'Story character model')
        self.assertEqual(costumes.info(roster.hero('gambit'), 'mua2', ['0601'])[0]['label'], 'Classic')
        self.assertEqual(costumes.info(roster.hero('wolverine'), 'mua2', ['0301'])[0]['label'], 'Modern')


if __name__ == '__main__':
    unittest.main()
