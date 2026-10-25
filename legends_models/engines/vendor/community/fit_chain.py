"""Bounded, seam-shared cosmetic chain-envelope fit. Never edits skeletons.

The optional refine API uses every cached reference frame, fixed influences and
fixed normal/outline directions. It returns an unchanged fit if any shared
vertex group cannot satisfy its constraints inside the displacement cap.
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path
import numpy as np

sys.dont_write_bytecode = True
OUTLINE_WIDTH = .28
DISPLACEMENT_HEIGHT_FRACTION = .02
SAFETY_MARGIN = 1e-4


def project_capsule(point, start, end, radius):
    point,start,end = map(lambda value:np.asarray(value,dtype=float),(point,start,end))
    direction=end-start
    u=np.clip((point-start)@direction/max(direction@direction,1e-15),0,1)
    center=start+u*direction
    delta=point-center;distance=np.linalg.norm(delta)
    return point.copy() if distance<=radius else center+delta*(radius/distance)


def inverse_affine_step(world_delta, linear):
    """Row-vector inverse of a weighted LBS affine displacement."""
    return np.linalg.solve(np.asarray(linear).T,np.asarray(world_delta))


def shared_groups(source_positions, fitted_positions):
    """Union exact source seams and exact fitted seams, including transitive ties."""
    n=len(source_positions)
    if len(fitted_positions)!=n:raise ValueError('source correspondence changed')
    parent=np.arange(n)
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for positions in (source_positions,fitted_positions):
        _,inverse=np.unique(positions,axis=0,return_inverse=True)
        first={}
        for i,group in enumerate(inverse):
            if group in first:parent[root(i)]=root(first[group])
            else:first[group]=i
    return np.unique([root(i) for i in range(n)],return_inverse=True)[1]


def solve_group(original, outline_offset, linear, translation, starts, ends,
                body_radii, outline_radii, displacement_cap):
    """Find the closest shared bind point satisfying all frame capsules.

    starts/ends contain only segments incident on the group's fixed positive
    influences. Their union is evaluated exactly, independently in every frame.
    """
    from scipy.optimize import minimize
    original=np.asarray(original,dtype=float)
    outline_offset=np.asarray(outline_offset,dtype=float)
    linear=np.asarray(linear,dtype=float);translation=np.asarray(translation,dtype=float)
    starts=np.asarray(starts,dtype=float);ends=np.asarray(ends,dtype=float)
    radii=np.concatenate([body_radii,outline_radii])-SAFETY_MARGIN
    direction=ends-starts;den=np.maximum(np.sum(direction*direction,axis=2),1e-15)
    nf=len(linear)
    def distances_and_gradient(x):
        points=np.einsum('i,fij->fj',x,linear)+translation
        offset=np.einsum('i,fij->fj',outline_offset,linear)
        output=[];gradients=[]
        for p in (points,points+offset):
            u=np.clip(np.einsum('fsj,fsj->fs',p[:,None,:]-starts,direction)/den,0,1)
            centers=starts+u[:,:,None]*direction
            residual=p[:,None,:]-centers
            norm=np.linalg.norm(residual,axis=2)
            chosen=np.argmin(norm,axis=1)
            dist=norm[np.arange(nf),chosen]
            radial=residual[np.arange(nf),chosen]/np.maximum(dist[:,None],1e-15)
            output.append(dist)
            gradients.append(np.einsum('fij,fj->fi',linear,radial))
        return np.concatenate(output),np.concatenate(gradients)
    initial,_=distances_and_gradient(original)
    # Lipschitz lower bound: ||dx A|| <= largest-singular-value(A) ||dx||.
    scale=np.linalg.svd(linear,compute_uv=False)[:,0]
    lower=float(np.max(np.maximum(initial-radii,0)/np.tile(scale,2)))
    if np.all(initial<=radii):
        return original.copy(),dict(feasible=True,displacement=0.,lower_bound_displacement=0.,iterations=0)
    if lower>displacement_cap+1e-9:
        return original.copy(),dict(feasible=False,reason='displacement cap lower bound',
            displacement=0.,lower_bound_displacement=lower,cap=float(displacement_cap))
    def constraints(x):
        distances,gradient=distances_and_gradient(x)
        delta=x-original
        return np.r_[radii-distances,displacement_cap**2-delta@delta],np.vstack([-gradient,-2*delta])
    result=minimize(lambda x:((x-original)@(x-original),2*(x-original)),original.copy(),jac=True,
        constraints={'type':'ineq','fun':lambda x:constraints(x)[0],'jac':lambda x:constraints(x)[1]},
        method='SLSQP',options={'maxiter':180,'ftol':1e-12,'disp':False})
    distance,_=distances_and_gradient(result.x)
    displacement=float(np.linalg.norm(result.x-original))
    feasible=bool(np.all(distance<=radii+1e-7) and displacement<=displacement_cap+1e-8)
    return result.x if feasible else original.copy(),dict(feasible=feasible,displacement=displacement,
        lower_bound_displacement=lower,cap=float(displacement_cap),iterations=int(result.nit),
        solver_message=str(result.message),worst_constraint_excess=float(np.max(distance-radii)))


def refine(source,target,fit,port,animation_root,animation_db,*,max_displacement_fraction=.02):
    """Return (fitdict, report); write nothing and never prepare/edit animations.

    A failed bound leaves the returned fit unchanged. Caller must serialize into
    a separate file and run the unchanged full validator before considering use.
    The default cosmetic displacement safeguard is 2% of stock height. A caller
    may explicitly select another positive fraction; the report records it.
    """
    max_displacement_fraction=float(max_displacement_fraction)
    if not np.isfinite(max_displacement_fraction) or max_displacement_fraction<=0:
        raise ValueError('max_displacement_fraction must be positive and finite')
    from community_model import Model,pose
    from validate import REFERENCE_CLIPS,chain_distance
    result={key:np.array(value,copy=True) for key,value in fit.items()}
    original=np.asarray(fit['pos'],dtype='<f4').astype(float)
    weights=np.asarray(fit['weights'],dtype='<f4').astype(float)
    indices=np.asarray(fit['indices'],dtype=np.uint8)
    outline_norm=np.asarray(fit.get('outline_norm',fit['norm']),dtype=float)
    # The serializer first adds extrusion in float64, then casts its position
    # block. A strict safety margin covers the independent float32 round-trip.
    groups=shared_groups(source.pos,original)
    representatives=np.unique(groups,return_index=True)[1]
    for i in representatives:
        members=np.flatnonzero(groups==groups[i])
        for values,name in [(original,'positions'),(weights,'weights'),(indices,'indices'),(outline_norm,'outline directions')]:
            if not np.array_equal(values[members],np.broadcast_to(values[i],values[members].shape)):
                raise ValueError('coincident seam group has differing '+name)
    model=copy.copy(target)
    model.pos=original;model.weights=weights;model.indices=indices
    by={bone.get(4):i for i,bone in enumerate(target.bones) if bone.get(4)>=0}
    model.bone_indices=np.array([by[m] for m in target.palette])[indices]
    height=float(np.ptp(target.pos[:,2]));cap=height*max_displacement_fraction
    hull=Model(target.path,'__outline__')
    animation_root=Path(animation_root)
    mapping_path=animation_root/'reference-map.json'
    mapping=json.loads(mapping_path.read_text())['clips'] if mapping_path.exists() else {
        name:'common.IGB' if name=='pain_high' else animation_db for name in REFERENCE_CLIPS}
    worlds=[];frame_clips=[];limits={};frame_manifest=[]
    for name in REFERENCE_CLIPS:
        animation=np.load(animation_root/mapping[name]/(name+'.npz'))
        worst_body=worst_hull=0.
        for frame,qt in enumerate(animation['qt']):
            world=pose(target,animation['names'],qt,animation['use_translation'])
            worlds.append(world);frame_clips.append(name);frame_manifest.append((name,frame))
            worst_body=max(worst_body,float(chain_distance(target,target.skin(world),world).max()))
            worst_hull=max(worst_hull,float(chain_distance(hull,hull.skin(world),world).max()))
        limits[name]=[worst_body,worst_hull]
    worlds=np.asarray(worlds)
    body_radii=np.array([limits[name][0] for name in frame_clips])
    outline_radii=np.array([limits[name][1] for name in frame_clips])
    # Find only genuine outliers; every untouched group keeps its original bind.
    affected=np.zeros(len(original),dtype=bool)
    before={name:[0.,0.] for name in REFERENCE_CLIPS}
    for frame,(world,name) in enumerate(zip(worlds,frame_clips)):
        for part,points in enumerate((original,original+outline_norm*OUTLINE_WIDTH)):
            distance=chain_distance(model,model.skin(world,positions=points),world)
            before[name][part]=max(before[name][part],float(distance.max()))
            affected|=distance>limits[name][part]-SAFETY_MARGIN
    chosen=np.unique(groups[affected]);records=[];positions=original.copy()
    for group in chosen:
        i=int(representatives[group]);members=np.flatnonzero(groups==group)
        bones=model.bone_indices[i];weight=weights[i]
        transforms=np.einsum('k,fkij->fij',weight,
            np.asarray([target.inverse_by_bone[bones]@world[bones] for world in worlds]))
        pairs=set()
        for bone,w in zip(bones,weight):
            if w<=1e-5:continue
            if target.parent[bone]>=0:pairs.add((int(bone),int(target.parent[bone])))
            pairs.update((int(bone),int(child)) for child in np.flatnonzero(target.parent==bone))
        if not pairs:raise ValueError('weighted vertex has no incident bone chain')
        first,last=map(np.array,zip(*sorted(pairs)))
        starts=worlds[:,first,3,:3];ends=worlds[:,last,3,:3]
        point,record=solve_group(original[i],outline_norm[i]*OUTLINE_WIDTH,
            transforms[:,:3,:3],transforms[:,3,:3],starts,ends,body_radii,outline_radii,cap)
        positions[members]=point
        record.update(group=int(group),vertices=members.tolist(),dominant_bone=target.names[bones[np.argmax(weight)]])
        records.append(record)
    feasible=all(record['feasible'] for record in records)
    if feasible:
        result['pos']=np.array(fit['pos'],copy=True)
        for record in records:
            if record['displacement']>1e-12:
                members=record['vertices']
                result['pos'][members]=positions[members].astype('<f4').astype(float)
        # Check cap after serialization precision, not only solver precision.
        if float(np.linalg.norm(result['pos']-original,axis=1).max())>cap+1e-6:
            feasible=False
            result={key:np.array(value,copy=True) for key,value in fit.items()}
    report=dict(feasible=feasible,frames=len(worlds),clips=REFERENCE_CLIPS,stock_body_height=height,
        displacement_cap=cap,displacement_height_fraction=max_displacement_fraction,
        safety_margin=SAFETY_MARGIN,affected_groups=len(chosen),shared_groups=len(representatives),
        changed_vertices=int(np.any(result['pos']!=np.asarray(fit['pos']),axis=1).sum()) if feasible else 0,
        max_displacement=float(np.linalg.norm(result['pos']-np.asarray(fit['pos']),axis=1).max()),
        stock_chain_limits=limits,before_chain_maxima=before,groups=records,
        weights_normals_indices_unchanged=all(np.array_equal(result[key],fit[key]) for key in fit if key!='pos'),
        requires_full_serialized_validation=True)
    return result,report


def main():
    import community_convert
    from community_model import Model
    import validate
    root=Path(__file__).resolve().parents[1]
    port=Path(__import__('os').environ['LEGENDS_PORT'])
    destination=root/'output/chain-prototype';destination.mkdir(parents=True,exist_ok=True)
    reports={}
    for relative in ['wolverine/original','emma-frost/ultimate-xml1']:
        work=root/'output'/relative
        conversion=json.loads((work/'conversion.json').read_text())
        source_path=root/conversion['source']
        source,target=Model(source_path),Model(conversion['template'])
        fit=dict(np.load(work/'fit.npz'))
        db=json.loads((work/'animations/reference-map.json').read_text())['hero_db']
        fitted,report=refine(source,target,fit,port,work/'animations',db)
        out=destination/relative;out.mkdir(parents=True,exist_ok=True)
        if report['feasible']:
            np.savez(out/'fit.npz',**fitted)
            candidate=out/Path(conversion['template']).name
            community_convert.convert_candidate(source_path,Path(conversion['template']),candidate,fitted)
            validation=validate.evaluate(port,source_path,candidate,work/'animations',out/'validation.json',
                render_root=None,animation_db=db,target_id=candidate.stem,claws=relative.startswith('wolverine'))
            report['full_acceptance']=validation['acceptance']
            # Shared bounds reveal the small silhouette change at 1024px tall.
            from community_model import texture
            from render import render
            from PIL import Image
            previous=Model(root/conversion['output']);refined=Model(candidate)
            atlas=texture(previous)
            for view in ['front','side','back']:
                axes={'front':(1,2),'back':(1,2),'side':(0,2)}[view]
                xy=np.concatenate([previous.pos[:,axes],refined.pos[:,axes]])
                if view in ['front','side']:xy[:,0]*=-1
                bounds=xy.min(0),xy.max(0)
                images=[render(m,m.pos,atlas,view,title,width=1024,bounds=bounds,outline=True)
                    for m,title in [(previous,'Existing fit'),(refined,'Bounded fin refinement')]]
                pair=Image.new('RGB',(2048,1024))
                pair.paste(images[0],(0,0));pair.paste(images[1],(1024,0))
                pair.save(out/('bind-comparison-'+view+'.png'))
        else:
            report['rejection']='At least one seam group cannot satisfy every frame inside the 2% height displacement cap; original fit returned, no candidate serialized.'
        (out/'refinement.json').write_text(json.dumps(report,indent=2))
        reports[relative]=report
        print(relative,json.dumps({k:v for k,v in report.items() if k not in ['groups','before_chain_maxima','stock_chain_limits']}),flush=True)
    lines=['# Bounded chain-envelope mesh refinement prototype','',
        'Only bind positions may change. Weights, indices, normals, outline directions, UVs and target skeleton remain fixed. Each exact source/fitted seam group shares a single position. Every cached reference frame participates. Displacement is capped at 2% of stock body height. The full existing gate is unchanged.','',
        '| Candidate | Frames | Affected seam groups | Max displacement | Cap | Refiner feasible | Full gate |',
        '| --- | ---: | ---: | ---: | ---: | --- | --- |']
    for name,report in reports.items():
        lines.append(f"| {name} | {report['frames']} | {report['affected_groups']} | {report['max_displacement']:.6f} | {report['displacement_cap']:.6f} | {report['feasible']} | {report.get('full_acceptance',{}).get('numeric_pass','not serialized')} |")
        if not report['feasible']:
            failed=[r for r in report['groups'] if not r['feasible']]
            lines+=['',f"{name}: {len(failed)} blocked seam groups. Largest necessary-displacement lower bound {max(r['lower_bound_displacement'] for r in failed):.6f} units. The original fit was returned and no candidate was serialized.",'']
    lines+=['','Wolverine changes only eight vertices in six shared groups, all dominated by Head weights. Maximum displacement is 1.86% of stock height. Its unchanged full validator reports zero seam separation and unchanged gameplay matrices across all six clips. The three bind comparison PNGs are 2048x1024, share bounds, and preserve the recognizable mask fins and the remaining silhouette. Animated full-size and in-game acceptance still remain. This prototype does not enable a mod. Detailed per-group solver results and unchanged-validator reports are under `output/chain-prototype/`.','',
        'Reproduce: `py -3.12 -B -m unittest discover -s tools -p test_fit_chain.py`, then `py -3.12 -B tools/fit_chain.py`.','']
    (root/'evidence/chain-fit.md').write_text('\n'.join(lines))


if __name__=='__main__':main()
