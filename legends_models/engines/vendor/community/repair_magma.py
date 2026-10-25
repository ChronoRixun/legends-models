"""Restore authored Magma hand/cuff overlap influences without welding shells.

Diagnostic prototype only. All outputs stay under output/magma-repaired.
"""
import copy,json,sys,shutil,hashlib
from pathlib import Path
import numpy as np
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
import community_convert
from community_model import Model
from convert import refit
from validate import edges


def pad4(weights,indices):
    weights=np.asarray(weights);indices=np.asarray(indices,dtype=np.uint8)
    if weights.shape!=indices.shape or weights.ndim!=2 or not 1<=weights.shape[1]<=4:
        raise ValueError('expected matching one-to-four-lane influences')
    count=4-weights.shape[1]
    return np.pad(weights,((0,0),(0,count))),np.pad(indices,((0,0),(0,count)),mode='edge')


def dense_weights(weights,indices,count):
    dense=np.zeros((len(weights),count))
    for i in range(len(weights)):np.add.at(dense[i],indices[i],weights[i])
    return dense


def wrist_components(source):
    """Select disconnected components whose authored mass is hand/finger/forearm."""
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    e=edges(source);near=np.array(list(cKDTree(source.pos).query_pairs(1e-4)),dtype=int).reshape(-1,2)
    e=np.concatenate([e,near]);count,groups=connected_components(coo_matrix(
        (np.ones(len(e)),(e[:,0],e[:,1])),shape=(len(source.pos),len(source.pos))),directed=False)
    wrist=np.array([any(part in name for part in [' Hand',' Finger',' Forearm']) for name in source.names])
    selected=[];description=[]
    for group in range(count):
        ids=np.flatnonzero(groups==group)
        mass=float(np.sum(source.weights[ids]*wrist[source.bone_indices[ids]]))
        if mass/len(ids)>=.90:
            selected.extend(ids.tolist());description.append({'component':group,'vertices':len(ids),'wrist_mass_fraction':mass/len(ids)})
    if not selected:raise ValueError('no disconnected authored wrist components')
    return np.array(selected,dtype=int),description


def repair(source,target,fit,mode='localized'):
    """Return a fresh fit dict; never alter input assets or fit arrays."""
    if mode not in ['authored','localized']:raise ValueError('unknown repair mode')
    pos,norm,weights,indices,_=refit(source,target)
    weights,indices=pad4(weights,indices)
    output={key:np.array(value,copy=True) for key,value in fit.items()}
    selected,description=wrist_components(source)
    if mode=='authored':selected=np.arange(len(pos))
    # Exact source-coincident groups must share their position and dense influence
    # vector. Disjoint cuff/hand rings 0.3 units apart are deliberately not joined.
    _,groups=np.unique(source.pos,axis=0,return_inverse=True)
    selected=np.flatnonzero(np.isin(groups,np.unique(groups[selected])))
    output['pos'][selected]=pos[selected];output['norm'][selected]=norm[selected]
    output['weights'],output['indices']=pad4(output['weights'],output['indices'])
    output['weights'][selected]=weights[selected];output['indices'][selected]=indices[selected]
    dense=dense_weights(weights,indices,len(target.palette))
    for group in np.unique(groups[selected]):
        members=np.flatnonzero(groups==group)
        averaged=dense[members].mean(0)
        order=np.flatnonzero(averaged>0)
        if len(order)>4:raise ValueError('seam group needs more than four authored influences')
        order=order[np.argsort(averaged[order])[::-1]]
        output['pos'][members]=pos[members].mean(0)
        output['weights'][members]=0;output['indices'][members]=order[0]
        for lane,index in enumerate(order):
            output['weights'][members,lane]=averaged[index]
            output['indices'][members,lane]=index
    report={'mode':mode,'restored_vertices':len(selected),'restored_indices':selected.tolist(),
        'wrist_components':description,'max_position_change_from_round2':float(np.linalg.norm(output['pos']-fit['pos'],axis=1).max()),
        'source_shell_separation_preserved':True,'target_skeleton_modified':False,
        'outline_directions':'retained from existing shared-group round-2 outline normals'}
    return output,report


def main():
    import validate
    from fit_chain import refine
    port=Path(__import__('os').environ['LEGENDS_PORT'])
    oldwork=ROOT/'output/magma/ultimate-xml1'
    conversion=json.loads((oldwork/'conversion.json').read_text())
    source_path=ROOT/conversion['source'];template=Path(conversion['template'])
    source,target=Model(source_path),Model(template)
    previous=dict(np.load(oldwork/'fit.npz'));animations=oldwork/'animations'
    db=json.loads((animations/'reference-map.json').read_text())['hero_db']
    dest=ROOT/'output/magma-repaired';dest.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'CREDITS.md',dest/'CREDITS.md')
    attempts=[];chosen=None
    for mode in ['authored','localized']:
        fitted,report=repair(source,target,previous,mode=mode)
        out=dest/mode;out.mkdir(parents=True,exist_ok=True)
        candidate=out/'15801.IGB'
        summary=community_convert.convert_candidate(source_path,template,candidate,fitted)
        validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
            render_root=None,animation_db=db,target_id='15801',claws=False)
        report.update(conversion=summary,initial_acceptance=validation['acceptance'])
        failures=validation['acceptance']['failures']
        if failures and all('max_chain_distance' in failure for failure in failures):
            # Explicit 2.5% cosmetic bound authorized for this diagnostic pass.
            fitted,chain=refine(source,target,fitted,port,animations,db,max_displacement_fraction=.025)
            report['chain_refinement']=chain
            if chain['feasible']:
                summary=community_convert.convert_candidate(source_path,template,candidate,fitted)
                validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
                    render_root=None,animation_db=db,target_id='15801',claws=False)
                report['conversion']=summary
        report['final_acceptance']=validation['acceptance']
        np.savez(out/'fit.npz',**fitted)
        if validation['acceptance']['numeric_pass']:
            # Produce the complete required 20 PNG set using the unchanged validator.
            validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
                render_root=out/'renders',animation_db=db,target_id='15801',claws=False)
            report['final_acceptance']=validation['acceptance'];chosen=out
        (out/'repair.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        attempts.append(report)
        print('Magma repair',mode,validation['acceptance'],flush=True)
        if chosen is not None:break
    lines=['# Magma authored wrist overlap repair','',
        'The source uses separate cuff/hand shells with noncoincident overlapping boundary rings. Independent smoothing introduced finger weights at the hand root while the cuff stayed on hand/forearm weights. This tool restores authored influences and bind refit, shares exact source seam groups, and never welds the separated shell surfaces.','',
        '| Attempt | Restored vertices | Max bind position change | Numeric pass |','| --- | ---: | ---: | --- |']
    for report in attempts:
        lines.append(f"| {report['mode']} | {report['restored_vertices']} | {report['max_position_change_from_round2']:.6f} | {report['final_acceptance']['numeric_pass']} |")
        lines+=['',json.dumps(report['final_acceptance'],indent=2),'']
    if chosen is not None:
        lines+=['',f"Passing candidate: `{chosen.relative_to(ROOT).as_posix()}/15801.IGB`.",
            f"SHA256: `{hashlib.sha256((chosen/'15801.IGB').read_bytes()).hexdigest()}`.",
            'The unchanged full validator checked all reference frames, body and outline, exact seams, feet and gameplay matrices. Twenty 1024px-tall renders are saved in its renders subfolder. Visual review remains pending; native runtime acceptance remains pending.','']
    else:lines+=['','No passing candidate was produced. Existing candidates were not changed.','']
    lines+=['','Reproduce: `py -3.12 -B -m unittest discover -s tools -p test_repair_magma.py`; `py -3.12 -B tools/repair_magma.py`. No game or network is used.','']
    (ROOT/'evidence/magma-repair.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
