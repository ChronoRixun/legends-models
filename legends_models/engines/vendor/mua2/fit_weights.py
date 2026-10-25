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

def fit(source,target,port,work,iterations=450,animation_db='x1_03_wolverine.IGB',regularizer=.005,outline_smoothing=10,clips=None,samples=None,diagnostic=False,coat_transfer=False,stock_relative=False,couple_layers=True):
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
    for db in dict.fromkeys(db for _,db in clips) if clips else ['common.IGB',animation_db]:
        only={name for name,d in clips if d==db} if clips else (None if db==animation_db else {'pain_high'})
        manifest=export(Path(port)/'Actors'/db,port,work/'animations'/db,only)
        if only and not only.issubset(manifest):raise ValueError('Missing requested animations: '+str(only-set(manifest)))
        fit_clips.extend((name,db) for name in manifest)
    aliases={'Bip01':'Bip01 Pelvis','fx01':'Bip01 L Hand','fx02':'Bip01 R Hand'}
    n=len(source.pos);W=np.zeros((n,len(source.names)))
    for v,(idx,w) in enumerate(zip(source.bone_indices,source.weights)):
        for i,a in zip(idx,w):W[v,source.names.index(aliases.get(source.names[i],source.names[i]))]+=a
    e=edges(source);near=np.array(list(cKDTree(source.pos).query_pairs(1e-4)),dtype=int).reshape(-1,2)
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
    # Layered cloth (layers.py): every inner-shell vertex is bound with its outer shell's blend, so
    # the bind refit carries the authored inset instead of moving the two shells independently.
    from layers import source_layers,find_layers,coupling_plan,blend_dense,tie_lanes,WeightTie,skeleton_segments
    layers=source_layers(source) if couple_layers else None
    if layers is not None:smooth=blend_dense(smooth,coupling_plan(layers,None,n))
    smooth=group_average(smooth)[group]
    ix=np.argsort(smooth,axis=1)[:,-4:][:,::-1].copy();w=np.maximum(np.take_along_axis(smooth,ix,axis=1),0);w/=w.sum(1)[:,None]
    sm=copy.copy(source);sm.bone_indices=ix;sm.weights=w
    pos,norm,initial,indices,_=refit(sm,target)
    pos=group_average(pos)[group]
    if layers is not None:
        # Exact bind inset: the refit can move an inner shell through its outer shell where the two
        # skeletons differ most (neck/shoulders); place it from the outer triangle's frame instead.
        from layers import place_inner_shells
        pos,_placed=place_inner_shells(pos,np.asarray(source.pos,float),layers,n);pos=group_average(pos)[group]
    if coat_transfer and np.any(source.coat_mask):
        # The source's dynamic coat rig has no port equivalent. Retain the
        # ancestor-refitted bind, but initialize its skinning from the stock
        # coat/leg surface so it can follow the port's leg animation naturally.
        # This is a substantive weight transfer, never epsilon gate influences.
        distances,neighbors=cKDTree(target.pos).query(pos,k=4)
        tw=np.zeros((len(target.pos),len(target.palette)))
        for k in range(target.weights.shape[1]):np.add.at(tw,(np.arange(len(target.pos)),target.indices[:,k]),target.weights[:,k])
        inv=1/np.maximum(distances,.05)**2;inv/=inv.sum(1,keepdims=True)
        transferred=np.einsum('nk,nkj->nj',inv,tw[neighbors])
        old=np.zeros((len(pos),len(target.palette)))
        for k in range(4):np.add.at(old,(np.arange(len(pos)),indices[:,k]),initial[:,k])
        region=group_average(source.coat_mask.astype(float))>0
        old[source.coat_mask]=transferred[source.coat_mask]
        transferred=spsolve(eye(len(pos),format='csc')+10*L.tocsc(),old)
        averaged=group_average(old);averaged[region]=group_average(transferred)[region]
        gi=np.argsort(averaged,axis=1)[:,-4:][:,::-1].copy();gw=np.take_along_axis(averaged,gi,axis=1);gw/=gw.sum(1,keepdims=True)
        indices=gi[group].astype('u1');initial=gw[group]
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
    # ... and while fitting, each inner row's weights are tied to its outer triangle's (WeightTie):
    # both shells always deform by the same blend, so the inset can rotate/scale but never invert.
    plan=coupling_plan(layers,group,group_count) if layers is not None else coupling_plan(dict(vertex=[],bary=np.zeros((0,3))),None,0)
    indices,initial=tie_lanes(plan,initial,indices,len(target.palette))
    indices=indices.astype('u1');tie=WeightTie(plan,indices,len(target.palette))
    output_indices=indices[group]
    by={b.get(4):i for i,b in enumerate(target.bones) if b.get(4)>=0}
    bone_indices=np.array([by[j] for j in target.palette])[indices]
    hp=np.column_stack((pos,np.ones(n)));ohp=np.column_stack((outline_pos,np.ones(n)))
    trans=[];outline_trans=[];frame_manifest=[];world_frames=[];clip_limits={}
    if stock_relative:
        from model import Model
        from validate import chain_distance
        hull_name=next(g.get(2) for g in target.f.objects_of('igGeometry') if 'outline' in (g.get(2) or ''))
        target_hull=Model(target.path,hull_name)
        stock_edges=[edges(m) for m in (target,target_hull)]
        stock_lengths=[np.linalg.norm(m.pos[e[:,0]]-m.pos[e[:,1]],axis=1) for m,e in zip((target,target_hull),stock_edges)]
    for name,db in fit_clips:
        a=np.load(work/'animations'/db/(name+'.npz'))
        if stock_relative:
            limits=[dict(edge=0.,chain=0.) for _ in range(2)]
            for v in a['qt']:
                world=pose(target,a['names'],v,a['use_translation'])
                for ii,m in enumerate((target,target_hull)):
                    pp=m.skin(world);ee=stock_edges[ii];ll=stock_lengths[ii];valid=ll>1e-6
                    limits[ii]['edge']=max(limits[ii]['edge'],float((np.linalg.norm(pp[ee[:,0]]-pp[ee[:,1]],axis=1)[valid]/ll[valid]).max()))
                    limits[ii]['chain']=max(limits[ii]['chain'],float(chain_distance(m,pp,world).max()))
            clip_limits[name]=limits
        frame_ids=np.unique(np.linspace(0,len(a['qt'])-1,min(samples,len(a['qt']))).astype(int)) if samples else range(len(a['qt']))
        for frame in frame_ids:
            v=a['qt'][frame]
            world=pose(target,a['names'],v,a['use_translation'])
            world_frames.append(world)
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
    worlds=np.array(world_frames)
    # Candidate influence masks are evaluated at the same >1e-5 threshold as
    # validate.py. The distance gradient moves skin weights, not the skeleton.
    segments={i:([(i,int(target.parent[i]))] if target.parent[i]>=0 else [])+[(i,int(j)) for j in np.where(target.parent==i)[0]] for i in range(len(target.names))}
    def chain_gradient(points,weights,limit):
        best=np.full((n,nf),np.inf);nearest=np.zeros_like(points)
        for bone in range(len(target.names)):
            mask=np.any((bone_indices==bone)&(weights>1e-5),axis=1);rows=np.flatnonzero(mask)
            if not len(rows):continue
            pp=points[rows]
            for aa,bb in segments[bone]:
                aa,bb=worlds[:,aa,3,:3],worlds[:,bb,3,:3];vv=bb-aa;den=np.maximum(np.einsum('fi,fi->f',vv,vv),1e-15)
                u=np.clip(np.einsum('nfi,fi->nf',pp-aa,vv)/den,0,1);close=aa+u[:,:,None]*vv
                dd=np.linalg.norm(pp-close,axis=2);better=dd<best[rows]
                best[rows]=np.minimum(best[rows],dd)
                tmp=nearest[rows];tmp[better]=close[better];nearest[rows]=tmp
        over=np.maximum(best-limit[None,:],0)
        gp=(points-nearest)*(2*over/np.maximum(best,1e-12))[:,:,None]
        return float((over**2).sum()),gp,float(best.max())
    calls=0;start=time.monotonic();last={}
    def calc(x):
        nonlocal calls,last
        x=x.reshape(n,4);z=np.exp(x-x.max(1,keepdims=True));w_free=z/z.sum(1,keepdims=True);w=tie.forward(w_free)
        loss=0.;gw=np.zeros_like(w);maxima=[];chain_max=[];relative_max=[]
        for ii,(coords,bind_length) in enumerate([(C,length),(OC,outline_length)]):
            p=np.einsum('nfki,nk->nfi',coords,w).reshape(n,-1);d=(H@p).reshape(ne,nf,3)
            normd=np.linalg.norm(d,axis=2);ratio=normd/bind_length[:,None]
            edge_limit=np.array([clip_limits[name][ii]['edge']*.97 for name,_ in frame_manifest]) if stock_relative else 1.22
            over=np.maximum(ratio-edge_limit,0)
            gd=d*(2*over/(bind_length[:,None]*np.maximum(normd,1e-12)))[:,:,None]
            gp=(H.T@gd.reshape(ne,-1)).reshape(n,nf,3);gw+=np.einsum('nfki,nfi->nk',coords,gp)
            loss+=(over**2).sum();maxima.append(float(ratio.max()))
            if stock_relative:
                relative_max.append(float((ratio/(edge_limit[None,:]/.97)).max()))
                chain_limit=np.array([clip_limits[name][ii]['chain']*.97 for name,_ in frame_manifest])
                cl,cg,cm=chain_gradient(p.reshape(n,nf,3),w,chain_limit)
                loss+=cl;gw+=np.einsum('nfki,nfi->nk',coords,cg);chain_max.append(cm)
        loss+=regularizer*((w-initial)**2).sum();gw+=2*regularizer*(w-initial)
        gw=tie.backward(gw);gx=w_free*(gw-(gw*w_free).sum(1,keepdims=True));calls+=1
        last={'calls':calls,'loss':float(loss),'max_ratio':max(maxima),'max_body_ratio':maxima[0],'max_outline_ratio':maxima[1],'seconds':time.monotonic()-start,
              'max_chain_distance':max(chain_max) if chain_max else None,'max_edge_relative_to_stock':max(relative_max) if relative_max else None}
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
            if not stock_relative and callback_count>=40 and last['max_ratio']<=1.238:
                calc(xk)
                if last['max_ratio']<=1.238:raise StopIteration
        result=minimize(calc,initial_x,jac=True,callback=gate_stop,method='L-BFGS-B',options={'maxiter':iterations,'maxcor':12,'ftol':1e-10,'gtol':1e-6})
    calc(result.x);z=np.exp(result.x.reshape(n,4)-result.x.reshape(n,4).max(1,keepdims=True));w=tie.forward(z/z.sum(1,keepdims=True))
    report={**last,'solver_converged':bool(result.success),'solver_message':str(result.message),'iterations':int(result.nit),'frames':len(frame_manifest),'animations':[n for n,_ in fit_clips],'gate_limit':1.25 if not stock_relative else 'per-clip stock; full-frame independent validation required','gate_pass':last['max_ratio']<=1.25 if not stock_relative else False,'regularizer':regularizer,'outline_smoothing':outline_smoothing,'target_ratio':1.22 if not stock_relative else '0.97 * per-clip stock','coat_surface_weight_transfer':coat_transfer,'stock_relative_objective':stock_relative,'stock_clip_limits':clip_limits}
    (work/'fit.json').write_text(json.dumps(report,indent=2))
    report['shared_vertex_groups']=group_count
    report['uv_seams_share_weights']=True
    report['layer_coupling']={'pairs':0 if layers is None else int(len(layers['vertex'])),'tied_rows':int(len(plan['rows'])),
                              'levels':len(plan['levels']),'rule':'inner shell bound with and tied to its outer triangle blend (layers.py)'}
    fitted=dict(pos=output_pos,norm=output_norm,weights=w[group],indices=output_indices,outline_norm=output_outline)
    np.savez(work/'fit.npz',**fitted)
    if not report['gate_pass'] and not diagnostic:raise ValueError(f'Fit failed stretch gate: {last["max_ratio"]:.6f} > 1.25; diagnostic data saved')
    return fitted,report
