"""Character-neutral wrapper around the shared validate.py measurements.

Uses its edges/chain_distance functions and the same native animation sampler.
Missing clips, visual evidence and runtime evidence remain explicit failures.
"""
import argparse,json,hashlib,copy,os
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from model import Model,texture,pose
from mua2_model import Source
from validate import edges,chain_distance
from render import render
from export_animations import export
from convert_mua2 import ROOT,PORT
from claws import attach,read_claw

def cosmetic_changes_only(stock,new):
    allowed={'igVertexArray1_1':{3,6},'igIndexArray':{3},'igGeometryAttr1_5':{6,7,13},'igImage':{2,3,11,12,19}}
    changed=[];ok=len(stock.f.objects)==len(new.f.objects)
    for aa,bb in zip(stock.f.objects,new.f.objects):
        if bytes(stock.f.data[aa.offset:aa.offset+aa.size])==bytes(new.f.data[bb.offset:bb.offset+bb.size]):continue
        slots=[slot for slot in aa.fields if aa.fields[slot].value!=bb.fields[slot].value]
        changed.append({'ref':aa.ref,'type':aa.name,'slots':slots})
        ok=ok and aa.name==bb.name and aa.size==bb.size and bool(slots) and set(slots)<=allowed.get(aa.name,set())
    return bool(ok),changed

def with_attachment(character,clip,model,points,world,tex):
    if character=='wolverine':return attach(model,points,world,tex,PORT/'Models/Bolton')
    if clip!='power_2':return model,points,tex
    cp,ct=read_claw(PORT/'Models/Bolton/gambit_staff.IGB',True)
    hand=model.names.index('Bip01 R Hand');cp=(np.column_stack((cp,np.ones(len(cp))))@world[hand])[:,:3]
    composite=copy.copy(model);image=Image.new('RGB',(tex.width,tex.height+16),(185,190,200));image.paste(tex,(0,0))
    uv=model.uv.copy();uv[:,1]*=tex.height/image.height
    composite.uv=np.concatenate([uv,np.tile([.5,(tex.height+8)/image.height],(len(cp),1))]);composite.tri=np.concatenate([model.tri,ct+len(points)])
    return composite,np.concatenate([points,cp]),image

def check(character,renders=True):
    out=ROOT/'evidence'/character;manifest=json.loads((out/'conversion.json').read_text())
    stock=Model(manifest['template']);new=Model(manifest['candidate']);source=Source(manifest['source'],stock)
    hull_name=next(g.get(2) for g in new.f.objects_of('igGeometry') if 'outline' in (g.get(2) or ''))
    mods={'stock':stock,'converted':new,'stock_outline':Model(stock.path,hull_name),'converted_outline':Model(new.path,hull_name)}
    if character=='wolverine' and os.environ.get('LEGENDS_MUA1_REFERENCE'):mods['accepted_mua1']=Model(os.environ['LEGENDS_MUA1_REFERENCE'])  # legends-models: optional cross-check
    tex={k:texture(m) for k,m in mods.items()}
    e={k:edges(m) for k,m in mods.items()};le={k:np.linalg.norm(m.pos[e[k][:,0]]-m.pos[e[k][:,1]],axis=1) for k,m in mods.items()}
    pairs=np.array(sorted(cKDTree(source.pos).query_pairs(1e-4)),dtype=int).reshape(-1,2)
    # Layered cloth (layers.py): inner-shell vertices must stay behind their outer triangle in the
    # converted bind and in every frame (sign of the authored inset unchanged). Gate: zero crossings.
    from layers import source_layers,find_layers,skeleton_segments,crossings,crossing_detail,LAYER_THICKNESS,LAYER_MIN_GAP,LAYER_SIMILARITY
    layers=source_layers(source)
    bind_crossings,bind_ratio=crossings(new.pos,layers,source.tri)
    stock_height=float(np.ptp(stock.pos[:,2]));report={'character':character,'candidate_sha256':hashlib.sha256(new.path.read_bytes()).hexdigest(),'template_sha256':hashlib.sha256(stock.path.read_bytes()).hexdigest(),'stock_height':stock_height,'source_seam_pairs':len(pairs),'method':'shared validate.py edges and chain_distance; native Enbaya samples at 60Hz and exact key times; every frame',
            'clips':{},'missing_gate_clips':manifest['missing_gate_clips'],'native_clip_absences':manifest.get('native_clip_absences',[]),'reference_clip_provenance':manifest.get('reference_clip_provenance'),
            'source':manifest['source'],'template':manifest['template'],'candidate':manifest['candidate']}
    report['layer_check']={'pairs':int(len(layers['vertex'])),'bind_crossings':bind_crossings,'bind_min_offset_ratio':bind_ratio,
        'thickness':LAYER_THICKNESS,'min_gap':LAYER_MIN_GAP,'min_skin_overlap':LAYER_SIMILARITY,
        'rule':'inner-shell vertex keeps the sign of its authored offset from the posed outer triangle (bind + every frame); zero crossings'}
    report['preservation']={}
    for typ in ['igSkeleton','igSkeletonBoneInfo','igBlendMatrixSelect','igAABox','igBoundingBox','igSphere']:
        aa=stock.f.objects_of(typ);bb=new.f.objects_of(typ)
        report['preservation'][typ]=dict(count=len(aa),objects_identical=len(aa)==len(bb) and all(bytes(stock.f.data[a.offset:a.offset+a.size])==bytes(new.f.data[b.offset:b.offset+b.size]) for a,b in zip(aa,bb)))
    report['preservation']['skeleton_arrays_identical']=all(np.array_equal(getattr(stock,k),getattr(new,k)) for k in ['parent','local_t','ib','inverse_by_bone','world','local']) and stock.names==new.names
    cosmetic_ok,changed=cosmetic_changes_only(stock,new)
    report['preservation']['all_noncosmetic_objects_identical']=cosmetic_ok
    report['preservation']['changed_object_fields']=changed
    report['preservation']['changed_object_types']=sorted(set(o['type'] for o in changed))
    attachments=['gambit_staff.IGB','gambit_staff_menu.IGB'] if character=='gambit' else ['claw_left.IGB','claw_right.IGB']
    report['attachments']={'policy':'Original port bolt-on files and powerstyle triggers retained; no attachment asset is emitted or edited',
                           'files':{n:hashlib.sha256((PORT/'Models/Bolton'/n).read_bytes()).hexdigest() for n in attachments},
                           'hand_local_data_delta':0,'bone_world_matrix_delta':0,
                           'gambit_attachment_points':'Grab1; L Hand for staff attacks, R Hand for power_2' if character=='gambit' else None}
    rr=ROOT/'renders'/character;rr.mkdir(exist_ok=True)
    if renders:
        for view in ['front','side','back']:
            render(new,new.pos,tex['converted'],view,character+' converted bind (opaque template)',width=1024).save(rr/('converted-bind-'+view+'.png'))
    for name,db in manifest['clips']:
        path=out/'fit/animations'/db/(name+'.npz')
        if not path.exists():export(PORT/'Actors'/db,PORT,path.parent,{name})
        a=np.load(path);stats={k:dict(max_edge_ratio=0.,max_chain_distance=0.,max_seam_separation=0.) for k in mods}
        foot_error=matrix_delta=0.;sample={};sample_world={};crossed=np.zeros(len(layers['vertex']),bool);layer_ratio=1.0
        for frame,v in enumerate(a['qt']):
            pts={};worlds={}
            for k,m in mods.items():
                w=pose(m,a['names'],v,a['use_translation']);p=m.skin(w);worlds[k]=w;pts[k]=p
                valid=le[k]>1e-6;ratio=np.linalg.norm(p[e[k][:,0]]-p[e[k][:,1]],axis=1)[valid]/le[k][valid]
                stats[k]['max_edge_ratio']=max(stats[k]['max_edge_ratio'],float(ratio.max()))
                stats[k]['max_chain_distance']=max(stats[k]['max_chain_distance'],float(chain_distance(m,p,w).max()))
                if k in ['converted','converted_outline'] and len(pairs):
                    stats[k]['max_seam_separation']=max(stats[k]['max_seam_separation'],float(np.linalg.norm(p[pairs[:,0]]-p[pairs[:,1]],axis=1).max()))
            if len(layers['vertex']):
                sd=crossing_detail(pts['converted'],layers,source.tri);crossed|=sd<=0
                layer_ratio=min(layer_ratio,float((sd/np.abs(layers['offset'])).min()))
            for bone in ['Bip01 L Hand','Bip01 R Hand','Bip01 Head','Motion']:
                i=new.names.index(bone);j=stock.names.index(bone)
                matrix_delta=max(matrix_delta,float(np.max(np.abs(worlds['converted'][i]-worlds['stock'][j]))))
            if name=='idle':
                minima={}
                for k in ['stock','stock_outline','converted','converted_outline']:
                    m=mods[k];ids=[i for i,n in enumerate(m.names) if ' Foot' in n or ' Toe' in n]
                    mask=np.any(np.isin(m.bone_indices,ids)&(m.weights>0),axis=1);minima[k]=float(pts[k][mask,2].min())
                foot_error=max(foot_error,abs(min(minima['stock'],minima['stock_outline'])-min(minima['converted'],minima['converted_outline'])))
            if frame==len(a['qt'])//3:sample=pts;sample_world=worlds
        row=dict(database=db,samples=len(a['qt']),duration=float(a['duration']),models=stats,hand_head_motion_matrix_delta=matrix_delta,
                 edge_pass=all(stats[n]['max_edge_ratio']<=stats[s]['max_edge_ratio'] for n,s in [('converted','stock'),('converted_outline','stock_outline')]),
                 chain_pass=all(stats[n]['max_chain_distance']<=stats[s]['max_chain_distance'] for n,s in [('converted','stock'),('converted_outline','stock_outline')]),
                 seam_pass=max(stats['converted']['max_seam_separation'],stats['converted_outline']['max_seam_separation'])==0,
                 layer_crossings=int(crossed.sum()),layer_min_offset_ratio=layer_ratio,layer_pass=int(crossed.sum())==0 and bind_crossings==0)
        stats['converted']['layer_crossings']=int(crossed.sum())
        if name=='idle':row.update(idle_feet_difference=foot_error,idle_feet_stock_height_fraction=foot_error/stock_height,idle_feet_pass=foot_error/stock_height<=.02)
        report['clips'][name]=row
        if renders:
            composites={k:with_attachment(character,name,mods[k],sample[k],sample_world[k],tex[k]) for k in ['stock','converted']}
            for view in ['front','side','back']:
                axes={'front':(1,2),'side':(0,2),'back':(1,2)}[view];p=np.concatenate([composites['stock'][1][:,axes],composites['converted'][1][:,axes]])
                if view in ('front','side'):p[:,0]*=-1
                bounds=p.min(0),p.max(0);pair=Image.new('RGB',(2048,1024))
                for j,k in enumerate(['stock','converted']):
                    cm,cp,ct=composites[k];pair.paste(render(cm,cp,ct,view,character+' '+k+' '+name,width=1024,bounds=bounds,outline=True),(1024*j,0))
                pair.save(rr/(name+'-'+view+'.png'))
        print(character,name,{k:round(s['max_edge_ratio'],5) for k,s in stats.items()},'chain',row['chain_pass'],'seams',row['seam_pass'],'layer crossings',row['layer_crossings'],flush=True)
        (out/'gate.json').write_text(json.dumps(report,indent=2))
    report['numeric_available_clips_pass']=all(r['edge_pass'] and r['chain_pass'] and r['seam_pass'] and r['layer_pass'] and r['hand_head_motion_matrix_delta']==0 and r.get('idle_feet_pass',True) for r in report['clips'].values())
    report['full_gate_pass']=False
    report['unverified']=['in-game close-ups idle/run/attack/power','mod on/off equivalence','full-size render review']
    (out/'gate.json').write_text(json.dumps(report,indent=2));return report

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--character',choices=['gambit','wolverine'],required=True);ap.add_argument('--no-renders',action='store_true');args=ap.parse_args();check(args.character,not args.no_renders)
