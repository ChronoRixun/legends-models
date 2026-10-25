"""Roster adapter for the round-2 validator's exact LBS/edge/chain metrics.

All six named references are measured. Missing named character clips use the
common/style clip, then Wolverine's explicit reference as a documented stress
test. This does not assert the character uses that stress clip in gameplay.
"""
import argparse,json,struct,time,hashlib,copy
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from PIL import Image
from model import Model,texture,pose
from igb_file import IgbFile
from round1_converter import blob
from convert_xml2 import outline_model
from validate import edges,chain_distance
from render import render,triptych
from export_animations import export

CLIPS=['idle','run','attack_light1','f_heavy_attack_4','power_2','pain_high']
ROOT=Path(__file__).resolve().parents[1]

def clips(port,row,work):
    files=[row['animation_db']+'.IGB']+[s+'.IGB' for s in row.get('fightstyles',[])]+['common.IGB','fightstyle_hero.IGB','x1_03_wolverine.IGB']
    found={};requests={}
    for db in files:
        if not (port/'Actors'/db).exists():continue
        f=IgbFile((port/'Actors'/db).read_bytes());names={o.get(2) for o in f.objects_of('igAnimation')}
        for name in CLIPS:
            if name not in found and name in names:found[name]=db;requests.setdefault(db,set()).add(name)
    if set(found)!=set(CLIPS):raise ValueError('Reference clips unavailable')
    for db,names in requests.items():
        out=work/db
        missing={n for n in names if not (out/(n+'.npz')).exists()}
        if missing:export(port/'Actors'/db,port,out,missing)
    return {name:work/found[name]/(name+'.npz') for name in CLIPS}

def meshes(path):
    f=IgbFile(Path(path).read_bytes());out=[]
    for ga in f.objects_of('igGeometryAttr'):
        m=Model(path,'@'+str(ga.ref))
        if len(m.tri):out.append(m)
    return out

def render_mesh(main,models,points):
    """Include separate costume parts, such as the stock Cyclops visor."""
    result=copy.copy(main);positions=[];uv=[];triangles=[];offset=0
    for model,p in zip(models,points):
        if model.uv is None or model.norm is None:continue
        positions.append(p);uv.append(model.uv);triangles.append(model.tri+offset);offset+=len(p)
    result.pos=np.concatenate(positions);result.uv=np.concatenate(uv);result.tri=np.concatenate(triangles)
    return result

def check_preserved(stock,new):
    fs,fn=stock.f,new.f
    names=['igSkeleton','igSkeletonBoneInfo','igAnimation','igBoundingBox','igBoundingSphere','igAABox']
    result={}
    for name in names:
        a=fs.objects_of(name);b=fn.objects_of(name)
        if len(a)!=len(b):raise AssertionError(name+' count differs')
        for x,y in zip(a,b):
            if {s:v.value for s,v in x.fields.items()}!={s:v.value for s,v in y.fields.items()}:raise AssertionError(name+' fields differ')
        result[name]=len(a)
    for slot in [3,5]:
        if blob(fs,stock.sk.get(slot))!=blob(fn,new.sk.get(slot)):raise AssertionError('Skeleton bind/translation block differs')
    if not np.array_equal(stock.world,new.world) or not np.array_equal(stock.local_t,new.local_t):raise AssertionError('Skeleton matrices differ')
    allowed_fields=set();allowed_blocks=set()
    for ga in fs.objects_of('igGeometryAttr'):
        allowed_fields.update((ga.ref,s) for s in [6,7,13])
        va,ia=fs.obj(ga.get(4)),fs.obj(ga.get(5))
        allowed_fields.update((va.ref,s) for s in [3,6]);allowed_fields.add((ia.ref,3));allowed_blocks.add(ia.get(2))
        indirect=struct.unpack('<20i',blob(fs,va.get(2)))
        allowed_blocks.update(r for r in indirect if r>=0)
        allowed_blocks.update([va.get(7),va.get(8)])
    for im in fs.objects_of('igImage'):
        allowed_fields.update((im.ref,s) for s in [2,3,12,19]);allowed_blocks.add(im.get(13))
    for ml in fs.objects_of('igImageMipMapList'):
        allowed_fields.update((ml.ref,s) for s in [2,3,4]);allowed_blocks.add(ml.get(4))
    for ta in fs.objects_of('igTextureAttr'):allowed_fields.update((ta.ref,s) for s in [15,16])
    comparisons=0
    for obj in fs.objects:
        n=fn.obj(obj.ref)
        if n is None or n.name!=obj.name:raise AssertionError('Object identity changed')
        for slot,field in obj.fields.items():
            if (obj.ref,slot) not in allowed_fields:
                if field.value!=n.get(slot):raise AssertionError(f'Noncosmetic field changed {obj.name}:{obj.ref}:{slot}')
                comparisons+=1
    for ref in fs.blocks:
        if ref not in allowed_blocks:
            if blob(fs,ref)!=blob(fn,ref):raise AssertionError(f'Noncosmetic memory block changed {ref}')
            comparisons+=1
    for obj in fn.objects:
        if obj.ref>=len(fs.index) and obj.name not in ['igImage','igImageMipMapList']:raise AssertionError('Added nontexture object')
    return dict(unchanged_object_counts=result,skeleton_and_bind_bytes_identical=True,noncosmetic_fields_and_blocks_identical=True,exact_comparisons=comparisons)

def measure(port,row,candidate,out,renders=False):
    name=row['character'];out=Path(out);out.mkdir(parents=True,exist_ok=True)
    paths=clips(port,row,ROOT/'cache/animations');stockpath=port/'Actors'/(row['target']+'.IGB')
    sm,nm=Model(stockpath),Model(candidate);invariant=check_preserved(sm,nm)
    models={'stock':meshes(stockpath),'converted':meshes(candidate)}
    prep={}
    for label,ms in models.items():
        prep[label]=[]
        for m in ms:
            e=edges(m);le=np.linalg.norm(m.pos[e[:,0]]-m.pos[e[:,1]],axis=1)
            seams=np.array(list(cKDTree(m.pos).query_pairs(1e-9)),dtype=int).reshape(-1,2)
            prep[label].append((e,le,seams))
    bodyheight=float(np.ptp(sm.pos[:,2]));result=dict(character=name,stock=str(stockpath),candidate=str(candidate),stock_sha256=hashlib.sha256(stockpath.read_bytes()).hexdigest(),candidate_sha256=hashlib.sha256(Path(candidate).read_bytes()).hexdigest(),source=row['source'],height=bodyheight,invariant=invariant,clips={},errors=[])
    renderdir=ROOT/'renders/xml2'/name;renderdir.mkdir(parents=True,exist_ok=True)
    if renders:
        source=Model(port/'Actors'/(row['source']+'.IGB'))
        if name=='wolverine':
            from claws import attach
            for label,m in [('source',source),('converted',nm)]:
                cm,cp,ct=attach(m,m.pos,m.world,texture(m),port/'Models/Bolton')
                triptych(cm,cp,ct,renderdir/(label+'-bind.png'),name+' '+label+' bind / retained port claws')
        else:
            triptych(source,source.pos,texture(source),renderdir/'source-bind.png',name+' XML2 source bind')
            triptych(nm,nm.pos,texture(nm),renderdir/'converted-bind.png',name+' converted bind')
    for clip,path in paths.items():
        a=np.load(path);stats={k:dict(max_edge_ratio=0.,max_chain_distance=0.,max_seam_separation=0.) for k in models};matrix_delta=0.;feet_delta=0.;sample={};sample_world={}
        for frame,qt in enumerate(a['qt']):
            allpts={};allworld={};feet={}
            for label,ms in models.items():
                pts=[];worlds=[];feet_values=[]
                for mi,(m,(e,le,seams)) in enumerate(zip(ms,prep[label])):
                    world=pose(m,a['names'],qt,a['use_translation']);p=m.skin(world);pts.append(p);worlds.append(world)
                    valid=le>1e-6;ratio=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)/np.maximum(le,1e-12)
                    if np.any(valid):stats[label]['max_edge_ratio']=max(stats[label]['max_edge_ratio'],float(ratio[valid].max()))
                    stats[label]['max_chain_distance']=max(stats[label]['max_chain_distance'],float(chain_distance(m,p,world).max()))
                    if len(seams):stats[label]['max_seam_separation']=max(stats[label]['max_seam_separation'],float(np.linalg.norm(p[seams[:,0]]-p[seams[:,1]],axis=1).max()))
                    if clip=='idle':
                        footbones=[i for i,n in enumerate(m.names) if ' Foot' in n or ' Toe' in n]
                        mask=np.any(np.isin(m.bone_indices,footbones)&(m.weights>0),axis=1)
                        if mask.any():feet_values.append(float(p[mask,2].min()))
                allpts[label]=pts;allworld[label]=worlds
                if feet_values:feet[label]=min(feet_values)
            if len(feet)==2:feet_delta=max(feet_delta,abs(feet['stock']-feet['converted']))
            for bone in ['Bip01 L Hand','Bip01 R Hand','Bip01 Head','Motion']:
                if bone in sm.names and bone in nm.names:
                    matrix_delta=max(matrix_delta,float(np.abs(allworld['stock'][0][sm.names.index(bone)]-allworld['converted'][0][nm.names.index(bone)]).max()))
            if frame==len(a['qt'])//3:sample=allpts;sample_world=allworld
        s,n=stats['stock'],stats['converted'];checks=dict(edge=n['max_edge_ratio']<=s['max_edge_ratio'],chain=n['max_chain_distance']<=s['max_chain_distance'],seam=n['max_seam_separation']==0.,matrices=matrix_delta==0.)
        if clip=='idle':checks['feet']=feet_delta/bodyheight<=.02
        r=dict(reference_db=path.parent.name,samples=len(a['qt']),models=stats,hand_head_motion_matrix_delta=matrix_delta,idle_feet_fraction=feet_delta/bodyheight,checks=checks,pass_numeric=all(checks.values()))
        result['clips'][clip]=r
        print(name,clip,'PASS' if r['pass_numeric'] else 'FAIL',json.dumps({k:round(v,5) for k,v in n.items()}),checks,flush=True)
        if renders:
            # Every view is 1024 pixels tall, at the same stock/new scale.
            composites={}
            for label,main in [('stock',sm),('converted',nm)]:
                composite=render_mesh(main,models[label],sample[label]);tex=texture(main)
                if name=='wolverine':
                    from claws import attach
                    composite,cp,tex=attach(composite,composite.pos,sample_world[label][0],tex,port/'Models/Bolton');composite.pos=cp
                composites[label]=(composite,tex)
            for view in ['front','side','back']:
                ax={'front':(1,2),'back':(1,2),'side':(0,2)}[view];sign=-1 if view in ['front','side'] else 1
                comb=np.concatenate([m.pos[:,ax] for m,tex in composites.values()]);comb[:,0]*=sign;bounds=(comb.min(0),comb.max(0))
                pair=Image.new('RGB',(2048,1024))
                for j,(label,(composite,tex)) in enumerate(composites.items()):
                    pair.paste(render(composite,composite.pos,tex,view,f'{name} {label} {clip}',width=1024,bounds=bounds,outline=True),(j*1024,0))
                pair.save(renderdir/(clip+'-'+view+'.png'))
    result['pass_numeric']=all(r['pass_numeric'] for r in result['clips'].values())
    result['accepted']=False;result['remaining_gate']='Full-size visual review, attachments and in-game on/off verification still required'
    (out/(name+'.json')).write_text(json.dumps(result,indent=2));return result

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--port',type=Path,required=True);ap.add_argument('--character',required=True);ap.add_argument('--candidate',type=Path,required=True);ap.add_argument('--renders',action='store_true');a=ap.parse_args()
    row=json.loads((ROOT/'evidence/inventory.json').read_text())[a.character]
    measure(a.port,row,a.candidate,ROOT/'evidence/gates',a.renders)

if __name__=='__main__':main()
