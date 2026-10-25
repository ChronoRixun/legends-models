"""Read the little-endian, 64-bit MUA2 PC IGZ v6 container (stdlib + numpy).

Numeric fixups and layouts are derived from local files. No game assets included.
Unknown versions/layouts fail explicitly instead of being counted as parsed skins.
"""
import argparse
import collections
import json
import struct
from pathlib import Path
import numpy as np


def require(condition, message):
    if not condition:
        raise ValueError(message)


def packed_offsets(data, count):
    nibbles = [n for b in data for n in (b & 15, b >> 4)]
    cursor = 0
    offset = 0
    result = []
    for _ in range(count):
        value = shift = 0
        while True:
            require(cursor < len(nibbles) and shift < 36, 'Invalid packed fixup')
            n = nibbles[cursor]
            cursor += 1
            value |= (n & 7) << shift
            shift += 3
            if not n & 8:
                break
        offset += (value + 1) * 4
        result.append(offset)
    require(all(n == 0 for n in nibbles[cursor:]), 'Nonzero packed fixup padding')
    return result


class IGZ:
    def __init__(self, data, name=''):
        self.data = data
        self.name = str(name)
        require(len(data) >= 0x800, 'Truncated IGZ header')
        require(self.unpack('<4I', 0) == (0x49475a01, 6, 2, 0), 'Unsupported IGZ header')
        self.sections = []
        for off in range(0x10, 0x560, 16):
            start, size, align, pool = self.unpack('<4I', off)
            if not start and not size:
                break
            require(start + size <= len(data), 'Section beyond EOF')
            self.sections.append(dict(offset=start, size=size, alignment=align, pool=pool))
        require(len(self.sections) >= 2, 'Missing IGZ sections')
        fix = self.sections[0]
        magic, version, platform, header, count, header2, zero = self.unpack('<7I', fix['offset'])
        require((magic, version, platform & 0xffff, header, header2, zero) == (0x49475a01, 6, 8, 28, 28, 0), 'Unsupported fixup header')
        self.platform_flags = platform
        off = fix['offset'] + header
        self.fixups = {}
        for _ in range(count):
            kind, zero1, zero2, number, size, payload = self.unpack('<6I', off)
            require(kind not in self.fixups and zero1 == zero2 == 0 and 24 <= payload <= size, 'Invalid fixup header')
            require(off + size <= fix['offset'] + fix['size'], 'Fixup exceeds section')
            self.fixups[kind] = dict(offset=off, count=number, size=size, data=data[off+payload:off+size])
            off += size
        require(off == fix['offset'] + fix['size'], 'Fixup section size mismatch')
        def strings(kind):
            f = self.fixups[kind]
            parts = f['data'].split(b'\0')
            require(len(parts) > f['count'] and not any(parts[f['count']:]), 'String table count/padding mismatch')
            return [s.decode('utf-8') for s in parts[:f['count']]]
        self.strings = strings(1) if 1 in self.fixups else []
        self.types = strings(0)
        require(self.fixups[13]['count']==len(self.types), 'Type/size-table count mismatch')
        self.sizes = self.unpack('<%dI' % len(self.types), self.fixups[13]['offset'] + 24)
        self.locations = {k: packed_offsets(f['data'], f['count']) for k, f in self.fixups.items() if k in (4,5,6,7,12,16)}
        self.objects = {}
        for addr in self.locations[5]:
            typ = self.u32(addr)
            require(typ < len(self.types), 'Object type outside table')
            self.resolve(addr, self.sizes[typ])
            self.objects[addr] = self.types[typ]
        for addr in self.locations.get(4, []):
            require(self.u64(addr) < len(self.strings), 'String reference outside table')
        for addr in self.locations.get(6, []):
            self.resolve(self.u64(addr))

    @classmethod
    def read(cls, path):
        return cls(Path(path).read_bytes(), path)

    def unpack(self, fmt, offset):
        require(0 <= offset <= len(self.data)-struct.calcsize(fmt), 'Read beyond EOF')
        return struct.unpack_from(fmt, self.data, offset)

    def resolve(self, addr, length=1):
        section, rel = addr >> 24, addr & 0xffffff
        require(section + 1 < len(self.sections), 'Invalid encoded section')
        s = self.sections[section + 1]
        require(rel + length <= s['size'], 'Reference exceeds section')
        return s['offset'] + rel

    def u32(self, addr): return self.unpack('<I', self.resolve(addr, 4))[0]
    def i32(self, addr): return self.unpack('<i', self.resolve(addr, 4))[0]
    def u64(self, addr): return self.unpack('<Q', self.resolve(addr, 8))[0]
    def string(self, addr):
        require(addr in self.locations.get(4, []), 'Field is not a string reference')
        return self.strings[self.u64(addr)]
    def of_type(self, typ): return [a for a, t in self.objects.items() if t == typ]

    def array(self, addr, count, dtype='<f4', width=1):
        dtype = np.dtype(dtype)
        start = self.resolve(addr, count * width * dtype.itemsize)
        a = np.frombuffer(self.data, dtype, count * width, start).copy()
        return a.reshape(count, width) if width > 1 else a

    def memory(self, addr):
        """igMemoryRef: byte count and flags followed by a relocated pointer."""
        size = self.u32(addr) & 0x07ffffff
        ptr = self.u64(addr + 8)
        if size: self.resolve(ptr, size)
        return ptr, size

    def list(self, addr, dtype='<u8'):
        require(addr in self.objects, 'List is not an object')
        count, capacity = self.u32(addr+16), self.u32(addr+20)
        ptr, size = self.memory(addr+24)
        if count == capacity == 0:
            require(ptr == 0 and size in (0,1), 'Invalid empty list')
            return []
        require(count <= capacity and capacity*np.dtype(dtype).itemsize == size, 'List count/size mismatch')
        return self.array(ptr, count, dtype).tolist() if count else []

    def skeleton(self):
        skeletons = self.of_type('igSkeleton2')
        require(len(skeletons) == 1, 'Expected one igSkeleton2')
        sk = skeletons[0]
        bones = self.list(self.u64(sk+24))
        require(all(self.objects.get(b) == 'igSkeletonBone' for b in bones), 'Wrong bone object type')
        names = [self.string(b+16) for b in bones]
        parent = np.array([self.i32(b+24) for b in bones])
        blend = np.array([self.i32(b+28) for b in bones])
        require(all(-1 <= p < i for i,p in enumerate(parent)), 'Skeleton parents are not topologically ordered')
        ptr, size = self.memory(sk+32)
        require(size == (int(blend.max())+1) * 64, 'Inverse bind count mismatch')
        ib = self.array(ptr, size//64, width=16).reshape(-1,4,4).astype(float)
        require(np.isfinite(ib).all() and np.allclose(ib[:,:,3], [0,0,0,1], atol=1e-5), 'Invalid inverse bind matrix')
        require(len(set(blend[blend>=0])) == int((blend>=0).sum()), 'Duplicate blend matrix index')
        local_t = np.array([self.array(b+32, 3) for b in bones])
        inverse = np.tile(np.eye(4), (len(bones),1,1))
        inverse[blend>=0] = ib[blend[blend>=0]]
        world = np.linalg.inv(inverse)
        return dict(names=names, parent=parent, blend=blend, inverse=inverse, world=world, local_t=local_t)

    def buffers(self):
        f = self.fixups.get(11)
        if not f: return []
        require(len(f['data']) == f['count']*16, 'Data block table size mismatch')
        result = []
        for flagged_size, addr in struct.iter_unpack('<QQ', f['data']):
            size = flagged_size & 0x07ffffff
            start = self.resolve(addr, size)
            result.append((self.data[start:start+size], addr, flagged_size))
        return result

    def exported_handles(self):
        """The root object list and named-handle list have matching ordinals."""
        roots = self.fixups[8]
        require(roots['count'] == 1, 'Expected one root list')
        root = struct.unpack_from('<I', roots['data'])[0]
        objects = self.list(root)
        handles = self.fixups.get(9)
        if not handles: return {}
        require(handles['count'] == 1, 'Expected one handle list')
        handle_list = struct.unpack_from('<I', handles['data'])[0]
        hashes = self.list(handle_list)
        require(len(hashes) == len(objects), 'Exported handle count mismatch')
        return dict(zip(hashes,objects))

    def images(self):
        # External format IDs in the owned MUA2 PC archive. Block-layout checks
        # and neutral normal-map samples distinguish BC5 from BC3 unambiguously.
        formats = {0xe2cc844b883c45b2: ('DXT1',8),
                   0xa9d8eab9b718471e: ('DXT5',16),
                   0xeb6e31b305ecd805: ('ATI2',16)}
        exids = list(struct.unpack('<%dQ' % self.fixups[2]['count'], self.fixups[2]['data']))
        result=[]
        for a in self.of_type('igImage2'):
            w,h,depth,levels = self.unpack('<4H',self.resolve(a+24,8))
            require(depth==1 and w>0 and h>0 and levels>0, 'Unsupported image dimensions')
            require(a+40 in self.locations.get(7,[]), 'Image format is not external')
            ref = self.u64(a+40)
            require(ref >> 63 == 1 and (ref & 0x7fffffffffffffff) < len(exids), 'Invalid external image format reference')
            format_id=exids[ref & 0x7fffffffffffffff]
            require(format_id in formats, 'Unsupported image format '+hex(format_id))
            fourcc,block = formats[format_id]
            raw,addr,flags = self.buffers()[self.u64(a+56)]
            sizes=[max(1,(w>>i)+3>>2)*max(1,(h>>i)+3>>2)*block for i in range(levels)]
            require(sum(sizes)==len(raw), 'Image dimensions/mips/data length mismatch')
            mips=[];offset=0
            for size in sizes:
                mips.append(raw[offset:offset+size]);offset+=size
            result.append(dict(address=a,name=self.string(a+16),width=w,height=h,levels=levels,
                               format=fourcc,format_id=hex(format_id),mips=mips))
        return result

    def vertex_buffer(self, addr):
        require(self.objects.get(addr) == 'igVertexBuffer', 'Not a vertex buffer')
        count = self.u32(addr+16)
        vf = self.u64(addr+48)
        require(self.objects.get(vf) == 'igVertexFormat', 'Missing vertex format')
        stride = self.u32(vf+16)
        raw, data_addr, flags = self.buffers()[self.u64(addr+40)]
        require(len(raw) == stride*count, 'Vertex count/stride/data size mismatch')
        ptr, size = self.memory(vf+24)
        require(size % 12 == 0, 'Invalid vertex declaration size')
        declarations = self.array(ptr, size//12, '<u4', 3)
        formats = {0: ('<f4',1), 1:('<f4',2), 2:('<f4',3), 3:('<f4',4), 4:('u1',4), 23:('u1',4)}
        attributes = {}
        declaration_summary = []
        for fmt, semantic, off in declarations:
            fmt, semantic, off = int(fmt), int(semantic), int(off)
            if fmt == 44: break  # end marker
            base_fmt = fmt & 0xff
            require(base_fmt in formats, 'Unsupported vertex format '+hex(fmt))
            dtype, width = formats[base_fmt]
            require(off + np.dtype(dtype).itemsize*width <= stride, 'Vertex element exceeds stride')
            a = np.ndarray((count,width), dtype=dtype, buffer=raw, offset=off, strides=(stride,np.dtype(dtype).itemsize)).copy()
            require(semantic not in attributes and np.isfinite(a).all(), 'Invalid/repeated vertex attribute')
            attributes[semantic] = a
            declaration_summary.append(dict(format=hex(fmt),semantic=semantic,offset=off,width=width))
        require(0 in attributes, 'Missing position stream')
        return attributes, dict(count=count, stride=stride, data_address=hex(data_addr), declarations=declaration_summary)

    def meshes(self, skeleton=None):
        sk = skeleton or self.skeleton()
        by_matrix = {int(m):i for i,m in enumerate(sk['blend']) if m>=0}
        nodes = {a:t for a,t in self.objects.items() if t in ('igGroup','igAttrSet','igBlendMatrixSelect','igGeometry','igTransform','igTransformSource')}
        parents = collections.defaultdict(list)
        for a in nodes:
            ptr = self.u64(a+56)
            if self.objects.get(ptr) == 'igNodeList':
                for child in self.list(ptr): parents[child].append(a)
        result = []
        for g in self.of_type('igGeometry'):
            attrs = self.list(self.u64(g+64))
            gas = [a for a in attrs if self.objects.get(a)=='igGeometryAttr']
            require(len(gas) == 1, 'Expected one geometry attribute')
            ga = gas[0]
            vb, ib = self.u64(ga+24), self.u64(ga+32)
            arrays, meta = self.vertex_buffer(vb)
            require(self.objects.get(ib) == 'igIndexBuffer', 'Missing index buffer')
            count = self.u32(ib+16)
            require(self.u32(ib+48) == 2, 'Unsupported index format')
            primitive = self.u32(ib+56)
            require(primitive == 3 and count % 3 == 0, 'Unsupported primitive topology')
            raw, data_addr, flags = self.buffers()[self.u64(ib+40)]
            require(len(raw) == (count*2+3)&~3, 'Index count/data size mismatch')
            require(not any(raw[count*2:]), 'Nonzero index alignment padding')
            tri = np.frombuffer(raw,'<u2',count).copy().reshape(-1,3)
            require(int(tri.max()) < meta['count'], 'Index exceeds vertex count')
            path=[];current=g;seen=set()
            while current in parents:
                require(current not in seen and len(parents[current])==1, 'Ambiguous/cyclic scene graph')
                seen.add(current);current=parents[current][0];path.append(current)
            selectors=[p for p in path if self.objects[p]=='igBlendMatrixSelect']
            require(len(selectors)==1, 'Missing/ambiguous blend palette')
            palette=np.array(self.list(self.u64(selectors[0]+208),'<i4'))
            require(all(s in arrays for s in (0,1,6,8)), 'Missing skin vertex streams')
            pos,norm,weights,indices=[arrays[k] for k in (0,1,6,8)]
            uv=arrays.get(5)
            require(weights.shape[1]==4 and indices.shape[1]==4, 'Expected four influences')
            require(np.all(weights>=0) and np.all(weights<=1) and np.all(weights.sum(1)>0), 'Invalid skin weights')
            weight_error=float(np.max(np.abs(weights.sum(1)-1)))
            require(int(indices[weights>0].max()) < len(palette), 'Active vertex palette index out of range')
            active_palette=set(indices[weights>0].tolist())
            require(all(int(palette[i]) in by_matrix for i in active_palette), 'Active palette matrix absent from skeleton')
            raw_indices=indices.copy()
            indices[weights==0] = min(active_palette)
            bone_indices=np.array([by_matrix.get(int(p),0) for p in palette])[indices]
            textures=[]
            for node in reversed(path):
                if self.objects[node] not in ('igAttrSet','igBlendMatrixSelect'):continue
                for attr in self.list(self.u64(node+64)):
                    if self.objects[attr]=='igTextureBindAttr2':
                        ta=self.u64(attr+24)
                        textures.append(dict(stage=self.u32(attr+16)&0xffff,hash=hex(self.u64(ta+72))))
            result.append(dict(pos=pos,norm=norm,uv=uv,weights=weights,indices=indices,raw_indices=raw_indices,bone_indices=bone_indices,tri=tri,palette=palette,
                               metadata=dict(address=hex(g),**meta,indices=count,triangles=len(tri),textures=textures,has_uv=uv is not None,
                                             max_weight_sum_error=weight_error,weight_sum_valid=weight_error<=2e-5)))
        require(len(result)==len(self.of_type('igGeometryAttr')), 'Unvisited geometry attributes')
        return result

    def skin_summary(self):
        sk=self.skeleton();meshes=self.meshes(sk)
        return dict(container=self.summary(),skeleton={k:v.tolist() if isinstance(v,np.ndarray) else v for k,v in sk.items()},
                    meshes=[m['metadata'] for m in meshes],vertices=sum(len(m['pos']) for m in meshes),triangles=sum(len(m['tri']) for m in meshes))
    def summary(self):
        return dict(name=self.name, bytes=len(self.data), sections=self.sections,
                    fixups={str(k):{n:v for n,v in f.items() if n != 'data'} for k,f in self.fixups.items()},
                    types=dict(collections.Counter(self.objects.values())), strings=self.strings,
                    objects=[dict(address=hex(a), file_offset=hex(self.resolve(a)), type=t) for a,t in self.objects.items()])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('--json', type=Path)
    ap.add_argument('--skin', action='store_true')
    args = ap.parse_args()
    f = IGZ.read(args.input)
    result = json.dumps(f.skin_summary() if args.skin else f.summary(), indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(result)
    else: print(result)


if __name__ == '__main__': main()
