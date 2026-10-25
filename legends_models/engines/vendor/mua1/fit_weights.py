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

def fit(source,target,port,work,iterations=450,animation_db='x1_03_wolverine.IGB',regularizer=.005,outline_smoothing=10,align_soles=False,couple_layers=True,loose_geometry=True):
    from scipy.sparse import coo_matrix,diags,eye
    from scipy.sparse.linalg import spsolve
    from scipy.spatial import cKDTree
    from scipy.optimize import minimize
    from model import Model,pose
    from convert import refit
    from reference_clips import prepare
    from validate import edges,REFERENCE_CLIPS,chain_distance
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    fit_clips=prepare(port,animation_db,work/'animations')
    stock_hull=Model(target.path,target.path.stem+'_outline')
    stock_limits={};chain_limits={}
    for name,db in fit_clips:
        a=np.load(work/'animations'/db/(name+'.npz'));limits=[];chains=[]
        for model in [target,stock_hull]:
            ed=edges(model);ln=np.linalg.norm(model.pos[ed[:,0]]-model.pos[ed[:,1]],axis=1);valid=ln>1e-6;worst=0;chain=0
            for v in a['qt']:
                world=pose(model,a['names'],v,a['use_translation']);p=model.skin(world)
                worst=max(worst,float((np.linalg.norm(p[ed[:,0]]-p[ed[:,1]],axis=1)[valid]/ln[valid]).max()))
                if loose_geometry:chain=max(chain,float(chain_distance(model,p,world).max()))
            limits.append(worst);chains.append(chain)
        stock_limits[name]=limits;chain_limits[name]=chains
    aliases={'Bip01':'Bip01 Pelvis','fx01':'Bip01 L Hand','fx02':'Bip01 R Hand'}
    n=len(source.pos);W=np.zeros((n,len(source.names)))
    for v,(idx,w) in enumerate(zip(source.bone_indices,source.weights)):
        for i,a in zip(idx,w):W[v,source.names.index(aliases.get(source.names[i],source.names[i]))]+=a
    e=edges(source);near=np.array(list(cKDTree(source.pos).query_pairs(1e-4)),dtype=int).reshape(-1,2)
    parent=np.arange(n)
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    # Authored UV splits can have slightly different source weights (Colossus).
    # Their coincident surface must still share a single position and influence
    # vector after fitting. Preserve continuity regardless of that discrepancy.
    for a,b in near:parent[root(b)]=root(a)
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
    sole_report=[]
    if align_soles:
        bymat={b.get(4):i for i,b in enumerate(target.bones) if b.get(4)>=0}
        fitted_bones=np.array([bymat[j] for j in target.palette])[indices]
        for side in ['L','R']:
            bones=[i for i,name in enumerate(target.names) if name.startswith('Bip01 '+side+' ') and (' Foot' in name or ' Toe' in name)]
            mask=(initial*np.isin(fitted_bones,bones)).sum(1)>.5
            stock_mask=np.any(np.isin(target.bone_indices,bones)&(target.weights>0),axis=1)
            low=float(pos[mask,2].min());desired=float(target.pos[stock_mask,2].min());delta=desired-low
            ankle=float(target.world[target.names.index('Bip01 '+side+' Foot'),3,2])
            if abs(delta)>.02*np.ptp(target.pos[:,2]):raise ValueError('Sole alignment would require more than 2% of stock height')
            taper=np.clip((ankle-pos[:,2])/max(ankle-low,1e-6),0,1)
            pos[mask,2]+=delta*taper[mask]
            changed=mask & (taper>0);scale=1-delta/max(ankle-low,1e-6)
            norm[changed,2]/=scale;norm[changed]/=np.linalg.norm(norm[changed],axis=1,keepdims=True)
            sole_report.append({'side':side,'source_fitted_sole_z':low,'stock_sole_z':desired,'delta':delta,'ankle_z':ankle,'vertices':int(changed.sum())})
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
    loose_report=None
    if loose_geometry:
        # Long hair / loose geometry (layers.py): a strand hanging past its leaf bone (Jean's hair
        # below Ponytail11) is further from its own anchor chain than any stock vertex is from its
        # chain. Blend it, with a smooth falloff along the strand, into the body bone it rests on
        # (nearest non-anchor bone; hands/feet excluded), then let the chain objective below settle
        # the blend. The stock-relative stretch and chain rules are unchanged.
        from layers import loose_vertices
        limit=.97*min(c[0] for c in chain_limits.values());start_falloff=.75*limit
        skin_bones=np.array([by[j] for j in target.palette])[indices]
        pal_of={i:k for k,i in enumerate(np.array([by[j] for j in target.palette]))}
        blocked=[i for i,b in enumerate(target.bones) if b.get(4)<0 or i not in pal_of]
        rows,rest,dist=loose_vertices(pos,skin_bones,initial,target.names,target.parent,target.world,start_falloff,
                                      exclude=(' Hand',' Finger',' Foot',' Toe'),anchor_weight=.1,blocked=blocked)
        far=rows[dist>limit]
        if len(far):
            top=float(dist.max());alpha=np.clip((dist-start_falloff)/max(top-start_falloff,1e-6),0,1);alpha=.6*alpha*alpha*(3-2*alpha)
            P=len(target.palette);dense=np.zeros((n,P))
            for k in range(4):np.add.at(dense,(np.arange(n),indices[:,k]),initial[:,k])
            for r,b,al in zip(rows,rest,alpha):
                if b<0:continue
                dense[r]*=1-al;dense[r,pal_of[b]]+=al
            # Keep each touched row's lanes = its four largest blended influences.
            touched=rows[rest>=0]
            top4=np.argsort(dense[touched],axis=1)[:,-4:][:,::-1]
            indices=indices.copy();initial=initial.copy()
            indices[touched]=top4;ww=np.take_along_axis(dense[touched],top4,axis=1);initial[touched]=ww/ww.sum(1,keepdims=True)
            indices,initial=tie_lanes(plan,initial,indices,len(target.palette))   # inner shells follow the new blend too
            indices=indices.astype('u1');tie=WeightTie(plan,indices,len(target.palette));output_indices=indices[group]
        loose_report={'limit':limit,'falloff_start':start_falloff,'vertices_in_falloff':int(len(rows)),'vertices_over_limit':int(len(far)),
                      'max_anchor_distance':float(dist.max()) if len(dist) else 0.,'rest_bones':sorted({target.names[b] for b in rest if b>=0}),
                      'rule':'strand past its anchor chain blends into the nearest non-anchor body bone (smoothstep falloff, max 0.6); chain objective settles it'}
    bone_indices=np.array([by[j] for j in target.palette])[indices]
    hp=np.column_stack((pos,np.ones(n)));ohp=np.column_stack((outline_pos,np.ones(n)))
    trans=[];outline_trans=[];frame_manifest=[];world_frames=[]
    for name,db in fit_clips:
        a=np.load(work/'animations'/db/(name+'.npz'))
        for frame,v in enumerate(a['qt']):
            world=pose(target,a['names'],v,a['use_translation']);world_frames.append(world)
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
    limits_by_frame=np.array([stock_limits[name] for name,_ in frame_manifest]).T
    chain_by_frame=np.array([chain_limits[name] for name,_ in frame_manifest]).T if loose_geometry else None
    if loose_geometry:
        worlds=np.array(world_frames)
        segments={i:([(i,int(target.parent[i]))] if target.parent[i]>=0 else [])+[(i,int(j)) for j in np.where(target.parent==i)[0]] for i in range(len(target.names))}
    def chain_gradient(points,weights,limit):
        best=np.full((n,nf),np.inf);nearest=np.zeros_like(points)
        for bone in range(len(target.names)):
            rows=np.flatnonzero(np.any((bone_indices==bone)&(weights>1e-5),axis=1))
            if not len(rows):continue
            pp=points[rows]
            for aa,bb in segments[bone]:
                aa,bb=worlds[:,aa,3,:3],worlds[:,bb,3,:3];vv=bb-aa;den=np.maximum(np.einsum('fi,fi->f',vv,vv),1e-15)
                u=np.clip(np.einsum('nfi,fi->nf',pp-aa,vv)/den,0,1);close=aa+u[:,:,None]*vv
                dd=np.linalg.norm(pp-close,axis=2);better=dd<best[rows]
                best[rows]=np.minimum(best[rows],dd);tmp=nearest[rows];tmp[better]=close[better];nearest[rows]=tmp
        over=np.maximum(best-limit[None,:],0)
        return float((over**2).sum()),(points-nearest)*(2*over/np.maximum(best,1e-12))[:,:,None],float((best/(limit[None,:]/.97)).max())
    # Offset-preserving constraint (layers.py): every inner/outer pair keeps >= 30% of its inset in
    # every fit frame; the fit only counts as done with zero crossings.
    from layers import check_plan,layer_penalty,LAYER_WEIGHT
    checks=check_plan(layers,group) if layers is not None else None
    calls=0;start=time.monotonic();last={}
    def calc(x):
        nonlocal calls,last
        x=x.reshape(n,4);z=np.exp(x-x.max(1,keepdims=True));w_free=z/z.sum(1,keepdims=True);w=tie.forward(w_free)
        loss=0.;gw=np.zeros_like(w);maxima=[];relative=[];chain_relative=[];layer_state=(0,1.)
        for part,(coords,bind_length) in enumerate([(C,length),(OC,outline_length)]):
            p=np.einsum('nfki,nk->nfi',coords,w).reshape(n,-1);d=(H@p).reshape(ne,nf,3)
            normd=np.linalg.norm(d,axis=2);ratio=normd/bind_length[:,None];over=np.maximum(ratio-limits_by_frame[part][None,:]*.98,0)
            gd=d*(2*over/(bind_length[:,None]*np.maximum(normd,1e-12)))[:,:,None]
            gp=(H.T@gd.reshape(ne,-1)).reshape(n,nf,3);gw+=np.einsum('nfki,nfi->nk',coords,gp)
            loss+=(over**2).sum();maxima.append(float(ratio.max()));relative.append(float((ratio/limits_by_frame[part][None,:]).max()))
            if checks is not None and part==0 and len(checks['rows']):
                ll,lg,lc,lm=layer_penalty(p.reshape(n,nf,3),checks)
                loss+=LAYER_WEIGHT*ll;gw+=LAYER_WEIGHT*np.einsum('nfki,nfi->nk',coords,lg);layer_state=(lc,lm)
            if loose_geometry:
                cl,cg,cr=chain_gradient(p.reshape(n,nf,3),w,chain_by_frame[part]*.97)
                loss+=cl;gw+=np.einsum('nfki,nfi->nk',coords,cg);chain_relative.append(cr)
        loss+=regularizer*((w-initial)**2).sum();gw+=2*regularizer*(w-initial)
        gw=tie.backward(gw);gx=w_free*(gw-(gw*w_free).sum(1,keepdims=True));calls+=1
        last={'calls':calls,'loss':float(loss),'max_ratio':max(maxima),'max_relative_to_stock':max(relative),'max_body_ratio':maxima[0],'max_outline_ratio':maxima[1],'seconds':time.monotonic()-start,
              'max_chain_relative_to_stock':max(chain_relative) if chain_relative else None,
              'layer_crossings':layer_state[0],'layer_min_offset_ratio':layer_state[1]}
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
    def within(margin):
        return (last['max_relative_to_stock']<=margin and (last['max_chain_relative_to_stock'] is None or last['max_chain_relative_to_stock']<=margin)
                and last['layer_crossings']==0)
    if within(1):
        message='Existing local fit revalidated over all current frames' if start_weights is not initial else 'Initial seam-safe fit passes stock edge limits'
        result=SimpleNamespace(x=initial_x,success=True,message=message,nit=0)
    else:
        callback_count=0
        def gate_stop(xk):
            nonlocal callback_count
            callback_count+=1
            if within(.995):
                calc(xk)
                if within(.995):raise StopIteration
        result=minimize(calc,initial_x,jac=True,callback=gate_stop,method='L-BFGS-B',options={'maxiter':iterations,'maxcor':12,'ftol':1e-10,'gtol':1e-6})
    calc(result.x);z=np.exp(result.x.reshape(n,4)-result.x.reshape(n,4).max(1,keepdims=True));w=tie.forward(z/z.sum(1,keepdims=True))
    repair=None
    if layers is not None:
        # Residual crossings (second-order: weights vary across the outer triangle) are closed exactly
        # by giving that inner row and its outer triangle one shared influence vector (layers.py).
        from layers import check_plan,repair_crossings,lanes_from_dense,to_dense
        P=len(target.palette);pal_bone=np.array([by[j] for j in target.palette])
        mats_pal=[np.array([target.inverse_by_bone[b]@world[b] for b in pal_bone]) for world in world_frames]
        def posed_fn(D,rows):
            out=np.empty((len(rows),len(mats_pal),3));hr=hp[rows];Dr=D[rows]
            for fi,M in enumerate(mats_pal):out[:,fi]=np.einsum('np,ni,pij->nj',Dr,hr,M)[:,:3]
            return out
        D,repair=repair_crossings(to_dense(indices,w,P),check_plan(layers,group),posed_fn)
        if repair['unified_rows']:
            indices,w=lanes_from_dense(D);indices=indices.astype('u1');output_indices=indices[group]
    report={**last,'solver_converged':bool(result.success),'solver_message':str(result.message),'iterations':int(result.nit),'frames':len(frame_manifest),'animations':[n for n,_ in fit_clips],'stock_edge_limits':stock_limits,'gate_pass':last['max_relative_to_stock']<=1,'regularizer':regularizer,'outline_smoothing':outline_smoothing,'target_stock_fraction':.98}
    report['shared_vertex_groups']=group_count
    report['loose_geometry']=loose_report
    report['layer_repair']=repair
    report['gate_pass']=within(1)
    report['uv_seams_share_weights']=True
    report['layer_coupling']={'pairs':0 if layers is None else int(len(layers['vertex'])),'tied_rows':int(len(plan['rows'])),
                              'levels':len(plan['levels']),'rule':'inner shell bound with and tied to its outer triangle blend (layers.py)'}
    report['sole_alignment']=sole_report
    report['started_from_cache']=start_weights is not initial
    (work/'fit.json').write_text(json.dumps(report,indent=2))
    fitted=dict(pos=output_pos,norm=output_norm,weights=w[group],indices=output_indices,outline_norm=output_outline)
    np.savez(work/'fit.npz',**fitted)
    # The serialized candidate is independently checked by validate.evaluate;
    # failed fits remain diagnostics and are never enabled by the converter.
    return fitted,report
