"""Small seam-shared bind corrections under stock-relative animation constraints.

Fixed skin weights, fixed port skeleton, fixed outline directions. Corrections
are bounded per coordinate (two model units by default); feet/hands are locked. Reports
the actual maximum displacement. Independent full-frame validation is mandatory.
"""
import argparse,json,time,datetime,os
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import coo_matrix
from model import Model,pose
from validate import edges,chain_distance
from convert_mua2 import ROOT,PORT,Source,atlas,resolve,GAME,write_candidate

def refine(char,iterations=120,samples=24,bound=2.,joint_weights=False,surface_regularize=False,reset_bind=False,seed_weights=None,layer_lock=False,couple_layers=False,layer_weight=None):
    out=ROOT/'evidence'/char;manifest=json.loads((out/'conversion.json').read_text());target=Model(manifest['template']);current=Model(manifest['candidate'])
    source=Source(manifest['source'],target);old=dict(np.load(out/'fit/fit.npz'))
    if seed_weights:
        seed=Model(seed_weights)
        if len(seed.pos)!=len(source.pos):raise ValueError('Seed weight vertex count differs')
        old['weights']=seed.weights.astype(float);old['indices']=seed.indices.copy()
    # The preceding fit emits exactly identical seam-group coordinates/weights.
    _,rep,group=np.unique(old['pos'],axis=0,return_index=True,return_inverse=True)
    n=len(rep);p0=old['pos'][rep].astype(float);hn=old['outline_norm'][rep].astype(float)
    indices=old['indices'][rep];weights=old['weights'][rep]
    layer_pairs=[];motion_group=np.arange(n);motion_count=np.ones(n)
    if layer_lock:
        from scipy.spatial import cKDTree
        candidates=np.array(sorted(cKDTree(source.pos).query_pairs(.85)),dtype=int).reshape(-1,2)
        candidates=candidates[source.coat_mask[candidates].all(1)&(np.einsum('ni,ni->n',source.norm[candidates[:,0]],source.norm[candidates[:,1]])<-.8)]
        nearest={}
        for aa,bb in candidates:
            d=float(np.linalg.norm(source.pos[aa]-source.pos[bb]))
            for a,b in [(int(aa),int(bb)),(int(bb),int(aa))]:
                if a not in nearest or d<nearest[a][0]:nearest[a]=(d,b)
        parent=np.arange(n)
        def find(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for a,(_,b) in nearest.items():
            if a<b and nearest.get(b,(0,-1))[1]==a:
                ga,gb=int(group[a]),int(group[b])
                if ga!=gb:parent[find(gb)]=find(ga);layer_pairs.append([ga,gb])
        _,motion_group=np.unique([find(i) for i in range(n)],return_inverse=True);motion_count=np.bincount(motion_group)
    # Layered cloth (layers.py, replaces the experimental mutual-pair --layer-lock): each inner-shell
    # row takes the barycentric blend of its outer triangle's bind correction (the authored inset
    # vector is kept exactly) and of its weights (both shells deform by the same blend).
    plan=None
    if couple_layers:
        from layers import source_layers,find_layers,coupling_plan,tie_lanes,WeightTie,skeleton_segments
        if layer_lock:raise ValueError('--couple-layers replaces --layer-lock; use one')
        found=source_layers(source)
        plan=coupling_plan(found,group,n)
        indices,weights=tie_lanes(plan,weights,indices,len(target.palette));indices=indices.astype('u1')
        tie=WeightTie(plan,indices,len(target.palette))
    def disp_forward(d):
        if plan is None:return d
        d=d.copy()
        for lv in plan['levels']:
            r,t,b=plan['rows'][lv],plan['tri_rows'][lv],plan['bary'][lv];d[r]=np.einsum('kj,kji->ki',b,d[t])
        return d
    def disp_backward(g):
        if plan is None:return g
        g=g.copy()
        for lv in reversed(plan['levels']):
            r,t,b=plan['rows'][lv],plan['tri_rows'][lv],plan['bary'][lv]
            np.add.at(g,t.ravel(),(b[:,:,None]*g[r][:,None,:]).reshape(-1,3));g[r]=0
        return g
    def project(values):
        if not layer_lock:return values
        acc=np.zeros((len(motion_count),)+values.shape[1:]);np.add.at(acc,motion_group,values)
        acc/=motion_count.reshape((-1,)+(1,)*(values.ndim-1));return acc[motion_group]
    if layer_lock:
        dense=np.zeros((n,len(target.palette)))
        for k in range(4):np.add.at(dense,(np.arange(n),indices[:,k]),weights[:,k])
        dense=project(dense);indices=np.argsort(dense,axis=1)[:,-4:][:,::-1].copy().astype('u1');weights=np.take_along_axis(dense,indices,axis=1);weights/=weights.sum(1,keepdims=True)
    bm={b.get(4):i for i,b in enumerate(target.bones) if b.get(4)>=0};bi=np.array([bm[i] for i in target.palette])[indices]
    locked_bones=[i for i,name in enumerate(target.names) if any(s in name for s in [' Foot',' Toe',' Hand',' Finger'])]
    locked=np.any(np.isin(bi,locked_bones)&(weights>.25),axis=1)
    ee=np.unique(np.sort(group[edges(current)],axis=1),axis=0);ee=ee[ee[:,0]!=ee[:,1]];ne=len(ee)
    H=coo_matrix((np.r_[np.ones(ne),-np.ones(ne)],(np.r_[np.arange(ne),np.arange(ne)],np.r_[ee[:,0],ee[:,1]])),shape=(ne,n)).tocsr()
    hull_name=next(g.get(2) for g in target.f.objects_of('igGeometry') if 'outline' in (g.get(2) or ''));stock_hull=Model(target.path,hull_name)
    st=[target,stock_hull];se=[edges(m) for m in st];sl=[np.linalg.norm(m.pos[e[:,0]]-m.pos[e[:,1]],axis=1) for m,e in zip(st,se)]
    worlds=[];mats=[];limits=[]
    for name,db in manifest['clips']:
        a=np.load(out/'fit/animations'/db/(name+'.npz'));mx=np.zeros((2,2))
        for v in a['qt']:
            w=pose(target,a['names'],v,a['use_translation'])
            for j,m in enumerate(st):
                p=m.skin(w);valid=sl[j]>1e-6
                mx[j,0]=max(mx[j,0],(np.linalg.norm(p[se[j][:,0]]-p[se[j][:,1]],axis=1)[valid]/sl[j][valid]).max())
                mx[j,1]=max(mx[j,1],chain_distance(m,p,w).max())
        frame_ids=range(len(a['qt'])) if samples<=0 else np.unique(np.linspace(0,len(a['qt'])-1,min(samples,len(a['qt']))).astype(int))
        for fi in frame_ids:
            w=pose(target,a['names'],a['qt'][fi],a['use_translation']);worlds.append(w)
            mats.append(np.einsum('nk,nkij->nij',weights,(target.inverse_by_bone@w)[bi]));limits.append(mx*.985)
    worlds=np.array(worlds);T=np.array(mats).transpose(1,0,2,3).copy();limits=np.array(limits);nf=len(worlds)
    raw_matrices=np.array([(target.inverse_by_bone@world)[bi] for world in worlds],dtype='f4').transpose(1,0,2,3,4).copy() if joint_weights else None
    segs={i:([(i,int(target.parent[i]))] if target.parent[i]>=0 else [])+[(i,int(j)) for j in np.where(target.parent==i)[0]] for i in range(len(target.names))}
    active={i:np.flatnonzero(np.any((bi==i)&(weights>1e-5),axis=1)) for i in range(len(target.names))}
    def chain(p,limit,ww):
        best=np.full((n,nf),np.inf);near=np.zeros_like(p)
        selections={i:np.flatnonzero(np.any((bi==i)&(ww>1e-5),axis=1)) for i in range(len(target.names))} if joint_weights else active
        for bone,rows in selections.items():
            if not len(rows):continue
            pp=p[rows]
            for aa,bb in segs[bone]:
                aa,bb=worlds[:,aa,3,:3],worlds[:,bb,3,:3];v=bb-aa;den=np.maximum(np.einsum('fi,fi->f',v,v),1e-15)
                u=np.clip(np.einsum('nfi,fi->nf',pp-aa,v)/den,0,1);close=aa+u[:,:,None]*v;d=np.linalg.norm(pp-close,axis=2)
                better=d<best[rows];best[rows]=np.minimum(best[rows],d);tmp=near[rows];tmp[better]=close[better];near[rows]=tmp
        over=np.maximum(best-limit[None,:],0);grad=(p-near)*(2*over/np.maximum(best,1e-12))[:,:,None]
        return float((over*over).sum()),grad,float((best/(limit[None,:]/.985)).max())
    checks=None
    if plan is not None:
        from layers import check_plan,layer_penalty,LAYER_WEIGHT
        checks=check_plan(found,group)
        LAYER_WEIGHT=LAYER_WEIGHT if layer_weight is None else layer_weight
    start=time.monotonic();calls=0;last={}
    def fun(x):
        nonlocal calls,last
        raw_p=x[:n*3].reshape(n,3);p=base+project(raw_p-base) if layer_lock else base+disp_forward(raw_p-base) if plan is not None else raw_p
        grad=np.zeros_like(p);loss=0.;ers=[];crs=[];wg=np.zeros_like(weights);layer_state=(0,1.)
        constraint_scale=100. if surface_regularize else 1.
        if joint_weights:
            logits=project(x[n*3:].reshape(n,4));z=np.exp(logits-logits.max(1,keepdims=True));ww_free=z/z.sum(1,keepdims=True)
            ww=tie.forward(ww_free) if plan is not None else ww_free
            transforms=np.einsum('nk,nfkij->nfij',ww,raw_matrices)
        else:ww=weights;transforms=T
        for j in range(2):
            rest=p+hn*.28*j;rest_d=H@rest;le=np.maximum(np.linalg.norm(rest_d,axis=1),1e-8)
            posed=np.einsum('ni,nfij->nfj',rest,transforms[:,:,:3,:3])+transforms[:,:,3,:3]
            d=(H@posed.reshape(n,-1)).reshape(ne,nf,3);dn=np.maximum(np.linalg.norm(d,axis=2),1e-12);ratio=dn/le[:,None]
            over=np.maximum(ratio-limits[:,j,0],0)
            gd=d*(2*over/(le[:,None]*dn))[:,:,None]
            gp=(H.T@gd.reshape(ne,-1)).reshape(n,nf,3)
            grad+=constraint_scale*np.einsum('nfij,nfj->ni',transforms[:,:,:3,:3],gp)
            grad+=constraint_scale*(H.T@(rest_d*(-2*np.sum(over*dn,axis=1)/le**3)[:,None]))
            loss+=constraint_scale*float((over*over).sum());ers.append(float((ratio/(limits[None,:,j,0]/.985)).max()))
            cl,cg,cr=chain(posed,limits[:,j,1],ww);loss+=constraint_scale*cl;grad+=constraint_scale*np.einsum('nfij,nfj->ni',transforms[:,:,:3,:3],cg);crs.append(cr)
            if checks is not None and j==0 and len(checks['rows']) and LAYER_WEIGHT>0:
                # Offset-preserving constraint (layers.py): keep >= 30% of every authored inset.
                ll,lg,lc,lm=layer_penalty(posed,checks);lg=LAYER_WEIGHT*constraint_scale*lg
                loss+=LAYER_WEIGHT*constraint_scale*ll;grad+=np.einsum('nfij,nfj->ni',transforms[:,:,:3,:3],lg);layer_state=(lc,lm)
                cg=cg+lg/constraint_scale
            if joint_weights:
                lane=np.einsum('ni,nfkij->nfkj',np.column_stack((rest,np.ones(n))),raw_matrices)[:,:,:,:3]
                wg+=constraint_scale*np.einsum('nfkj,nfj->nk',lane,gp+cg)
        diff=p-p0;loss+=.003*float((diff*diff).sum());grad+=.006*diff
        min_orientation=None
        if surface_regularize:
            # Spread the correction over the cloth surface; unconstrained local
            # corrections can satisfy edge bounds while inverting triangles.
            delta=H@(p-base);loss+=float((delta*delta*surface_edge_weight[:,None]).sum())
            grad+=2*(H.T@(delta*surface_edge_weight[:,None]))
            aa,bb,cc=p[face[:,0]],p[face[:,1]],p[face[:,2]];u=bb-aa;v=cc-aa
            ratio=np.einsum('ni,ni->n',np.cross(u,v),base_face_normal)/base_face_area
            over=np.maximum(.3-ratio,0);loss+=1000*float((over*over).sum());min_orientation=float(ratio.min())
            factor=(-2000*over/base_face_area)[:,None]
            gb=np.cross(v,base_face_normal)*factor;gc=np.cross(base_face_normal,u)*factor
            np.add.at(grad,face[:,0],-gb-gc);np.add.at(grad,face[:,1],gb);np.add.at(grad,face[:,2],gc)
            if joint_weights:
                dense=np.zeros((n,len(target.palette)))
                for lane_id in range(4):np.add.at(dense,(np.arange(n),indices[:,lane_id]),ww[:,lane_id])
                dw=H@dense;loss+=float((dw*dw*cloth_edge_weight[:,None]).sum())
                dg=2*(H.T@(dw*cloth_edge_weight[:,None]))
                wg+=np.take_along_axis(dg,indices,axis=1)
        if joint_weights:
            loss+=.01*float(((ww-weights)**2).sum());wg+=.02*(ww-weights)
            if plan is not None:wg=tie.backward(wg)
            wg=ww_free*(wg-(wg*ww_free).sum(1,keepdims=True));gradient=np.r_[project(disp_backward(grad)).ravel(),project(wg).ravel()]
        else:gradient=project(disp_backward(grad)).ravel()
        calls+=1;last=dict(calls=calls,loss=loss,max_stock_edge_fraction=max(ers),max_stock_chain_fraction=max(crs),min_bind_face_orientation=min_orientation,seconds=time.monotonic()-start,
                           layer_crossings=layer_state[0],layer_min_offset_ratio=layer_state[1])
        if calls==1 or calls%25==0:print(json.dumps(last),flush=True)
        return loss,gradient
    baseline=out/'weight-constraints-only.IGB'
    base=Model(baseline).pos[rep].astype(float) if baseline.exists() else p0
    if reset_bind:p0=base.copy()
    if plan is not None:
        # Exact bind inset before refining (layers.place_inner_shells): inner shells are placed from
        # their outer triangle's frame; the tied corrections below then move both shells together.
        from layers import place_inner_shells
        vpos,_=place_inner_shells(base[group],np.asarray(source.pos,float),found,len(group))
        acc=np.zeros_like(base);np.add.at(acc,group,vpos);base=acc/np.bincount(group,minlength=n)[:,None];p0=base.copy()
    base_lengths=np.linalg.norm(H@base,axis=1);surface_edge_weight=1/np.maximum(base_lengths,.2)**2
    cloth=source.coat_mask[rep];cloth_edge_weight=surface_edge_weight*(cloth[ee[:,0]]|cloth[ee[:,1]])
    face=group[source.tri];cross=np.cross(base[face[:,1]]-base[face[:,0]],base[face[:,2]]-base[face[:,0]])
    area=np.linalg.norm(cross,axis=1);valid=area>1e-4;face=face[valid];base_face_area=area[valid];base_face_normal=cross[valid]/base_face_area[:,None]
    lo=base-bound;hi=base+bound;lo[locked]=p0[locked];hi[locked]=p0[locked]
    x0=p0.ravel();bounds=list(zip(lo.ravel(),hi.ravel()))
    if joint_weights:
        logits0=np.log(np.maximum(weights,1e-12));x0=np.r_[x0,logits0.ravel()]
        wlo=np.full_like(logits0,-30.);whi=np.full_like(logits0,30.);wlo[locked]=logits0[locked];whi[locked]=logits0[locked]
        bounds+=list(zip(wlo.ravel(),whi.ravel()))
    initial_loss,initial_grad=fun(x0);eligible=np.flatnonzero(np.repeat(~locked,3))
    if joint_weights:eligible=np.r_[eligible,n*3+np.flatnonzero(np.repeat(~locked,4))]
    check_ids=eligible[np.argsort(np.abs(initial_grad[eligible]))[-3:]];gradient_errors=[]
    for ix in check_ids:
        xx=x0.copy();step=1e-5;xx[ix]+=step;plus=fun(xx)[0];xx[ix]-=2*step;minus=fun(xx)[0]
        numeric=(plus-minus)/(2*step);error=abs(numeric-initial_grad[ix])/max(1,abs(numeric),abs(initial_grad[ix]));gradient_errors.append(float(error))
    if max(gradient_errors)>1e-3:raise ValueError('Bind objective gradient check failed: '+str(gradient_errors))
    def stop_when_margin(x):
        if (out/'stop-fitting.flag').exists():raise StopIteration
        deadline=os.environ.get('MUA2_FIT_DEADLINE')
        if deadline and datetime.datetime.now(datetime.timezone.utc)>=datetime.datetime.fromisoformat(deadline):
            raise StopIteration
        orientation_ok=not surface_regularize or last['min_bind_face_orientation']>=.2
        layers_ok=lambda:checks is None or not LAYER_WEIGHT or last['layer_crossings']==0
        if calls>=25 and orientation_ok and last['max_stock_edge_fraction']<=.99 and last['max_stock_chain_fraction']<=.99 and layers_ok():
            fun(x)
            if (not surface_regularize or last['min_bind_face_orientation']>=.2) and last['max_stock_edge_fraction']<=.99 and last['max_stock_chain_fraction']<=.99 and layers_ok():raise StopIteration
    result=minimize(fun,x0,jac=True,callback=stop_when_margin,method='L-BFGS-B',bounds=bounds,options={'maxiter':iterations,'maxcor':10,'ftol':1e-10,'gtol':1e-7})
    fun(result.x);p=result.x[:n*3].reshape(n,3)
    if layer_lock:p=base+project(p-base)
    elif plan is not None:p=base+disp_forward(p-base)
    old['pos']=p[group];old['indices']=indices[group]
    if joint_weights:
        logits=project(result.x[n*3:].reshape(n,4));z=np.exp(logits-logits.max(1,keepdims=True));ww=z/z.sum(1,keepdims=True)
        old['weights']=(tie.forward(ww) if plan is not None else ww)[group]
    elif plan is not None:old['weights']=weights[group]
    repair=None
    if plan is not None:
        # Residual crossings over every refit frame: one shared influence vector for the patch.
        from layers import check_plan,repair_crossings,lanes_from_dense,to_dense
        P=len(target.palette);pal_bone=np.array([bm[i] for i in target.palette]);hp=np.column_stack((p,np.ones(n)))
        mats_pal=[(target.inverse_by_bone@world)[pal_bone] for world in worlds]
        def posed_fn(D,rows):
            out=np.empty((len(rows),len(mats_pal),3));hr=hp[rows];Dr=D[rows]
            for fi,M in enumerate(mats_pal):out[:,fi]=np.einsum('np,ni,pij->nj',Dr,hr,M)[:,:3]
            return out
        final_w=old['weights'][rep]
        # Report only: unifying a patch's influences here ignores the stretch/chain/surface terms
        # (Gambit pass 1: pain_high stretch 6.49x after a repair), so refine_bind never applies it;
        # the offset-preserving constraint pass is what closes crossings.
        D,repair=repair_crossings(to_dense(indices,final_w,P),check_plan(found,group),posed_fn,rounds=0)
        repair['applied']=False
    elif layer_lock:old['weights']=weights[group]
    report={**last,'gradient_check_relative_errors':gradient_errors,'iterations':int(result.nit),'message':str(result.message),'groups':n,'frames':nf,'locked_groups':int(locked.sum()),'coordinate_bound':bound,
            'maximum_bind_displacement':float(np.linalg.norm(p-base,axis=1).max()),'rms_bind_displacement':float(np.sqrt(((p-base)**2).sum(1).mean())),
            'maximum_incremental_displacement':float(np.linalg.norm(p-p0,axis=1).max()),'all_frames':samples<=0,'joint_weights':joint_weights,'surface_regularize':surface_regularize,'reset_bind':reset_bind,'seed_weights':str(seed_weights) if seed_weights else None,
            'layer_lock':layer_lock,'paired_inner_outer_groups':len(layer_pairs),'motion_groups':len(motion_count),
            'couple_layers':couple_layers,'coupled_inner_rows':0 if plan is None else int(len(plan['rows'])),'layer_repair':repair}
    np.savez(out/'fit/fit.npz',**old);(out/'bind-refinement.json').write_text(json.dumps(report,indent=2))
    textures=resolve(source.path,GAME,out/'textures');image,atlas_report=atlas(source,textures,out);write_candidate(source,target,Path(manifest['candidate']),old,image)
    import hashlib
    manifest['bind_refinement']=report;manifest['candidate_sha256']=hashlib.sha256(Path(manifest['candidate']).read_bytes()).hexdigest()
    manifest['fit']['gate_pass']=None;manifest['fit']['gate_deferred_to_independent_validator']=True
    (out/'conversion.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--character',required=True,choices=['gambit','wolverine']);ap.add_argument('--iterations',type=int,default=120);ap.add_argument('--samples',type=int,default=24);ap.add_argument('--bound',type=float,default=2.);ap.add_argument('--joint-weights',action='store_true');ap.add_argument('--surface-regularize',action='store_true');ap.add_argument('--reset-bind',action='store_true');ap.add_argument('--seed-weights',type=Path);ap.add_argument('--layer-lock',action='store_true');ap.add_argument('--couple-layers',action='store_true',help='tie inner cloth shells to their outer shell (layers.py)');ap.add_argument('--layer-weight',type=float,help='offset-preserving constraint weight (0: tie only; a second pass can add it)');a=ap.parse_args();refine(a.character,a.iterations,a.samples,a.bound,a.joint_weights,a.surface_regularize,a.reset_bind,a.seed_weights,a.layer_lock,a.couple_layers,a.layer_weight)
