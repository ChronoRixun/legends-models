"""Community v4 support adapted from mua-models2/tools/igb_file.py (round-2 converter).

Read and patch 0xFADA Alchemy IGB: community v4, XML1/XML2 v6, MUA1 v8.

v4 has an implicit directory index and a trailing root reference; see
evidence/png8-format.md. Its returned field slots are normalized to v6.
v4 block resizing is intentionally unsupported.

Layout (little-endian u32 unless noted):
  header        12 words: [0] directory bytes, [1] directory entries, [2] meta-object bytes, [3] meta objects,
                [4] object bytes, [5] objects, [6] memory bytes, [7] memory blocks, [8] meta-field bytes,
                [9] meta fields, [10] magic 0xFADA, [11] version
  v8 strings    a size-prefixed section: count, then count x (u32 byte length, bytes); present before the meta fields
  meta fields   [9] x (name length, major, minor), then the names; [8] excludes v8's shared string section
  alignment     one section that starts with its own size
  meta objects  [3] x (name length, major, minor, field count, parent, slot count), then per meta object its name
                and field count x (field meta, slot, size) as u16                                  ([2] bytes)
  two more sections that start with their own size (the external directory, the memory pool names)
  directory     [1] entries, each (meta object, bytes, field values)                               ([0] bytes)
  index         u32 bytes, u32 count, count x u16: per reference number the directory entry of that item: an
                igObjectDirEntry item is an object, an igMemoryDirEntry item a memory block of the entry's size
  one word
  objects       each (meta object, bytes, field values), in index order                ([4] bytes, [5] objects)
  memory        the blocks in index order, each 4-aligned from the section start          ([6] bytes, [7] blocks)
Field values: a v6 string is (u32 length, bytes), while a v8 string is a u32 shared-table index. A 1- or 2-byte scalar
(bool, char, short) and a short array take 4 bytes; a float vector / matrix its floats; a long / double 8 bytes;
anything else its size. A reference (object or memory block) is the item's number in the index, -1 for none.

Every section is checked while reading (IgbError otherwise). An object whose fields do not add up to its size is kept
with decoded=False. set_* methods patch values in place; rebuilt() can replace memory blocks with arbitrary sizes but
does not add, remove or resize objects or their fields.
"""
from __future__ import annotations

import struct

MAGIC = 0xFADA
HEADER = 0x30
_FLOATS = {'igFloatMetaField': 1, 'igVec2fMetaField': 2, 'igVec3fMetaField': 3, 'igVec4fMetaField': 4,
           'igMatrix44fMetaField': 16}
_WIDE = {'igDoubleMetaField': '<d', 'igLongMetaField': '<q', 'igUnsignedLongMetaField': '<Q'}


class IgbError(ValueError):
    pass


class Field:
    __slots__ = ('slot', 'type', 'offset', 'size', 'value')

    def __init__(self, slot, type_, offset, size, value):
        self.slot, self.type, self.offset, self.size, self.value = slot, type_, offset, size, value


class Obj:
    __slots__ = ('ref', 'meta', 'name', 'offset', 'size', 'fields', 'decoded')

    def __init__(self, ref, meta, name, offset, size):
        self.ref, self.meta, self.name, self.offset, self.size = ref, meta, name, offset, size
        self.fields, self.decoded = {}, False

    def get(self, slot, default=None):
        f = self.fields.get(slot)
        return default if f is None else f.value


def _u32(data, off, n=1):
    try:
        v = struct.unpack_from(f'<{n}I', data, off)
    except struct.error as ex:
        raise IgbError(f'truncated at 0x{off:x}') from ex
    return v if n > 1 else v[0]


class IgbFile:
    """The structure of one IGB. `buf` is a mutable copy; the set_* methods write into it; bytes(buf) is the
    edited file (same size as the original)."""

    def __init__(self, data):
        self.data = bytes(data)
        self.buf = bytearray(self.data)
        d = self.data
        if len(d) < HEADER:
            raise IgbError('shorter than the header')
        h = _u32(d, 0, 12)
        if h[10] != MAGIC:
            raise IgbError(f'magic 0x{h[10]:x}, not 0x{MAGIC:x}')
        self.header = h
        self.is_v4 = h[11] == 0x80000004
        if h[11] not in (0x80000004, 0xF0000006, 0xF0000008):
            raise IgbError(f"unsupported IGB version 0x{h[11]:08x}")
        # meta fields
        off = HEADER
        self.strings = []
        if h[11] == 0xF0000008:
            string_start = off
            off += self._section(off)
            count = _u32(d, string_start + 4)
            pos = string_start + 8
            for _ in range(count):
                length = _u32(d, pos)
                pos += 4
                self.strings.append(d[pos:pos + length].split(b'\0')[0].decode('latin-1'))
                pos += length
            if pos != off:
                raise IgbError(f'string table ends at 0x{pos:x}, header says 0x{off:x}')
        meta_start = off
        recs = [_u32(d, off + 12 * i, 3) for i in range(h[9])]
        off += 12 * h[9]
        self.field_types = []
        for r in recs:
            self.field_types.append(d[off:off + r[0]].split(b'\0')[0].decode('latin-1'))
            off += r[0]
        if off != meta_start + h[8]:
            raise IgbError(f'meta fields end at 0x{off:x}, header says 0x{meta_start + h[8]:x}')
        off += self._section(off)                               # alignment
        # meta objects
        start = off
        heads = [_u32(d, off + 24 * i, 6) for i in range(h[3])]
        off += 24 * h[3]
        self.metas = []
        for nl, _maj, _min, fc, parent, _slots in heads:
            name = d[off:off + nl].split(b'\0')[0].decode('latin-1')
            off += nl
            fields = [struct.unpack_from('<3H', d, off + 6 * j) for j in range(fc)]
            off += 6 * fc
            for t, _s, _z in fields:
                if t >= len(self.field_types):
                    raise IgbError(f'meta object {name}: field meta {t} out of range')
            self.metas.append({'name': name, 'parent': parent if parent != 0xFFFFFFFF else -1,
                               'fields': [(self.field_types[t], s, z) for t, s, z in fields]})
        if off != start + h[2]:
            raise IgbError(f'meta objects end at 0x{off:x}, header says 0x{start + h[2]:x}')
        self.meta_index = {m['name']: i for i, m in enumerate(self.metas)}
        # v4 predates these two sections. Its directory is also its reference index.
        self.raw_metas = self.metas
        if self.is_v4:
            self.metas = []
            for i, m in enumerate(self.raw_metas):
                def derives(name):
                    at = i
                    for _ in range(64):
                        if at < 0: return False
                        if self.raw_metas[at]['name'] == name: return True
                        at = self.raw_metas[at]['parent']
                    raise IgbError('cyclic meta inheritance')
                def slot(s):
                    if derives('igAttr'):
                        return 2 if s == 3 else s
                    if derives('igNode'):
                        return {3: 2, 4: 3, 6: 5}.get(s, s)
                    return s - 1
                self.metas.append(dict(m, fields=[(t, slot(s), z) for t, s, z in m['fields']]))
        else:
            off += self._section(off)                           # external directory
            off += self._section(off)                           # memory pool names
        # directory
        start, self.directory = off, []
        self._dir_start, self._dir_entries = off, []          # (offset, bytes, fields) per entry, for rebuilt()
        for _ in range(h[1]):
            t, size = _u32(d, off, 2)
            if t >= len(self.metas) or size < 8:
                raise IgbError(f'directory entry at 0x{off:x}: meta {t}, {size} bytes')
            fields, used = self._decode(off + 8, t)
            if used != size - 8:
                raise IgbError(f'directory entry at 0x{off:x} ({self.metas[t]["name"]}) does not decode')
            self.directory.append((self.metas[t]['name'], {s: f.value for s, f in fields.items()}))
            self._dir_entries.append((off, size, fields))
            off += size
        if off != start + h[0]:
            raise IgbError(f'directory ends at 0x{off:x}, header says 0x{start + h[0]:x}')
        # index
        if self.is_v4:
            size, count = 0, h[1]
        else:
            size, count = _u32(d, off, 2)
        if count != h[5] + h[7]:
            raise IgbError(f'index has {count} items, header says {h[5]} objects + {h[7]} memory blocks')
        self.index = list(range(count)) if self.is_v4 else list(struct.unpack_from(f'<{count}H', d, off + 8))
        self._index_off = off
        off += size
        if not self.is_v4:
            off += 4                                            # one word (not used here)
        # objects
        start, self.items, self.objects = off, [], []
        self._obj_start = off
        blocks = []
        for ref, di in enumerate(self.index):
            if di >= len(self.directory):
                raise IgbError(f'item {ref}: directory entry {di} out of range')
            kind, vals = self.directory[di]
            if kind == 'igObjectDirEntry':
                t, osz = _u32(d, off, 2)
                if t >= len(self.metas) or osz < 8 or off + osz > len(d):
                    raise IgbError(f'object {ref} at 0x{off:x}: meta {t}, {osz} bytes')
                if t != vals.get(11):
                    raise IgbError(f'object {ref}: meta {self.metas[t]["name"]}, directory says {vals.get(11)}')
                o = Obj(ref, t, self.metas[t]['name'], off, osz)
                fields, used = self._decode(off + 8, t)
                o.fields, o.decoded = fields, used == osz - 8
                self.items.append(o)
                self.objects.append(o)
                off += osz
            elif kind == 'igMemoryDirEntry':
                self.items.append(None)
                blocks.append((ref, vals.get(7)))
            else:
                raise IgbError(f'item {ref}: directory entry {kind}')
        if len(self.objects) != h[5] or off != start + h[4]:
            raise IgbError(f'{len(self.objects)} objects ending at 0x{off:x}; header says {h[5]} ending at '
                           f'0x{start + h[4]:x}')
        # memory blocks: each starts 4-aligned from the start of the memory section; the section (and the file) is
        # padded to 4 bytes after the last block, which a 2-byte index array can leave unaligned (XML1 actor skins
        # 4803 / 48_acolytemental: the file is 2 bytes longer than the last block's end)
        self.blocks, mem = {}, off
        self._mem_start = mem
        for ref, size in blocks:
            if size is None or size < 0:
                raise IgbError(f'memory block {ref}: no size')
            off = mem + (off - mem + 3) // 4 * 4
            self.blocks[ref] = (off, size)
            off += size
        end = mem + (off - mem + 3) // 4 * 4
        if len(d) not in ((off + 4, end + 4) if self.is_v4 else (off, end)) or h[6] not in (off - mem, end - mem):
            raise IgbError(f'memory blocks end at 0x{off:x} ({off - mem} bytes), the file at 0x{len(d):x} '
                           f'(header: {h[6]} bytes)')

        self.root_ref = _u32(d, len(d) - 4) if self.is_v4 else None
        if self.is_v4 and self.obj(self.root_ref) is None:
            raise IgbError(f'v4 root reference {self.root_ref} is not an object')

    # ------------------------------------------------------------------------------------------------- reading
    def _section(self, off):
        size = _u32(self.data, off)
        if size < 4 or off + size > len(self.data):
            raise IgbError(f'section at 0x{off:x}: {size} bytes')
        return size

    def _decode(self, off, meta):
        d, fields, start = self.data, {}, off
        for ftype, slot, size in self.metas[meta]['fields']:
            if ftype == 'igStringMetaField':
                if self.header[11] == 0xF0000008:
                    index = _u32(d, off)
                    if index >= len(self.strings):
                        raise IgbError(f'string index {index} at 0x{off:x} is out of range')
                    value = self.strings[index]
                    used = 4
                else:
                    ln = _u32(d, off)
                    value = d[off + 4:off + 4 + ln].split(b'\0')[0].decode('latin-1')
                    used = 4 + ln
            elif ftype in _FLOATS:
                n = _FLOATS[ftype]
                used = 4 * n
                value = list(struct.unpack_from(f'<{n}f', d, off)) if off + used <= len(d) else None
            elif ftype in _WIDE:
                used = 8
                value = struct.unpack_from(_WIDE[ftype], d, off)[0] if off + 8 <= len(d) else None
            else:
                used = max(4, (size + 3) // 4 * 4)
                if used == 4:
                    scalar = {'igBoolMetaField': '<B', 'igCharMetaField': '<b',
                              'igUnsignedCharMetaField': '<B', 'igShortMetaField': '<h',
                              'igUnsignedShortMetaField': '<H'}.get(ftype, '<i')
                    value = struct.unpack_from(scalar, d, off)[0] if off + 4 <= len(d) else None
                else:
                    value = d[off:off + used]
            fields[slot] = Field(slot, ftype, off, used, value)
            off += used
        return fields, off - start

    def isa(self, obj, name):
        """obj's meta object is `name` or derives from it."""
        m = obj.meta if isinstance(obj, Obj) else obj
        seen = 0
        while 0 <= m < len(self.metas) and seen < 64:
            if self.metas[m]['name'] == name:
                return True
            m, seen = self.metas[m]['parent'], seen + 1
        return False

    def obj(self, ref):
        """the object with reference number `ref` (None for -1, a memory block or out of range)."""
        if not isinstance(ref, int) or not 0 <= ref < len(self.items):
            return None
        return self.items[ref]

    def block(self, ref):
        """(offset, size) of memory block `ref`, or None."""
        return self.blocks.get(ref)

    def ref_list(self, ref):
        """the i32 references a memory block holds (a node / attribute / light list's data)."""
        b = self.block(ref)
        if b is None:
            return []
        off, size = b
        return list(struct.unpack_from(f'<{size // 4}i', self.data, off))

    def list_items(self, list_ref):
        """the references of an igDataList-derived object (count slot 2, data block slot 4)."""
        lst = self.obj(list_ref)
        if lst is None or not lst.decoded or not self.isa(lst, 'igDataList'):
            return None
        refs = self.ref_list(lst.get(4))
        n = lst.get(2)
        return refs[:n] if isinstance(n, int) and 0 <= n <= len(refs) else None

    def objects_of(self, name):
        return [o for o in self.objects if self.isa(o, name)]

    # ------------------------------------------------------------------------------------------------- writing
    def set_floats(self, obj, slot, values, first=0):
        """write floats into a float field of `obj` from element `first` on (the field keeps its size)."""
        f = obj.fields[slot]
        if f.type not in _FLOATS or first + len(values) > _FLOATS[f.type]:
            raise IgbError(f'{obj.name} slot {slot}: not a float field of that size')
        struct.pack_into(f'<{len(values)}f', self.buf, f.offset + 4 * first, *values)

    def block_floats(self, ref):
        off, size = self.blocks[ref]
        return list(struct.unpack_from(f'<{size // 4}f', self.buf, off))

    def set_block_floats(self, ref, values):
        off, size = self.blocks[ref]
        if len(values) * 4 != size:
            raise IgbError(f'memory block {ref}: {size} bytes, {len(values)} floats given')
        struct.pack_into(f'<{len(values)}f', self.buf, off, *values)

    def edited(self):
        return bytes(self.buf)

    def rebuilt(self, new_blocks):
        """the file with memory blocks replaced by new contents of ANY size ({ref: bytes}), plus every in-place edit
        made through the set_* methods (self.buf). Objects keep their reference numbers: a resized block gets a
        directory entry of its own size (an existing identical entry is reused, otherwise one is appended - entries
        are shared by every block of the same size and type), the memory section is laid out again (each block
        4-aligned from the section start, the section padded to 4) and the header's directory / memory totals follow.
        Nothing else moves, so the result re-reads with IgbFile (callers should check)."""
        if self.is_v4:
            if new_blocks:
                raise IgbError('v4 memory resizing is unsupported; write geometry into a v6 template')
            return bytes(self.buf)
        buf, h = self.buf, list(self.header)
        for ref, blob in new_blocks.items():
            if ref not in self.blocks:
                raise IgbError(f'memory block {ref}: not a memory block of this file')
        entries = [bytes(buf[o:o + n]) for o, n, _f in self._dir_entries]
        index = list(self.index)
        for ref, blob in new_blocks.items():
            di = index[ref]
            o, n, fields = self._dir_entries[di]
            if len(blob) == fields[7].value:
                continue
            e = bytearray(entries[di])
            struct.pack_into('<i', e, fields[7].offset - o, len(blob))
            e = bytes(e)
            if e in entries:
                index[ref] = entries.index(e)
            else:
                index[ref] = len(entries)
                entries.append(e)
        if len(entries) > 0xFFFF:
            raise IgbError('too many directory entries')
        directory = b''.join(entries)
        # index section: same size and count, new entry numbers
        isize, icount = _u32(self.data, self._index_off, 2)
        idx = bytearray(buf[self._index_off:self._index_off + isize])
        struct.pack_into(f'<{icount}H', idx, 8, *index)
        # memory section
        mem = bytearray()
        for ref in range(len(self.items)):
            if ref not in self.blocks:
                continue
            mem += b'\0' * (-len(mem) % 4)
            if ref in new_blocks:
                mem += new_blocks[ref]
            else:
                off, size = self.blocks[ref]
                mem += buf[off:off + size]
        mem += b'\0' * (-len(mem) % 4)
        h[0], h[1], h[6] = len(directory), len(entries), len(mem)
        head = bytearray(buf[:self._dir_start])
        struct.pack_into('<12I', head, 0, *h)
        tail = buf[self._index_off + isize:self._mem_start]      # the one word + the objects
        return bytes(head) + directory + bytes(idx) + bytes(tail) + bytes(mem)


def vertex_indirect(igb, vertex_array):
    """Return the canonical 20-reference vertex stream table (v4 stores 19).

    v4/v6 share the position, normal, and UV0 indices 0, 1, 11. The absent
    twentieth v4 stream is returned as -1. No on-disk bytes are changed.
    """
    location = igb.block(vertex_array.get(2))
    if location is None:
        raise IgbError('vertex array has no indirect stream table')
    off, size = location
    if size not in (76, 80):
        raise IgbError(f'vertex indirect table is {size} bytes, expected 76 or 80')
    refs = struct.unpack_from(f'<{size // 4}i', igb.data, off)
    return refs + (-1,) * (20 - len(refs))


def png8_texture(igb, image=None):
    """Decode a community PNG8 igImage to a Pillow RGBA image.

    Uses the embedded raster and igClut, without external PNGs or file paths.
    Only the proven linear 8-bit indexed image / RGBA8888 palette combination
    is accepted. Image slots are normalized to the v6 API by IgbFile.
    """
    from PIL import Image
    if image is None:
        images = igb.objects_of('igImage')
        if not images:
            raise IgbError('no igImage')
        image = max(images, key=lambda o: o.get(2) * o.get(3))
    if image.get(11) != 65536 or image.get(18) != 8:
        raise IgbError(f'unsupported indexed image format {image.get(11)}, depth {image.get(18)}')
    width, height = image.get(2), image.get(3)
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) < 1:
        raise IgbError('invalid texture dimensions')
    clut = igb.obj(image.get(17))
    if clut is None or clut.name != 'igClut':
        raise IgbError('indexed image has no igClut')
    count = clut.get(3)
    if clut.get(2) != 7 or clut.get(4) != 4 or not isinstance(count, int) or not 1 <= count <= 256:
        raise IgbError('unsupported palette format')
    def block(ref):
        location = igb.block(ref)
        if location is None:
            raise IgbError(f'missing texture memory block {ref}')
        off, size = location
        return igb.data[off:off + size]
    palette = block(clut.get(5))
    if len(palette) != count * 4 or clut.get(6) != len(palette):
        raise IgbError('palette byte count mismatch')
    indices = block(image.get(13))
    if len(indices) != width * height:
        raise IgbError('only one linear PNG8 mip level is supported')
    if max(indices) >= count:
        raise IgbError('texture references a palette index outside its igClut')
    rgba = bytearray(4 * len(indices))
    for i, index in enumerate(indices):
        rgba[4 * i:4 * i + 4] = palette[4 * index:4 * index + 4]
    return Image.frombytes('RGBA', (width, height), bytes(rgba))
