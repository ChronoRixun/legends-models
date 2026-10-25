"""Read the existing animated claw bolt-ons; display their fully extended pose."""
import copy,struct
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image
from igb_file import IgbFile
from model import array
from round1_converter import blob,geometry_attribute

@lru_cache(maxsize=8)
def read_claw(path,allow_multiple=False):
    f=IgbFile(Path(path).read_bytes())
    choices=[]
    for g in f.objects_of('igGeometry'):
        ga=geometry_attribute(f,g);va=f.obj(ga.get(4))
        if va.name=='igVertexArray2':
            streams={f.obj(r).get(4):f.obj(f.obj(r).get(3)) for r in f.list_items(va.get(3))}
            if 3 not in streams:continue
            pos=array(f,streams[1].get(4),width=3);primitive=ga.get(7)
        else:
            indirect=struct.unpack('<20i',blob(f,va.get(2)))
            if indirect[1]<0:continue
            pos=array(f,indirect[0],width=3);primitive=ga.get(6)
        idx=array(f,f.obj(ga.get(5)).get(2),'<u2')
        if primitive==3:tri=idx.reshape(-1,3)
        else:
            lengths=array(f,f.obj(ga.get(13)).get(2),'<u4');tris=[];start=0
            for length in lengths:
                strip=idx[start:start+length];start+=length
                for i in range(len(strip)-2):
                    a,b,c=map(int,strip[i:i+3]);a,b=(b,a) if i%2 else (a,b)
                    if len({a,b,c})==3:tris.append([a,b,c])
            tri=np.array(tris)
        # Find the graph path, then evaluate every ancestor's final transform key.
        parents={}
        for o in f.objects:
            if f.isa(o,'igGroup'):
                for child in f.list_items(o.get(7)) or []:parents[child]=o
        node=g
        while node.ref in parents:
            node=parents[node.ref]
            if node.name=='igTransform':
                matrix=np.array(node.get(8)).reshape(4,4)
                seq=f.obj(node.get(10) if f.header[11]==0xf0000008 else node.get(11))
                if seq and seq.name=='igTransformSequence1_5':
                    from model import quat_matrix
                    q=array(f,f.obj(seq.get(3)).get(4),width=4)[-1] if seq.get(3)>=0 else [0,0,0,1]
                    matrix=quat_matrix(q).T
                    if seq.get(4)>=0:matrix[:3,:3]=np.diag(array(f,f.obj(seq.get(4)).get(4),width=3)[-1])@matrix[:3,:3]
                    if seq.get(2)>=0:matrix[3,:3]=array(f,f.obj(seq.get(2)).get(4),width=3)[-1]
                pos=(np.column_stack((pos,np.ones(len(pos))))@matrix)[:,:3]
        choices.append((pos,tri))
    if allow_multiple:
        offset=0;positions=[];triangles=[]
        for p,t in choices:
            positions.append(p);triangles.append(t+offset);offset+=len(p)
        return np.concatenate(positions),np.concatenate(triangles)
    if len(choices)!=1:raise ValueError(f'Expected one claw body in {path}, got {len(choices)}')
    return choices[0]

def attach(model,pos,world,tex,folder):
    composite=copy.copy(model);positions=[pos];tris=[model.tri];uv=[model.uv.copy()]
    h=tex.height;atlas=Image.new('RGB',(tex.width,h+16),(180,190,204));atlas.paste(tex,(0,0));uv[0][:,1]*=h/(h+16)
    offset=len(pos)
    for side in ['left','right']:
        cp,ct=read_claw(Path(folder)/('claw_'+side+'.igb'))
        hand=model.names.index('Bip01 '+('L' if side=='left' else 'R')+' Hand')
        positions.append((np.column_stack((cp,np.ones(len(cp))))@world[hand])[:,:3]);tris.append(ct+offset)
        uv.append(np.tile([.5,(h+8)/(h+16)],(len(cp),1)));offset+=len(cp)
    composite.tri=np.concatenate(tris);composite.uv=np.concatenate(uv)
    return composite,np.concatenate(positions),atlas
