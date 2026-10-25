"""Geometry-preserving bind refit plus constrained four-weight animation fitting.

All animation samples and fitted weights are derived locally from owned assets.
The solver changes skin weights, never bones, inverse binds, timing or motion.
"""
import copy,json,time
from types import SimpleNamespace
from pathlib import Path
import numpy as np

CLIPS=[('idle','x1_03_wolverine.IGB'),('run','x1_03_wolverine.IGB'),
       ('attack_light1','x1_03_wolverine.IGB'),('f_heavy_attack_4','x1_03_wolverine.IGB'),
       ('power_2','x1_03_wolverine.IGB'),('pain_high','common.IGB')]

def fit(source,target,port,work,iterations=450,animation_db='x1_03_wolverine.IGB',regularizer=.005,outline_smoothing=10,references=None,gate_limit=1.25):
    from scipy.sparse import coo_matrix,diags,eye
    from scipy.sparse.linalg import spsolve
    from scipy.spatial import cKDTree
    from scipy.optimize import minimize
    from model import pose
    from convert import refit
    from export_animations import export
    from validate import edges
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    fit_clips=[]
    if references:
        import shutil
        for name,path in references.items():
            db=path.parent.name;dest=work/'animations'/db;dest.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dest/path.name);fit_clips.append((name,db))
    else:
        for db in ['common.IGB',animation_db]:
            manifest=export(Path(port)/'Actors'/db,port,work/'animations'/db,None if db==animation_db else {'pain_high'})
            fit_clips.extend((name,db) for name in manifest)
    aliases={'Bip01':'Bip01 Pelvis','fx01':'Bip01 L Hand','fx02':'Bip01 R Hand'}
    for i,name in enumerate(source.names):
        dest=aliases.get(name,name)
        if dest in target.names and target.bones[target.names.index(dest)].get(4)>=0:continue
        j=int(source.parent[i])
        while j>=0:
            ancestor=aliases.get(source.names[j],source.names[j])
            if ancestor in target.names and target.bones[target.names.index(ancestor)].get(4)>=0:
                aliases[name]=ancestor;break
            j=int(source.parent[j])
    n=len(source.pos);W=np.zeros((n,len(source.names)))
    for v,(idx,w) in enumerate(zip(source.bone_indices,source.weights)):
        for i,a in zip(idx,w):W[v,source.names.index(aliases.get(source.names[i],source.names[i]))]+=a
    e=edges(source);near=np.array(list(cKDTree(source.pos).query_pairs(1e-6)),dtype=int).reshape(-1,2)
    parent=np.arange(n)
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for a,b in near:
        parent[root(b)]=root(a)
    _,group=np.unique([root(i) for i in range(n)],return_inverse=True)
    group_count=int(group.max()+1);counts=np.bincount(group)
    def group_average(a):
        out=np.zeros((group_count,)+a.shape[1:]);np.add.at(out,group,a)
        return out/counts.reshape((-1,)+(1,)*(a.ndim-1))
    graph=np.concatenate([e,near]);dist=np.linalg.norm(source.pos[graph[:,0]]-source.pos[graph[:,1]],axis=1)
    value=1/np.maximum(dist,.05)**2
    adj=coo_matrix((np.r_[value,value],(np.r_[graph[:,0],graph[:,1]],np.r_[graph[:,1],graph[:,0]])),shape=(n,n)).tocsr()
    L=diags(np.asarray(adj.sum(1)).ravel())-adj
    smooth=spsolve(eye(n,format='csc')+10*L.tocsc(),W)
    smooth=group_average(smooth)[group]
    ix=np.argsort(smooth,axis=1)[:,-4:][:,::-1].copy();w=np.maximum(np.take_along_axis(smooth,ix,axis=1),0);w/=w.sum(1)[:,None]
    sm=copy.copy(source);sm.bone_indices=ix;sm.weights=w
    pos,norm,initial,indices,_=refit(sm,target)
    pos=group_average(pos)[group]
    # Smooth only the extrusion directions, preserving the authored body normals.
    # Sharp adjacent normals can nearly collapse an inflated-shell bind edge.
    hd=np.linalg.norm(pos[graph[:,0]]-pos[graph[:,1]],axis=1);hv=1/np.maximum(hd,.05)**2
    ha=coo_matrix((np.r_[hv,hv],(np.r_[graph[:,0],graph[:,1]],np.r_[graph[:,1],graph[:,0]])),shape=(n,n)).tocsr()
    hl=diags(np.asarray(ha.sum(1)).ravel())-ha
    outline_norm=spsolve(eye(n,format='csc')+outline_smoothing*hl.tocsc(),norm)
    outline_norm/=np.maximum(np.linalg.norm(outline_norm,axis=1,keepdims=True),1e-12)
    outline_norm=group_average(outline_norm)[group]
    outline_norm/=np.maximum(np.linalg.norm(outline_norm,axis=1,keepdims=True),1e-12)
    outline_pos=pos+outline_norm*.28
    output_pos,output_norm,output_indices,output_outline=pos,norm,indices,outline_norm
    _,representatives=np.unique(group,return_index=True)
    pos=pos[representatives];outline_pos=outline_pos[representatives]
    initial=initial[representatives];indices=indices[representatives]
    e=np.unique(np.sort(group[e],axis=1),axis=0);e=e[e[:,0]!=e[:,1]]
    n=group_count
    by={b.get(4):i for i,b in enumerate(target.bones) if b.get(4)>=0}
    bone_indices=np.array([by[j] for j in target.palette])[indices]
    hp=np.column_stack((pos,np.ones(n)));ohp=np.column_stack((outline_pos,np.ones(n)))
    trans=[];outline_trans=[];frame_manifest=[]
    for name,db in fit_clips:
        a=np.load(work/'animations'/db/(name+'.npz'))
        for frame,v in enumerate(a['qt']):
            world=pose(target,a['names'],v,a['use_translation'])
            mats=np.array([target.inverse_by_bone[i]@world[i] if b.get(4)>=0 else np.eye(4) for i,b in enumerate(target.bones)])
            trans.append(np.einsum('vi,vkij->vkj',hp,mats[bone_indices])[:,:,:3].copy());frame_manifest.append([name,frame])
            outline_trans.append(np.einsum('vi,vkij->vkj',ohp,mats[bone_indices])[:,:,:3].copy())
    C=np.asarray(trans).transpose(1,0,2,3).copy();nf=C.shape[1];ne=len(e)
    OC=np.asarray(outline_trans).transpose(1,0,2,3).copy()
    del trans,outline_trans
    H=coo_matrix((np.r_[np.ones(ne),-np.ones(ne)],(np.r_[np.arange(ne),np.arange(ne)],np.r_[e[:,0],e[:,1]])),shape=(ne,n)).tocsr()
    length=np.linalg.norm(pos[e[:,0]]-pos[e[:,1]],axis=1)
    outline_length=np.linalg.norm(outline_pos[e[:,0]]-outline_pos[e[:,1]],axis=1)
    if min(length.min(),outline_length.min())<1e-6:raise ValueError('Degenerate fitted edge; cannot certify stretch')
    calls=0;start=time.monotonic();last={}
    def calc(x):
        nonlocal calls,last
        x=x.reshape(n,4);z=np.exp(x-x.max(1,keepdims=True));w=z/z.sum(1,keepdims=True)
        loss=0.;gw=np.zeros_like(w);maxima=[]
        for coords,bind_length in [(C,length),(OC,outline_length)]:
            p=np.einsum('nfki,nk->nfi',coords,w).reshape(n,-1);d=(H@p).reshape(ne,nf,3)
            normd=np.linalg.norm(d,axis=2);ratio=normd/bind_length[:,None];over=np.maximum(ratio-1.22,0)
            gd=d*(2*over/(bind_length[:,None]*np.maximum(normd,1e-12)))[:,:,None]
            gp=(H.T@gd.reshape(ne,-1)).reshape(n,nf,3);gw+=np.einsum('nfki,nfi->nk',coords,gp)
            loss+=(over**2).sum();maxima.append(float(ratio.max()))
        loss+=regularizer*((w-initial)**2).sum();gw+=2*regularizer*(w-initial)
        gx=w*(gw-(gw*w).sum(1,keepdims=True));calls+=1
        last={'calls':calls,'loss':float(loss),'max_ratio':max(maxima),'max_body_ratio':maxima[0],'max_outline_ratio':maxima[1],'seconds':time.monotonic()-start}
        if calls%50==0:print('fit',json.dumps(last),flush=True)
        return loss,gx.ravel()
    start_weights=initial
    if (work/'fit.npz').exists():
        old=np.load(work/'fit.npz')
        # A previous local fit is only a starting point. All frames are evaluated
        # again and the gate still has to pass before any mod is written.
        if np.array_equal(old['indices'],output_indices) and np.allclose(old['pos'],output_pos,atol=1e-8,rtol=0):
            start_weights=group_average(old['weights'])
    initial_x=np.log(np.maximum(start_weights,1e-4)).ravel()
    calc(initial_x)
    if start_weights is not initial and last['max_ratio']<=1.249:
        result=SimpleNamespace(x=initial_x,success=True,message='Existing local fit revalidated over all current frames',nit=0)
    else:
        callback_count=0
        def gate_stop(xk):
            nonlocal callback_count
            callback_count+=1
            if callback_count>=40 and last['max_ratio']<=1.238:
                calc(xk)
                if last['max_ratio']<=1.238:raise StopIteration
        result=minimize(calc,initial_x,jac=True,callback=gate_stop,method='L-BFGS-B',options={'maxiter':iterations,'maxcor':12,'ftol':1e-10,'gtol':1e-6})
    calc(result.x);z=np.exp(result.x.reshape(n,4)-result.x.reshape(n,4).max(1,keepdims=True));w=z/z.sum(1,keepdims=True)
    report={**last,'solver_converged':bool(result.success),'solver_message':str(result.message),'iterations':int(result.nit),'frames':len(frame_manifest),'animations':[n for n,_ in fit_clips],'gate_limit':gate_limit,'gate_pass':last['max_ratio']<=gate_limit if gate_limit is not None else None,'regularizer':regularizer,'outline_smoothing':outline_smoothing,'target_ratio':1.22,'acceptance':'Independent stock-relative per-clip validator required' if gate_limit is None else 'Legacy internal stretch-only limit'}
    (work/'fit.json').write_text(json.dumps(report,indent=2))
    report['shared_vertex_groups']=group_count
    report['uv_seams_share_weights']=True
    fitted=dict(pos=output_pos,norm=output_norm,weights=w[group],indices=output_indices,outline_norm=output_outline)
    np.savez(work/'fit.npz',**fitted)
    if gate_limit is not None and not report['gate_pass']:raise ValueError(f'Fit failed stretch gate: {last["max_ratio"]:.6f} > {gate_limit}; diagnostic data saved')
    return fitted,report
