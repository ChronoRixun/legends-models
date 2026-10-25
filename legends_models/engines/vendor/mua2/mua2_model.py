"""IGZ skin adapter for the round-2 fitter; preserve port skeleton and fold extras."""
from pathlib import Path
import numpy as np
from igz import IGZ,require

class Bone:
    def __init__(self,index,parent):self.index=index;self.parent=parent
    def get(self,field):return self.index if field==4 else self.parent if field==3 else None

class Source:
    def __init__(self,path,target=None):
        self.path=Path(path);self.igz=IGZ.read(path);sk=self.igz.skeleton();self.meshes=self.igz.meshes(sk)
        self.removed_claw_triangles=0
        if target is not None:
            claw_bones=np.array([' Claw ' in n for n in sk['names']])
            for m in self.meshes:
                claw_vertex=(claw_bones[m['bone_indices']]*m['weights']).sum(1)>1e-5
                affected=claw_vertex[m['tri']]
                require(np.array_equal(affected.any(1),affected.all(1)), 'Claws share triangles with body; manual split required')
                remove=affected.all(1)
                if remove.any():
                    self.removed_claw_triangles+=int(remove.sum());m['tri']=m['tri'][~remove]
                    used=np.unique(m['tri']);remap=np.full(len(m['pos']),-1);remap[used]=np.arange(len(used));m['tri']=remap[m['tri']]
                    for k in ('pos','norm','uv','weights','indices','bone_indices'):m[k]=m[k][used]
                    m['metadata']['count']=len(used);m['metadata']['triangles']=len(m['tri'])
        self.names=sk['names'];self.parent=sk['parent'];self.world=sk['world'];self.inverse_by_bone=sk['inverse'];self.local_t=sk['local_t']
        for key in ('pos','norm','uv','weights','bone_indices'):
            setattr(self,key,np.concatenate([m[key] for m in self.meshes]))
        self.coat_mask=(np.array([n.startswith('Coat_') for n in self.names])[self.bone_indices]*self.weights).sum(1)>1e-5
        self.tri=np.concatenate([m['tri'].astype(int)+sum(len(x['pos']) for x in self.meshes[:i]) for i,m in enumerate(self.meshes)])
        self.decisions={}
        if target is not None:
            # A matched ancestor supplies the rest frame as well as the target
            # weight. Moving a facial vertex to its ancestor's origin is wrong.
            valid={n for n,b in zip(target.names,target.bones) if b.get(4)>=0}
            mapped=[]
            for i,name in enumerate(self.names):
                j=i
                if name=='Bip01':j=self.names.index('Bip01 Pelvis')
                while self.names[j] not in valid and self.parent[j]>=0:
                    j=int(self.parent[j])
                    if self.names[j]=='Bip01':j=self.names.index('Bip01 Pelvis')
                if self.names[j] not in valid:
                    require(not np.any((self.bone_indices==i)&(self.weights>0)), 'Weighted bone has no target ancestor: '+name)
                    j=self.names.index('Bip01 Pelvis')
                mapped.append(j)
                if j!=i:self.decisions[name]=self.names[j]
            keep=sorted(set(mapped));remap={j:k for k,j in enumerate(keep)}
            self.bone_indices=np.array([remap[mapped[i]] for i in range(len(mapped))])[self.bone_indices]
            parent=[]
            for j in keep:
                p=int(sk['parent'][j]);seen=set()
                while p>=0 and p not in remap:
                    require(p not in seen,'Ancestor cycle');seen.add(p);p=int(sk['parent'][p])
                parent.append(remap[p] if p>=0 else -1)
            self.names=[self.names[i] for i in keep];self.parent=np.array(parent)
            self.world=self.world[keep];self.inverse_by_bone=self.inverse_by_bone[keep];self.local_t=self.local_t[keep]
            for row in range(len(self.pos)):
                pairs={}
                for i,w in zip(self.bone_indices[row],self.weights[row]):
                    if w>0:pairs[int(i)]=pairs.get(int(i),0)+float(w)
                pairs=sorted(pairs.items(),key=lambda t:-t[1]);self.weights[row]=0;self.bone_indices[row]=pairs[0][0]
                for k,(i,w) in enumerate(pairs):self.weights[row,k]=w;self.bone_indices[row,k]=i
        self.bones=[Bone(i,int(p)) for i,p in enumerate(self.parent)]
        self.palette=np.arange(len(self.names));self.indices=self.bone_indices.astype('u1')
        self.local=np.array([w@np.linalg.inv(self.world[p]) if p>=0 else w for w,p in zip(self.world,self.parent)])

    def skin(self,world,positions=None):
        pos=self.pos if positions is None else positions
        mats=self.inverse_by_bone@world
        return np.einsum('vi,vkij,vk->vj',np.column_stack((pos,np.ones(len(pos)))),mats[self.bone_indices],self.weights)[:,:3]
