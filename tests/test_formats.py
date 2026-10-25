# SPDX-License-Identifier: MIT
"""IGB / IGZ / .bin readers on synthetic data."""
import struct
import tempfile
import unittest
from pathlib import Path

from legends_models.formats.igb import IgbError, IgbFile
from legends_models.formats.igz import IGZ, packed_offsets
from legends_models.formats.mua_bin import Archive, decrypt, safe_parts

from synth import igb, igz, mua_bin


class IgbReader(unittest.TestCase):
    def test_parses_object_fields_and_block(self):
        f = IgbFile(igb())
        self.assertEqual(f.header[10], 0xFADA)
        self.assertEqual(len(f.objects), 1)
        o = f.objects[0]
        self.assertEqual(o.name, 'igThing')
        self.assertTrue(o.decoded)
        self.assertEqual((o.get(2), o.get(3)), (7, 'hello'))
        self.assertEqual(o.get(4), [1.5])
        off, size = f.block(1)
        self.assertEqual(f.data[off:off + size], b'abcdef')
        self.assertEqual(f.objects_of('igThing'), [o])

    def test_rebuilt_resizes_a_block_and_rereads(self):
        f = IgbFile(igb())
        g = IgbFile(f.rebuilt({1: b'0123456789'}))
        off, size = g.block(1)
        self.assertEqual(g.data[off:off + size], b'0123456789')
        self.assertEqual(g.objects[0].get(3), 'hello')

    def test_in_place_edit_round_trips(self):
        f = IgbFile(igb())
        struct.pack_into('<i', f.buf, f.objects[0].fields[2].offset, 99)
        self.assertEqual(IgbFile(f.edited()).objects[0].get(2), 99)

    def test_bad_magic_and_truncation_rejected(self):
        data = bytearray(igb())
        struct.pack_into('<I', data, 40, 0x1234)
        with self.assertRaises(IgbError):
            IgbFile(bytes(data))
        with self.assertRaises(IgbError):
            IgbFile(igb()[:20])
        with self.assertRaises(IgbError):
            IgbFile(igb()[:-12])

    def test_unknown_block_replacement_refused(self):
        with self.assertRaises(IgbError):
            IgbFile(igb()).rebuilt({0: b'x'})


class IgzReader(unittest.TestCase):
    def test_minimal_container(self):
        f = IGZ(igz())
        self.assertEqual(f.types, ['igThing'])
        self.assertEqual(f.of_type('igThing'), [4])
        self.assertEqual(len(f.sections), 2)

    def test_packed_fixups(self):
        self.assertEqual(packed_offsets(bytes([0xd1, 0x01]), 3), [8, 64, 68])
        for bad, n in [(b'\xff' * 20, 1), (b'\x88', 1), (b'\x11', 1)]:
            with self.assertRaises(ValueError):
                packed_offsets(bad, n)

    def test_rejections(self):
        with self.assertRaises(ValueError):
            IGZ(b'\x01ZGI')
        data = bytearray(igz())
        struct.pack_into('<I', data, 4, 5)          # version
        with self.assertRaises(ValueError):
            IGZ(bytes(data))
        data = bytearray(igz())
        struct.pack_into('<I', data, 0x24, 0xFFFFFF)  # data section size beyond EOF
        with self.assertRaises(ValueError):
            IGZ(bytes(data))
        with self.assertRaises(ValueError):
            IGZ(igz(object_type=3))                 # object type outside the type table
        with self.assertRaises(ValueError):
            IGZ(igz(type_size=64))                  # object exceeds its section


class MuaBin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'actors.bin'

    def tearDown(self):
        self.tmp.cleanup()

    def test_scramble_is_an_involution(self):
        raw = bytes(range(64))
        self.assertEqual(bytes(decrypt(decrypt(raw))), raw)

    def test_list_and_read(self):
        self.path.write_bytes(mua_bin([('0301.igz', b'wolverine' * 50), ('0601.igz', b'gambit')]))
        a = Archive(self.path)
        self.assertEqual([e[0] for e in a.entries], ['actors/0301.igz', 'actors/0601.igz'])
        self.assertEqual(a.read(a.entries[1]), b'gambit')
        self.assertEqual(a.read(a.entries[0]), b'wolverine' * 50)

    def test_unsafe_names_and_bad_magic_rejected(self):
        self.path.write_bytes(mua_bin([('../evil.igz', b'x')]))
        with self.assertRaises(ValueError):
            Archive(self.path)
        for bad in ['a/../b', 'CON', 'x:y', 'trail.']:
            with self.assertRaises(ValueError):
                safe_parts(bad)
        data = bytearray(mua_bin([('a.igz', b'x')]))
        data[0] ^= 1
        self.path.write_bytes(bytes(data))
        with self.assertRaises(ValueError):
            Archive(self.path)

    def test_truncated_payload_rejected(self):
        self.path.write_bytes(mua_bin([('a.igz', b'x' * 1000)])[:-5])
        with self.assertRaises(ValueError):
            Archive(self.path)


if __name__ == '__main__':
    unittest.main()
