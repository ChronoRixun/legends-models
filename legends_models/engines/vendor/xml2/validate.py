"""Original round-2 edge and bone-chain metrics; math unchanged.
Roster validation is in validate_roster.py."""
import numpy as np

def edges(model):
    t=model.tri;return np.unique(np.sort(np.concatenate([t[:,[0,1]],t[:,[1,2]],t[:,[2,0]]]),axis=1),axis=0)

def chain_distance(model,points,world):
    """Shortest distance to the segments incident on ANY positively weighted bone.

    The chain is restricted to the influencing bones plus their parent/children;
    it is not an unrestricted distance to any convenient bone in the body.
    """
    best=np.full(len(points),np.inf)
    for i in range(len(model.names)):
        mask=np.any((model.bone_indices==i)&(model.weights>1e-5),axis=1)
        if not mask.any():continue
        segs=[(i,int(model.parent[i]))] if model.parent[i]>=0 else []
        segs += [(i,j) for j in np.where(model.parent==i)[0]]
        for a,b in segs:
            a,b=world[a,3,:3],world[b,3,:3];v=b-a;den=v@v
            u=np.clip((points[mask]-a)@v/max(den,1e-15),0,1)
            dist=np.linalg.norm(points[mask]-(a+u[:,None]*v),axis=1)
            best[mask]=np.minimum(best[mask],dist)
    return best
