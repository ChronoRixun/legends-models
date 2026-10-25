"""Raven binary XML (XMLB/ENGB/CHRB/NAVB/BOYB, magic 0x11B1) <-> xml.etree conversion.

Layout: u32 magic 0x11B1, u32 version 1, then nodes in depth-first order, then a
deduplicated NUL-terminated string table. Each node is
    u32 name, u32 next_sibling, u32 first_child, u32 attr_count, (u32 key, u32 value) * attr_count
where every offset is absolute from the start of the file (0xFFFFFFFF = none).
"""
import struct
import xml.etree.ElementTree as ET

MAGIC = 0x11B1
NONE = 0xFFFFFFFF
HEADER = 8
MULTI_ROOT = 'xmlb_multiple_roots'


def _str(data, off):
    return data[off:data.index(b'\0', off)].decode('latin-1')


def decode(data):
    magic, version = struct.unpack_from('<II', data, 0)
    if magic != MAGIC:
        raise ValueError('not an XMLB file')

    def node(off):
        name, sib, child, count = struct.unpack_from('<IIII', data, off)
        el = ET.Element(_str(data, name))
        for i in range(count):
            k, v = struct.unpack_from('<II', data, off + 16 + 8 * i)
            el.set(_str(data, k), _str(data, v))
        c = child
        while c != NONE:
            sub, c = node(c)
            el.append(sub)
        return el, sib

    roots = []
    off = HEADER
    while off != NONE:
        el, off = node(off)
        roots.append(el)
    if len(roots) == 1:
        return roots[0]
    # a few files chain several top-level elements; hold them in a wrapper that encode() unwraps
    wrapper = ET.Element(MULTI_ROOT)
    wrapper.extend(roots)
    return wrapper


def encode(root, version=1):
    roots = list(root) if root.tag == MULTI_ROOT else [root]
    order = []

    def collect(el):
        order.append(el)
        for sub in el:
            collect(sub)

    for r in roots:
        collect(r)
    offsets = {}
    pos = HEADER
    for el in order:
        offsets[id(el)] = pos
        pos += 16 + 8 * len(el.attrib)

    strings = {}
    table = bytearray()

    def s(text):
        if text not in strings:
            strings[text] = pos + len(table)
            table.extend(text.encode('latin-1') + b'\0')
        return strings[text]

    out = bytearray(struct.pack('<II', MAGIC, version))

    def emit(el, sibling):
        children = list(el)
        name = s(el.tag)
        # key/value strings are interned in attribute order after the tag, matching Raven's tool
        pairs = [(s(k), s(v)) for k, v in el.attrib.items()]
        rel = lambda e: offsets[id(e)] if e is not None else NONE
        out.extend(struct.pack('<IIII', name, rel(sibling),
                               rel(children[0]) if children else NONE, len(pairs)))
        for k, v in pairs:
            out.extend(struct.pack('<II', k, v))
        for i, sub in enumerate(children):
            emit(sub, children[i + 1] if i + 1 < len(children) else None)

    for i, r in enumerate(roots):
        emit(r, roots[i + 1] if i + 1 < len(roots) else None)
    assert len(out) == pos
    return bytes(out + table)


def to_text(root):
    ET.indent(root)
    return ET.tostring(root, encoding='unicode')


if __name__ == '__main__':
    import sys
    cmd, src, dst = sys.argv[1:4]
    if cmd == 'd':
        open(dst, 'w', encoding='latin-1').write(to_text(decode(open(src, 'rb').read())))
    elif cmd == 'e':
        open(dst, 'wb').write(encode(ET.parse(src).getroot()))
