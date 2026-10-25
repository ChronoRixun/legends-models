"""Read-only Raven binary XML (XMLB / ENGB, magic 0x11B1) iterator.

Layout: u32 magic 0x11B1, u32 version, then nodes depth-first, then a NUL-terminated string table.
Node: u32 name, u32 next_sibling, u32 first_child, u32 attr_count, (u32 key, u32 value) * attr_count;
offsets are absolute (0xFFFFFFFF = none). Only what the costume-name reader needs.
"""
import struct

MAGIC = 0x11B1
NONE = 0xFFFFFFFF


def _str(data, off):
    return data[off:data.index(b'\0', off)].decode('latin-1')


def elements(data):
    """Yield (tag, {attr: value}) for every node, depth-first. Raises ValueError for a non-XMLB file."""
    if len(data) < 8 or struct.unpack_from('<I', data, 0)[0] != MAGIC:
        raise ValueError('not an XMLB file')
    stack = [8]
    seen = 0
    while stack:
        off = stack.pop()
        if off == NONE:
            continue
        seen += 1
        if seen > len(data):
            raise ValueError('XMLB node loop')
        name, sib, child, count = struct.unpack_from('<IIII', data, off)
        attrs = {}
        for i in range(count):
            k, v = struct.unpack_from('<II', data, off + 16 + 8 * i)
            attrs[_str(data, k)] = _str(data, v)
        yield _str(data, name), attrs
        stack.append(sib)
        stack.append(child)
