"""v6 mesh-only transplant into the port's original object graph and skeleton."""
import argparse,json,struct,copy
from pathlib import Path
import numpy as np
from model import Model
from igb_file import IgbFile
from round1_converter import blob,put_int
from convert import refit
from upscale import rewrite,decode

def outline_model(path):
    f=IgbFile(Path(path).read_bytes());choices=[]
    for ga in f.objects_of('igGeometryAttr'):
        va=f.obj(ga.get(4))
        if va.name=='igVertexArray2':continue
        indirect=struct.unpack('<20i',blob(f,va.get(2)))
        if indirect[1]<0:choices.append((va.get(3),ga.ref))
    return Model(path,'@'+str(max(choices)[1])) if choices else None

def weld(pos,weights,indices,norm,source_pos):
    from scipy.spatial import cKDTree
    parent=np.arange(len(pos))
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for a,b in cKDTree(source_pos).query_pairs(1e-6):parent[root(b)]=root(a)
    _,group=np.unique([root(i) for i in range(len(pos))],return_inverse=True)
    out=[pos.copy(),weights.copy(),indices.copy(),norm.copy()]
    for g in range(int(group.max())+1):
        ix=np.flatnonzero(group==g)
        if len(ix)==1:continue
        out[0][ix]=pos[ix].mean(0);out[3][ix]=norm[ix].mean(0)
        pairs={}
        for i in ix:
            for b,w in zip(indices[i],weights[i]):pairs[int(b)]=pairs.get(int(b),0)+float(w)/len(ix)
        p=sorted(pairs.items(),key=lambda a:-a[1])[:4];total=sum(w for b,w in p)
        wi=np.zeros(4);ii=np.full(4,p[0][0],dtype='u1')
        for k,(b,w) in enumerate(p):wi[k]=w/total;ii[k]=b
        out[1][ix]=wi;out[2][ix]=ii
    out[3]/=np.maximum(np.linalg.norm(out[3],axis=1,keepdims=True),1e-12)
    return out,group

def convert(source_path,target_path,dest,fit=None):
    if Path(dest).resolve() in {Path(source_path).resolve(),Path(target_path).resolve()}:raise ValueError('Output must not overwrite a source actor')
    source,target=Model(source_path),Model(target_path);f=target.f
    if source.f.header[11]!=0xf0000006 or f.header[11]!=0xf0000006:raise ValueError('Expected two v6 files')
    if fit is None:
        p,n,w,i,aliases=refit(source,target);(p,w,i,n),groups=weld(p,w,i,n,source.pos)
        # Average the normals at every seam so the hull stays closed too.
        hn=n.copy()
        source_hull=outline_model(source_path)
        if source_hull is not None:
            source_hull.norm=np.zeros_like(source_hull.pos)
            hp,hno,hw,hi,_=refit(source_hull,target)
            (hp,hw,hi,hno),_=weld(hp,hw,hi,hno,source_hull.pos)
            authored_hull=(hp,hw,hi,source_hull.tri)
        else:authored_hull=None
    else:p,n,w,i=[fit[k] for k in ['pos','norm','weights','indices']];hn=fit.get('outline_norm',n);aliases={};groups=None;authored_hull=None
    hull=outline_model(target_path)
    if hull is None:raise ValueError('Port template has no outline schema')
    blocks={};body_ga=target.ga.ref;hull_ga=hull.ga.ref
    for ga in f.objects_of('igGeometryAttr'):
        va=f.obj(ga.get(4));ia=f.obj(ga.get(5))
        if ga.ref not in [body_ga,hull_ga]:
            # Additional old costume mesh (e.g. Cyclops visor) is superseded by
            # the donor's complete body. Keep graph refs, disable its draw.
            blocks[ia.get(2)]=b'';put_int(f,ia,3,0);put_int(f,ga,6,3);put_int(f,ga,7,0);continue
        is_hull=ga.ref==hull_ga;ind=struct.unpack('<20i',blob(f,va.get(2)))
        vp,vw,vi,vt=(authored_hull if is_hull and authored_hull is not None else (p+hn*.28 if is_hull else p,w,i,source.tri[:,::-1] if is_hull else source.tri))
        put_int(f,va,3,len(vp));put_int(f,va,6,0x441 if is_hull else 0x10443)
        blocks[ind[0]]=np.asarray(vp,dtype='<f4').tobytes()
        if not is_hull:blocks[ind[1]]=np.asarray(n,dtype='<f4').tobytes();blocks[ind[11]]=np.asarray(source.uv,dtype='<f4').tobytes()
        blocks[va.get(7)]=np.asarray(vw,dtype='<f4').tobytes();blocks[va.get(8)]=np.asarray(vi,dtype='u1').tobytes()
        tri=vt
        blocks[ia.get(2)]=tri.astype('<u2').tobytes();put_int(f,ia,3,len(tri)*3);put_int(f,ga,6,3);put_int(f,ga,7,len(tri));put_int(f,ga,13,-1)
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(f.rebuilt(blocks))
    sim=max(source.f.objects_of('igImage'),key=lambda o:o.get(2)*o.get(3));tex=decode(source.f,sim)
    info=rewrite(dest,dest,lambda image:tex)
    return dict(source=str(source_path),target=str(target_path),vertices=len(p),triangles=len(source.tri),aliases=aliases,texture=info,source_seam_groups=int(groups.max()+1) if groups is not None else None)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('target',type=Path);ap.add_argument('output',type=Path);ap.add_argument('--fit',type=Path);a=ap.parse_args()
    print(json.dumps(convert(a.source,a.target,a.output,np.load(a.fit) if a.fit else None),indent=2))
