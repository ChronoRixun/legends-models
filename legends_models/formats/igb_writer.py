"""Grow a v6 (XML1/XML2) IGB: new meta objects, new objects of any meta type, new memory blocks.

`IgbFile.rebuilt()` can only resize memory blocks. A material stage needs object types the converted actor does not
carry (igEnvironmentMapShader2, igTextureCubeAttr), new images and new lists, so this writer re-serialises the whole
file from its parsed sections:

  header | meta fields + alignment (unchanged bytes) | meta objects (existing + appended) |
  external directory + memory pool names (unchanged bytes) | directory (existing + appended entries) |
  index (existing + appended items) | one word | objects (existing bytes incl. in-place edits + appended) |
  memory (existing / replaced / appended blocks, each 4-aligned)

Existing reference numbers, meta indices and directory entry numbers never move, so every reference already in the
file stays valid. Meta object layouts are format facts (names, field types, slots, sizes); `meta_layout()` copies one
from any v6 file that has the type, remapping its parent and field-type indices into the target's tables.

Memory blocks: a block's directory entry is an igMemoryDirEntry (size, alignment type, pool, ...). A new block must
name an existing block whose entry it copies (only the size changes): pixel data next to pixel data, reference
lists next to reference lists. Copying the wrong kind is what broke the round-2 first probe.
"""
from __future__ import annotations

import struct

from .igb import HEADER, IgbError, IgbFile

V6 = 0xF0000006
_INT_TYPES = {'igIntMetaField', 'igUnsignedIntMetaField', 'igEnumMetaField', 'igBoolMetaField', 'igShortMetaField',
              'igUnsignedShortMetaField', 'igCharMetaField', 'igUnsignedCharMetaField', 'igObjectRefMetaField',
              'igMemoryRefMetaField', 'igRawRefMetaField'}


def meta_layout(f: IgbFile, name: str) -> dict:
    """The layout of meta object `name` in `f`: {'name', 'major', 'minor', 'parent' (name), 'slots',
    'fields': [(field type name, slot, size)]}."""
    if name not in f.meta_index:
        raise IgbError(f'{name}: not in this file')
    i = f.meta_index[name]
    head = _meta_heads(f)[i]
    m = f.metas[i]
    return {'name': name, 'major': head[1], 'minor': head[2],
            'parent': f.metas[m['parent']]['name'] if m['parent'] >= 0 else None,
            'slots': head[5], 'fields': list(m['fields'])}


def _meta_offsets(f: IgbFile):
    """(start of the meta object section, its end) for a v6 file."""
    if f.header[11] != V6:
        raise IgbError('the writer supports v6 (XML1/XML2) files only')
    off = HEADER + f.header[8]
    off += struct.unpack_from('<I', f.data, off)[0]          # alignment section
    return off, off + f.header[2]


def _meta_heads(f: IgbFile):
    start, _ = _meta_offsets(f)
    return [struct.unpack_from('<6I', f.data, start + 24 * i) for i in range(f.header[3])]


def encode_fields(f: IgbFile, meta: dict, values: dict) -> bytes:
    """Serialise one object's field values in meta field order. Missing values are 0 / -1 for references / ''."""
    out = bytearray()
    for ftype, slot, size in meta['fields']:
        v = values.get(slot)
        if ftype == 'igStringMetaField':
            raw = (v or '').encode('latin-1') + b'\0'
            raw += b'\0' * (-len(raw) % 4)
            out += struct.pack('<I', len(raw)) + raw
        elif ftype in ('igFloatMetaField', 'igVec2fMetaField', 'igVec3fMetaField', 'igVec4fMetaField',
                       'igMatrix44fMetaField'):
            n = {'igFloatMetaField': 1, 'igVec2fMetaField': 2, 'igVec3fMetaField': 3, 'igVec4fMetaField': 4,
                 'igMatrix44fMetaField': 16}[ftype]
            vals = [0.0] * n if v is None else (list(v) if isinstance(v, (list, tuple)) else [v])
            if len(vals) != n:
                raise IgbError(f'{meta["name"]} slot {slot}: {n} floats expected')
            out += struct.pack(f'<{n}f', *vals)
        elif ftype in _INT_TYPES:
            if v is None:
                v = -1 if ftype in ('igObjectRefMetaField', 'igMemoryRefMetaField') else 0
            out += struct.pack('<i', int(v))
        else:
            used = max(4, (size + 3) // 4 * 4)
            raw = bytes(v) if v is not None else b'\0' * used
            if len(raw) != used:
                raise IgbError(f'{meta["name"]} slot {slot} ({ftype}): {used} bytes expected, {len(raw)} given')
            out += raw
    return bytes(out)


class IgbWriter:
    """Collects additions to one parsed v6 file; `bytes()` returns the grown file (re-parsed and checked)."""

    def __init__(self, f: IgbFile):
        if f.header[11] != V6:
            raise IgbError('the writer supports v6 (XML1/XML2) files only')
        self.f = f
        self.new_metas: list[dict] = []
        self.new_dir: list[bytes] = []
        self.new_items: list[tuple] = []          # ('object', meta index, field bytes) | ('memory', bytes, dir entry)
        self.replaced: dict[int, bytes] = {}
        self._dir_raw = [bytes(f.buf[o:o + n]) for o, n, _ in f._dir_entries]

    # ------------------------------------------------------------------------------------------------ metas
    def meta_names(self):
        return [m['name'] for m in self.f.metas] + [m['name'] for m in self.new_metas]

    def meta(self, name: str) -> dict:
        """The (existing or added) layout of `name` with 'index'."""
        if name in self.f.meta_index:
            i = self.f.meta_index[name]
            return {'index': i, 'name': name, 'fields': self.f.metas[i]['fields']}
        for k, m in enumerate(self.new_metas):
            if m['name'] == name:
                return {'index': len(self.f.metas) + k, **m}
        raise IgbError(f'{name}: no such meta object (add it with ensure_meta)')

    def ensure_meta(self, layout: dict) -> int:
        """Add meta object `layout` (from meta_layout()) unless the file has it; returns its index."""
        names = self.meta_names()
        if layout['name'] in names:
            mine = self.meta(layout['name'])['fields']
            if [tuple(x) for x in mine] != [tuple(x) for x in layout['fields']]:
                raise IgbError(f'{layout["name"]}: layout differs from the file\'s own')
            return names.index(layout['name'])
        if layout['parent'] is not None and layout['parent'] not in names:
            raise IgbError(f'{layout["name"]}: parent {layout["parent"]} missing (add it first)')
        for ftype, _s, _z in layout['fields']:
            if ftype not in self.f.field_types:
                raise IgbError(f'{layout["name"]}: field type {ftype} not in this file')
        self.new_metas.append(dict(layout))
        return len(names)

    # ------------------------------------------------------------------------------------------------ items
    def _next_ref(self):
        return len(self.f.index) + len(self.new_items)

    def _object_dir_entry(self, meta_index: int) -> int:
        """Directory entry number of an igObjectDirEntry for meta_index (existing or new)."""
        for di, (kind, vals) in enumerate(self.f.directory):
            if kind == 'igObjectDirEntry' and vals.get(11) == meta_index:
                return di
        template = next(di for di, (kind, _v) in enumerate(self.f.directory) if kind == 'igObjectDirEntry')
        o, _n, fields = self.f._dir_entries[template]
        raw = bytearray(self._dir_raw[template])
        struct.pack_into('<i', raw, fields[11].offset - o, meta_index)
        raw = bytes(raw)
        all_entries = self._dir_raw + self.new_dir
        if raw in all_entries:
            return all_entries.index(raw)
        self.new_dir.append(raw)
        return len(self._dir_raw) + len(self.new_dir) - 1

    def add_object(self, meta_name: str, values: dict) -> int:
        m = self.meta(meta_name)
        body = encode_fields(self.f, m, values)
        ref = self._next_ref()
        self.new_items.append(('object', m['index'], body, self._object_dir_entry(m['index'])))
        return ref

    def clone_object(self, obj, changes: dict) -> int:
        """A new object of obj's type with obj's values, `changes` applied (strings may grow)."""
        values = {s: fl.value for s, fl in obj.fields.items()}
        values.update(changes)
        return self.add_object(obj.name, values)

    def _memory_dir_entry(self, size: int, template_ref: int) -> int:
        if template_ref not in self.f.blocks:
            raise IgbError(f'template {template_ref} is not a memory block')
        di = self.f.index[template_ref]
        o, _n, fields = self.f._dir_entries[di]
        raw = bytearray(self._dir_raw[di])
        struct.pack_into('<i', raw, fields[7].offset - o, size)
        raw = bytes(raw)
        all_entries = self._dir_raw + self.new_dir
        if raw in all_entries:
            return all_entries.index(raw)
        self.new_dir.append(raw)
        return len(self._dir_raw) + len(self.new_dir) - 1

    def add_memory(self, data: bytes, template_ref: int) -> int:
        """A new memory block whose directory entry copies the one of block `template_ref` (size changed)."""
        ref = self._next_ref()
        self.new_items.append(('memory', bytes(data), self._memory_dir_entry(len(data), template_ref)))
        return ref

    def replace_memory(self, ref: int, data: bytes):
        """New contents (any size) for an existing block."""
        if ref not in self.f.blocks:
            raise IgbError(f'{ref}: not a memory block of this file')
        self.replaced[ref] = bytes(data)

    def set_int(self, obj, slot: int, value: int):
        struct.pack_into('<i', self.f.buf, obj.fields[slot].offset, int(value))
        obj.fields[slot].value = int(value)

    def set_list(self, list_obj, refs, template_ref=None):
        """Make igDataList-derived `list_obj` hold `refs` (count, capacity, data block)."""
        data = struct.pack(f'<{len(refs)}i', *refs)
        block = list_obj.get(4)
        if block is not None and block >= 0 and block in self.f.blocks:
            self.replace_memory(block, data)
        else:
            if template_ref is None:
                raise IgbError('empty list: give a template reference-list block')
            self.set_int(list_obj, 4, self.add_memory(data, template_ref))
        self.set_int(list_obj, 2, len(refs))
        self.set_int(list_obj, 3, len(refs))

    # ------------------------------------------------------------------------------------------------ output
    def __bytes__(self):
        f, buf = self.f, self.f.buf
        h = list(f.header)
        # meta objects
        mstart, mend = _meta_offsets(f)
        heads = bytes(buf[mstart:mstart + 24 * h[3]])
        bodies = bytes(buf[mstart + 24 * h[3]:mend])
        names = self.meta_names()
        for m in self.new_metas:
            name = m['name'].encode('latin-1') + b'\0'
            parent = names.index(m['parent']) if m['parent'] is not None else 0xFFFFFFFF
            heads += struct.pack('<6I', len(name), m['major'], m['minor'], len(m['fields']), parent, m['slots'])
            bodies += name
            for ftype, slot, size in m['fields']:
                bodies += struct.pack('<3H', f.field_types.index(ftype), slot, size)
        meta_section = heads + bodies
        h[2], h[3] = len(meta_section), h[3] + len(self.new_metas)
        between = bytes(buf[mend:f._dir_start])                 # external directory + memory pool names
        directory = b''.join(self._dir_raw + self.new_dir)
        if len(self._dir_raw) + len(self.new_dir) > 0xFFFF:
            raise IgbError('too many directory entries')
        index = list(f.index)
        # replaced blocks of a new size need their own directory entry
        extra_dir = []
        all_dir = self._dir_raw + self.new_dir
        for ref, data in self.replaced.items():
            di = index[ref]
            o, _n, fields = f._dir_entries[di]
            if len(data) == fields[7].value:
                continue
            raw = bytearray(all_dir[di])
            struct.pack_into('<i', raw, fields[7].offset - o, len(data))
            raw = bytes(raw)
            if raw in all_dir + extra_dir:
                index[ref] = (all_dir + extra_dir).index(raw)
            else:
                extra_dir.append(raw)
                index[ref] = len(all_dir) + len(extra_dir) - 1
        directory += b''.join(extra_dir)
        objects = bytearray(buf[f._obj_start:f._mem_start])
        memory = bytearray()
        for ref in range(len(f.items)):
            if ref in f.blocks:
                memory += b'\0' * (-len(memory) % 4)
                if ref in self.replaced:
                    memory += self.replaced[ref]
                else:
                    o, n = f.blocks[ref]
                    memory += buf[o:o + n]
        for item in self.new_items:
            if item[0] == 'object':
                _k, mi, body, di = item
                objects += struct.pack('<II', mi, len(body) + 8) + body
                index.append(di)
                h[5] += 1
            else:
                _k, data, di = item
                memory += b'\0' * (-len(memory) % 4)
                memory += data
                index.append(di)
                h[7] += 1
        memory += b'\0' * (-len(memory) % 4)
        raw_index = struct.pack(f'<{len(index)}H', *index)
        raw_index += b'\0' * (-len(raw_index) % 4)
        index_section = struct.pack('<II', len(raw_index) + 8, len(index)) + raw_index
        h[0], h[1] = len(directory), len(self._dir_raw) + len(self.new_dir) + len(extra_dir)
        h[4], h[6] = len(objects), len(memory)
        isize = struct.unpack_from('<I', f.data, f._index_off)[0]
        word = bytes(buf[f._index_off + isize:f._obj_start])
        head = bytearray(buf[:mstart])
        struct.pack_into('<12I', head, 0, *h)
        result = bytes(head) + meta_section + between + directory + index_section + word + bytes(objects) + bytes(memory)
        check = IgbFile(result)
        if not all(o.decoded for o in check.objects):
            raise IgbError('grown file has undecoded objects')
        return result
