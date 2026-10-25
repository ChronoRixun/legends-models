# SPDX-License-Identifier: MIT
"""Synthetic container builders for the reader tests (no game data)."""
import struct
import zlib

from legends_models.formats.mua_bin import decrypt


def igb(version=0xF0000006, block=b'abcdef', value=7, text='hello', number=1.5):
    """Minimal valid IGB: one object (igThing: int, string, float) and one memory block."""
    field_types = ['igIntMetaField', 'igStringMetaField', 'igFloatMetaField']
    metas = [('igObjectDirEntry', [(0, 11, 4)]), ('igMemoryDirEntry', [(0, 7, 4)]),
             ('igThing', [(0, 2, 4), (1, 3, 4), (2, 4, 4)])]
    out = bytearray()
    # meta fields
    names = [n.encode() + b'\0' for n in field_types]
    mf = b''.join(struct.pack('<3I', len(n), 1, 0) for n in names) + b''.join(names)
    # meta objects
    heads, bodies = b'', b''
    for name, fields in metas:
        nb = name.encode() + b'\0'
        heads += struct.pack('<6I', len(nb), 1, 0, len(fields), 0xFFFFFFFF, len(fields))
        bodies += nb + b''.join(struct.pack('<3H', t, s, z) for t, s, z in fields)
    mo = heads + bodies
    # directory: entry 0 = object of meta 2, entry 1 = memory block of len(block)
    directory = struct.pack('<3I', 0, 12, 2) + struct.pack('<3I', 1, 12, len(block))
    index = struct.pack('<II', 12, 2) + struct.pack('<2H', 0, 1)
    tb = text.encode() + b'\0'
    obj_fields = struct.pack('<i', value) + struct.pack('<I', len(tb)) + tb + struct.pack('<f', number)
    obj = struct.pack('<II', 2, 8 + len(obj_fields)) + obj_fields
    mem = block + b'\0' * (-len(block) % 4)
    header = [len(directory), 2, len(mo), 3, len(obj), 1, len(mem), 1, len(mf), 3, 0xFADA, version]
    out += struct.pack('<12I', *header)
    out += mf
    out += struct.pack('<I', 4)          # alignment section
    out += mo
    out += struct.pack('<I', 4)          # external directory
    out += struct.pack('<I', 4)          # memory pool names
    out += directory + index + struct.pack('<I', 0) + obj + mem
    return bytes(out)


def igz(type_name='igThing', type_size=16, object_type=0):
    """Minimal IGZ v6 (PC, 64-bit): a fixup section (types, sizes, one object location) + one data section."""
    def fixup(kind, count, data):
        data = data + b'\0' * (-len(data) % 4)
        return struct.pack('<6I', kind, 0, 0, count, 24 + len(data), 24) + data
    fixups = fixup(0, 1, type_name.encode() + b'\0') + fixup(13, 1, struct.pack('<I', type_size)) + fixup(5, 1, b'\x00')
    fix = struct.pack('<7I', 0x49475A01, 6, 8, 28, 3, 28, 0) + fixups
    data = bytearray(32)
    struct.pack_into('<I', data, 4, object_type)
    fix_off = 0x800
    data_off = fix_off + len(fix)
    head = bytearray(0x800)
    struct.pack_into('<4I', head, 0, 0x49475A01, 6, 2, 0)
    struct.pack_into('<4I', head, 0x10, fix_off, len(fix), 16, 0)
    struct.pack_into('<4I', head, 0x20, data_off, len(data), 16, 0)
    return bytes(head) + fix + bytes(data)


def mua_bin(files, stem_check=None):
    """MUA PC .bin (version 1, flags 0x11): zlib streams XOR-scrambled by mua_bin.decrypt (an involution)."""
    def pack(raw):
        return bytes(decrypt(zlib.compress(raw)))
    names = b''
    payloads = b''
    toc = b''
    for name, content in files:
        off = len(names)
        names += name.encode() + b'\0'
        p = pack(content)
        toc += struct.pack('<5Q', 0, off, len(payloads), len(p), len(content))
        payloads += p
    ptoc, pnames = pack(toc), pack(names)
    maximum = max((len(c) for _, c in files), default=0)
    head = struct.pack('<IHHIIIII', 0xB19ACA6E, 1, 0x11, len(files), len(ptoc), len(names), len(pnames), maximum)
    return head + ptoc + pnames + payloads
