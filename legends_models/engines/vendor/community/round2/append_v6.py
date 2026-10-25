"""Append clones of existing v6 object types and memory blocks, preserving old refs."""
import struct
from igb_file import IgbFile
from round1_converter import put_int

class Appender:
    def __init__(self,f):self.f=f;self.extra=[]
    def memory(self,data,template_ref=None):
        ref=len(self.f.index)+len(self.extra)
        self.extra.append(('memory',(bytes(data),template_ref if template_ref is not None else next(iter(self.f.blocks)))))
        return ref
    def clone(self,obj,changes):
        ref=len(self.f.index)+len(self.extra);raw=bytearray(self.f.buf[obj.offset:obj.offset+obj.size])
        for slot,value in changes.items():struct.pack_into('<i',raw,obj.fields[slot].offset-obj.offset,int(value))
        self.extra.append(('object',(self.f.index[obj.ref],bytes(raw))));return ref
    def finish(self,replacements=None):
        f=IgbFile(self.f.rebuilt(replacements or {}));h=list(f.header)
        entries=[bytes(f.buf[o:o+n]) for o,n,_ in f._dir_entries];idx=list(f.index)
        objs=bytearray(f.buf[f._obj_start:f._mem_start]);mem=bytearray(f.buf[f._mem_start:])
        for kind,data in self.extra:
            if kind=='object':
                olddi,raw=data;idx.append(olddi);objs+=raw;h[5]+=1
            else:
                data,template=data;di=f.index[template];o,n,fields=f._dir_entries[di]
                entry=bytearray(entries[di]);struct.pack_into('<i',entry,fields[7].offset-o,len(data));entry=bytes(entry)
                if entry not in entries:entries.append(entry)
                idx.append(entries.index(entry));mem+=b'\0'*(-len(mem)%4);mem+=data;h[7]+=1
        mem+=b'\0'*(-len(mem)%4);directory=b''.join(entries)
        rawidx=struct.pack('<'+str(len(idx))+'H',*idx);rawidx+=b'\0'*(-len(rawidx)%4)
        index=struct.pack('<II',len(rawidx)+8,len(idx))+rawidx
        h[0]=len(directory);h[1]=len(entries);h[4]=len(objs);h[6]=len(mem)
        head=bytearray(f.buf[:f._dir_start]);struct.pack_into('<12I',head,0,*h)
        oldsize=struct.unpack_from('<I',f.data,f._index_off)[0];word=f.buf[f._index_off+oldsize:f._obj_start]
        result=bytes(head)+directory+index+word+objs+mem
        check=IgbFile(result)
        if not all(o.decoded for o in check.objects):raise ValueError('Appended graph failed parse')
        return bytes(result)
