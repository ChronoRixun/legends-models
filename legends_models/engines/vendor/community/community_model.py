"""Community model selection and v4 support, adapted from mua-models2/tools/model.py."""
"""IGB skin inspection and row-vector linear blend skinning, no game execution."""
import io, struct, sys
from types import SimpleNamespace
sys.dont_write_bytecode=True
import re
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.append(str(Path(__file__).resolve().parents[2]/"mua-models2/tools"))
from community_igb import IgbFile
from round1_converter import blob, geometry_attribute

def array(f, ref, dtype='<f4', width=None):
    a=np.frombuffer(blob(f,ref),dtype=dtype).copy()
    return a.reshape(-1,width) if width else a

def blend_matrix_select(f, geometry_attr):
    """Find the selector whose scene subtree owns this drawable attribute.

    Children live in igGroup slot 7, attributes in igAttrSet slot 8. A nested
    selector overrides its ancestor's palette, so traversal stops there.
    Geometry enumeration order and selector enumeration order are unrelated.
    """
    matches=[]
    for selector in f.objects_of('igBlendMatrixSelect'):
        pending=[selector];seen=set()
        while pending:
            node=pending.pop()
            if node.ref in seen:continue
            seen.add(node.ref)
            if node.ref==geometry_attr.ref:
                matches.append(selector);break
            if node.ref!=selector.ref and f.isa(node,'igBlendMatrixSelect'):continue
            refs=[]
            if f.isa(node,'igGroup'):refs.extend(f.list_items(node.get(7)) or [])
            if f.isa(node,'igAttrSet'):refs.extend(f.list_items(node.get(8)) or [])
            pending.extend(obj for ref in refs if (obj:=f.obj(ref)) is not None)
    if len(matches)!=1:
        raise ValueError(f'geometry attribute {geometry_attr.ref} has {len(matches)} enclosing blend selectors')
    return matches[0]


class Model:
    def __init__(self,path,geometry=None):
        self.path=Path(path);self.f=IgbFile(self.path.read_bytes());f=self.f
        sk=f.objects_of('igSkeleton')[0];self.sk=sk
        self.bones=[f.obj(r) for r in f.list_items(sk.get(4))]
        self.names=[b.get(2) for b in self.bones]
        self.parent=np.array([b.get(3) for b in self.bones])
        self.local_t=array(f,sk.get(3),width=3).astype(float)
        self.ib=array(f,sk.get(5)).reshape(-1,4,4).astype(float)
        self.world=np.tile(np.eye(4),(len(self.bones),1,1))
        self.inverse_by_bone=np.tile(np.eye(4),(len(self.bones),1,1))
        ordinal=0
        for i,b in enumerate(self.bones):
            if b.get(4)>=0:
                # libIGSg RVA 0x5d5b6..0x5d5de: inverse binds advance
                # sequentially; bmIdx chooses only the OUTPUT cache slot.
                self.inverse_by_bone[i]=self.ib[ordinal]
                self.world[i]=np.linalg.inv(self.ib[ordinal]);ordinal+=1
        # Bip01 has no blend matrix in XML1; pelvis is a coincident child.
        if 'Bip01' in self.names and self.bones[self.names.index('Bip01')].get(4)<0:
            i=self.names.index('Bip01');self.world[i,3,:3]=self.local_t[i]
        self.local=np.array([w @ np.linalg.inv(self.world[p]) if p>=0 else w for w,p in zip(self.world,self.parent)])
        def info(ga):
            va=f.obj(ga.get(4));raw=blob(f,va.get(2))
            if va.name=='igVertexArray2':return va.get(2),True
            ind=struct.unpack('<%di'%(len(raw)//4),raw)
            return va.get(3),len(ind)>11 and ind[11]>=0
        attrs=[o for o in f.objects if f.isa(o,'igGeometryAttr') or o.name=='igGeometryAttr2']
        if geometry and geometry!='__outline__':
            g=next((g for g in f.objects_of('igGeometry') if g.get(2)==geometry),None)
            ga=geometry_attribute(f,g) if g else max((a for a in attrs if info(a)[1]),key=lambda a:info(a)[0])
        else:
            wants_uv=geometry!='__outline__'
            ga=max((a for a in attrs if info(a)[1]==wants_uv),key=lambda a:info(a)[0])
            g=next((g for g in f.objects_of('igGeometry') if any(r==ga.ref for r in f.list_items(g.get(8)) or [])),None)
        self.g=g or SimpleNamespace(get=lambda slot: self.path.stem if slot==2 else None)
        self.ga=ga;va=f.obj(ga.get(4));self.va=va
        if va.name=='igVertexArray2':
            streams={f.obj(r).get(4):f.obj(f.obj(r).get(3)) for r in f.list_items(va.get(3))}
            self.pos=array(f,streams[1].get(4),width=3)
            self.norm=array(f,streams[3].get(4),width=3)
            self.uv=array(f,streams[4].get(4),width=2)
            weights=array(f,streams[5].get(4)).reshape(len(self.pos),-1)
            indices=array(f,streams[6].get(4),'u1').reshape(len(self.pos),-1)
            if weights.shape!=indices.shape or not 1<=weights.shape[1]<=4:raise ValueError('Unsupported source influence streams')
            self.weights=np.pad(weights,((0,0),(0,4-weights.shape[1])))
            self.indices=np.pad(indices,((0,0),(0,4-indices.shape[1])),mode='edge')
            self.indirect=None
        else:
            raw=blob(f,va.get(2));ind=struct.unpack('<%di'%(len(raw)//4),raw);ind=ind+(-1,)*(20-len(ind));self.indirect=ind
            self.pos=array(f,ind[0],width=3)
            self.norm=array(f,ind[1],width=3) if ind[1]>=0 else None
            self.uv=array(f,ind[11],width=2) if ind[11]>=0 else None
            nw=(va.get(6)>>4)&15;ni=(va.get(6)>>8)&15
            self.weights=array(f,va.get(7),width=nw)
            self.indices=array(f,va.get(8),'u1',ni)
        self.blend_select=blend_matrix_select(f,ga)
        pal=f.list_items(self.blend_select.get(10));self.palette=np.array(pal)
        bymat={b.get(4):i for i,b in enumerate(self.bones) if b.get(4)>=0}
        self.bone_indices=np.array([bymat[m] for m in self.palette])[self.indices]
        idx=array(f,f.obj(ga.get(5)).get(2),'<u2')
        prim=ga.get(7) if va.name=='igVertexArray2' else ga.get(6)
        if prim==3:self.tri=idx.reshape(-1,3).astype(int)
        elif prim==4:
            tris=[];start=0
            lengths=array(f,f.obj(ga.get(13)).get(2),'<u4') if ga.get(13)>=0 else [len(idx)]
            for length in lengths:
                strip=idx[start:start+length];start+=length
                for j in range(len(strip)-2):
                    a,b,c=map(int,strip[j:j+3]);a,b=(b,a) if j%2 else (a,b)
                    if len({a,b,c})==3:tris.append([a,b,c])
            self.tri=np.array(tris)
        else:raise ValueError(f'Unsupported primitive {prim}')

    def skin(self,world,positions=None):
        pos=self.pos if positions is None else positions
        matrices=self.inverse_by_bone @ world
        homogeneous=np.column_stack((pos,np.ones(len(pos))))
        out=np.einsum('vi,vkij,vk->vj',homogeneous,matrices[self.bone_indices],self.weights)
        return out[:,:3]

def texture(model):
    f=model.f
    if f.header[11]==0x80000004:
        from community_igb import png8_texture
        return png8_texture(f).convert('RGB')
    images=f.objects_of('igImage')
    diffuse=[im for im in images if re.search(r'_d(?:-\d+)?\.png$',(im.get(22) or '').lower())]
    im=max(diffuse or images,key=lambda im:im.get(2)*im.get(3))
    w,h=im.get(2),im.get(3);data=blob(f,im.get(13));fmt=im.get(11)
    if fmt==7:
        if len(data)!=w*h*4 or im.get(19)!=w*4:raise ValueError('Invalid RGBA8888 image payload or row pitch')
        return Image.frombytes('RGBA',(w,h),data).convert('RGB')
    if fmt not in (14,15):raise ValueError('Unsupported image format '+str(fmt))
    # Pillow's DDS reader decodes the block format. PixelFormat 15 in these files is DXT3.
    fourcc=b'DXT1' if fmt==14 else b'DXT3'
    header=struct.pack('<7I',124,0x81007,h,w,len(data),0,1)+bytes(44)
    header+=struct.pack('<II4s5I',32,4,fourcc,0,0,0,0,0)+struct.pack('<5I',0x1000,0,0,0,0)
    return Image.open(io.BytesIO(b'DDS '+header+data)).convert('RGB')

def quat_matrix(q):
    q=np.asarray(q,dtype=float);q=q/(np.linalg.norm(q) or 1)
    x,y,z,w=q
    # Alchemy uses row vectors.
    return np.array([[1-2*y*y-2*z*z,2*x*y+2*z*w,2*x*z-2*y*w,0],
                     [2*x*y-2*z*w,1-2*x*x-2*z*z,2*y*z+2*x*w,0],
                     [2*x*z+2*y*w,2*y*z-2*x*w,1-2*x*x-2*y*y,0],[0,0,0,1.]])

def pose(model,names,qt,use_translation=None):
    tracks=dict(zip(names,qt));world=np.zeros_like(model.world);done=set()
    dynamic=dict(zip(names,use_translation)) if use_translation is not None else {}
    def visit(i):
        if i in done:return
        name=model.names[i];p=model.parent[i]
        local=model.local[i].copy()
        if name in tracks:
            v=tracks[name];local=quat_matrix(v[:4]).T;local[3,:3]=v[4:7]
            # igAnimationBinding's per-bone translation mask decides which static
            # offsets replace the zero channels in the compressed stream.
            if not dynamic.get(name,False):
                local[3,:3]=model.local_t[i]
        if p>=0:visit(p);world[i]=local@world[p]
        else:world[i]=local
        done.add(i)
    for i in range(len(world)):visit(i)
    return world
