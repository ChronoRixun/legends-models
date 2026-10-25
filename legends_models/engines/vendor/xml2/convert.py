"""Round-2 bind transfer adapted for v6 nonblending helper ancestors."""
import numpy as np

def refit(source,target):
    aliases={'Bip01':'Bip01 Pelvis','fx01':'Bip01 L Hand','fx02':'Bip01 R Hand'}
    # v6 donors can weight head/cape helpers for which the port retained only
    # nonblending bone records. Transfer that influence to its shared weighted
    # ancestor; never add an output bone or change attachment transforms.
    for i,name in enumerate(source.names):
        dest=aliases.get(name,name)
        if dest in target.names and target.bones[target.names.index(dest)].get(4)>=0:continue
        j=int(source.parent[i])
        while j>=0:
            ancestor=aliases.get(source.names[j],source.names[j])
            if ancestor in target.names and target.bones[target.names.index(ancestor)].get(4)>=0:
                aliases[name]=ancestor;break
            j=int(source.parent[j])
    mapped=[];transforms=[];decisions={}
    fallback_bone=next(i for i,b in enumerate(target.bones) if b.get(4)>=0)
    for i,b in enumerate(source.bones):
        name=source.names[i];dest=aliases.get(name,name)
        if dest not in target.names or target.bones[target.names.index(dest)].get(4)<0:
            mapped.append(fallback_bone);transforms.append(np.eye(4));continue
        j=target.names.index(dest);mapped.append(j)
        # Missing source bones bind through their shared ancestor, preserving
        # the mesh's existing anatomical position instead of reorienting it.
        src=source.names.index(dest) if name in aliases else i
        transforms.append(np.linalg.inv(source.world[src])@target.world[j])
        if name in aliases:decisions[name]=dest
    active=set(source.bone_indices[source.weights>1e-7].tolist())
    for i in active:
        dest=aliases.get(source.names[i],source.names[i])
        if dest not in target.names or target.bones[target.names.index(dest)].get(4)<0:
            raise ValueError(f'Unsupported weighted bone: {source.names[i]}')
    matrices=np.asarray(transforms)[source.bone_indices]
    hp=np.column_stack((source.pos,np.ones(len(source.pos))))
    pos=np.einsum('vi,vkij,vk->vj',hp,matrices,source.weights)[:,:3]
    normals=np.einsum('vi,vkij,vk->vj',source.norm,matrices[:,:,:3,:3],source.weights)
    normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
    bm2pal={m:i for i,m in enumerate(target.palette)}
    indices=np.array([[bm2pal[target.bones[mapped[int(i)]].get(4)] for i in row] for row in source.bone_indices],dtype='u1')
    # Coalesce influences that now share a parent. Fill zero-weight lanes with
    # the first active palette index, never an unused/out-of-range slot.
    weights=source.weights.copy()
    for v in range(len(pos)):
        merged={}
        for ix,w in zip(indices[v],weights[v]):
            if w>0:merged[int(ix)]=merged.get(int(ix),0)+float(w)
        pairs=sorted(merged.items(),key=lambda x:-x[1]);weights[v]=0;indices[v]=pairs[0][0]
        for k,(ix,w) in enumerate(pairs):indices[v,k]=ix;weights[v,k]=w
    return pos,normals,weights,indices,decisions
