"""Full-frame deformation measurements and 1024px per-view comparison renders."""
import argparse,json,math
from pathlib import Path
import numpy as np
from PIL import Image
from model import Model,texture,pose
from render import render,triptych
from claws import attach,read_claw
from round1_converter import blob

ROOT=Path(__file__).resolve().parents[1]
REFERENCE_CLIPS=['idle','run','attack_light1','f_heavy_attack_4','power_2','pain_high']

def stock_relative_gate(output):
    """Compare like-for-like body and outline maxima, independently per clip.

    Seam separation must be exactly zero. Attachment round-trip error alone
    allows 1e-10 units for floating point matrix inversion; matrices are exact.
    """
    failures=[]
    if not output.get('gameplay_structure_unchanged',True):failures.append('Port skeleton, palette or bounds changed')
    for name in REFERENCE_CLIPS:
        if name not in output['animations']:
            failures.append(name+': missing reference clip');continue
        row=output['animations'][name]
        for new,old in [('converted','stock'),('converted_outline','stock_outline')]:
            for metric in ['max_edge_ratio','max_chain_distance']:
                values=[row['models'][new][metric],row['models'][old][metric]]
                if not all(math.isfinite(v) for v in values):failures.append(f'{name}: non-finite {metric}')
                elif values[0]>values[1]:failures.append(f'{name}: {new} {metric} exceeds stock')
            if row['models'][new]['max_seam_separation']!=0:failures.append(f'{name}: {new} seam opens')
        if row['hand_head_motion_matrix_delta']!=0:failures.append(name+': gameplay matrix changed')
        if not math.isfinite(row['claw_hand_local_transform_error']) or row['claw_hand_local_transform_error']>1e-10:failures.append(name+': attachment coordinates changed')
    feet=output['animations'].get('idle',{}).get('foot_height_fraction',float('inf'))
    if not math.isfinite(feet) or feet>.02:failures.append('idle: feet differ by more than 2% of stock height')
    return {'numeric_pass':not failures,'failures':failures,'idle_feet_fraction_max':feet,
            'comparison':'body against stock body, outline against stock outline; separately per clip',
            'visual_acceptance':'requires separate full-size offline and native inspection'}

def seam_pairs(model):
    # Exact coincident bind vertices, including UV and normal discontinuities.
    _,inverse=np.unique(model.pos,axis=0,return_inverse=True)
    pairs=[]
    for group in range(inverse.max()+1):
        members=np.flatnonzero(inverse==group)
        pairs.extend((int(members[0]),int(i)) for i in members[1:])
    return np.asarray(pairs,dtype=int).reshape(-1,2)
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

def evaluate(port,source_path,converted,animation_root,output_path,render_root=None,animation_db='x1_03_wolverine.IGB',target_id='14301',claws=True,source_bolton=None):
    stock=Model(port/'Actors'/(target_id+'.IGB'));new=Model(converted);source=Model(source_path)
    hull=Model(converted,target_id+'_outline');stock_hull=Model(port/'Actors'/(target_id+'.IGB'),target_id+'_outline')
    mods={'stock':stock,'converted':new,'converted_outline':hull,'stock_outline':stock_hull}
    if render_root:render_root.mkdir(parents=True,exist_ok=True)
    tex={k:texture(m) for k,m in mods.items()}
    st=texture(source) if source.f.header[11]==0x80000004 else Image.fromarray(np.asarray(texture(source))[:,:,::-1])
    def composite(m,p,w,t,folder):return attach(m,p,w,t,folder) if claws and folder else (m,p,t)
    if render_root:
        cm,cp,ct=composite(source,source.pos,source.world,st,source_bolton)
        triptych(cm,cp,ct,render_root/'source-bind.png','MUA original bind')
        cm,cp,ct=composite(new,new.pos,new.world,tex['converted'],port/'Models/Bolton')
        triptych(cm,cp,ct,render_root/'converted-bind.png','Converted / port bind',True)
    bodyheight=float(np.ptp(stock.pos[:,2]));output={'stock_body_height':bodyheight,'converted_body_height':float(np.ptp(new.pos[:,2])),
        'method':'native sequential inverse-bind LBS; port Enbaya decoder; translation bitmask; 60 Hz plus exact key times',
        'source':str(source_path),'converted':str(converted),'animation_cache':str(animation_root),'animations':{}}
    output['gameplay_structure_unchanged']=(stock.names==new.names and np.array_equal(stock.parent,new.parent)
        and blob(stock.f,stock.sk.get(3))==blob(new.f,new.sk.get(3))
        and blob(stock.f,stock.sk.get(5))==blob(new.f,new.sk.get(5))
        and all([a.get(k) for k in (2,3,4,5)]==[b.get(k) for k in (2,3,4,5)] for a,b in zip(stock.bones,new.bones))
        and np.array_equal(stock.palette,new.palette)
        and [(b.get(2),b.get(3)) for b in stock.f.objects_of('igAABox')]==[(b.get(2),b.get(3)) for b in new.f.objects_of('igAABox')])
    edge={k:edges(m) for k,m in mods.items()};seams={k:seam_pairs(m) for k,m in mods.items()}
    if len(source.pos)!=len(new.pos):raise ValueError('Source correspondence required for seam checks')
    for k in ['converted','converted_outline']:
        seams[k]=np.unique(np.concatenate([seams[k],seam_pairs(source)]),axis=0)
    length={k:np.linalg.norm(m.pos[edge[k][:,0]]-m.pos[edge[k][:,1]],axis=1) for k,m in mods.items()}
    output['coincident_vertex_pairs']={k:len(v) for k,v in seams.items()}
    clips=[(n,'common.IGB' if n=='pain_high' else animation_db) for n in REFERENCE_CLIPS]
    if (animation_root/'reference-map.json').exists():
        mapping=json.loads((animation_root/'reference-map.json').read_text())['clips'];clips=[(n,mapping[n]) for n in REFERENCE_CLIPS]
    for name,db in clips:
        path=animation_root/db/(name+'.npz')
        if not path.exists():continue
        a=np.load(path);qt=a['qt'];stats={k:{'max_edge_ratio':0,'max_chain_distance':0,'max_seam_separation':0,'worst_frame':0} for k in mods}
        foot_difference=0;sample={};sample_world={};claw_gap=0;claw_local_error=0;hand_head_delta=0
        for frame,v in enumerate(qt):
            poses={};pts={}
            for k,m in mods.items():
                world=pose(m,a['names'],v,a['use_translation']);p=m.skin(world);poses[k]=world;pts[k]=p
                e=edge[k];ratio=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)/np.maximum(length[k],1e-9)
                valid=length[k]>1e-6;mx=float(ratio[valid].max())
                if mx>stats[k]['max_edge_ratio']:
                    ix=np.flatnonzero(valid)[np.argmax(ratio[valid])];stats[k].update(max_edge_ratio=mx,worst_frame=frame,worst_edge=e[ix].tolist(),bind_edge_length=float(length[k][ix]))
                stats[k]['max_chain_distance']=max(stats[k]['max_chain_distance'],float(chain_distance(m,p,world).max()))
                s=seams[k]
                if len(s):stats[k]['max_seam_separation']=max(stats[k]['max_seam_separation'],float(np.linalg.norm(p[s[:,0]]-p[s[:,1]],axis=1).max()))
            if name=='idle':
                minima={}
                for k,m in mods.items():
                    footbones=[i for i,n in enumerate(m.names) if ' Foot' in n or ' Toe' in n]
                    mask=np.any(np.isin(m.bone_indices,footbones)&(m.weights>0),axis=1)
                    minima[k]=float(pts[k][mask,2].min())
                foot_difference=max(foot_difference,abs(min(minima['stock'],minima['stock_outline'])-min(minima['converted'],minima['converted_outline'])))
            for bone in ['Bip01 L Hand','Bip01 R Hand','Bip01 Head','Motion']:
                hand_head_delta=max(hand_head_delta,float(np.max(np.abs(poses['converted'][new.names.index(bone)]-poses['stock'][stock.names.index(bone)]))))
            if claws:
                for side in ['left','right']:
                    cp,_=read_claw(port/'Models/Bolton'/('claw_'+side+'.IGB'))
                    hand='Bip01 '+('L' if side=='left' else 'R')+' Hand';hi=new.names.index(hand);w=poses['converted'][hi]
                    world_cp=np.column_stack((cp,np.ones(len(cp))))@w
                    claw_local_error=max(claw_local_error,float(np.max(np.abs((world_cp@np.linalg.inv(w))[:,:3]-cp))))
                    roots=world_cp[cp[:,0]<cp[:,0].min()+.6,:3]
                    handbones=[i for i,n in enumerate(new.names) if n.startswith(hand.replace(' Hand','')) and (' Hand' in n or ' Finger' in n)]
                    hv=pts['converted'][np.any(np.isin(new.bone_indices,handbones)&(new.weights>.05),axis=1)]
                    claw_gap=max(claw_gap,float(np.linalg.norm(roots[:,None,:]-hv[None,:,:],axis=2).min(1).max()))
            if frame==len(qt)//3:sample=pts;sample_world=poses
        for k in stats:stats[k]['chain_fraction_stock_height']=stats[k]['max_chain_distance']/bodyheight
        row={'database':db,'samples':len(qt),'duration':float(a['duration']),'has_exact_key_times':'times_ns' in a,'models':stats,'claw_hand_local_transform_error':claw_local_error,'claw_base_to_hand_vertex_distance_max':claw_gap,'hand_head_motion_matrix_delta':hand_head_delta}
        if name=='idle':row.update(max_foot_difference=foot_difference,foot_height_fraction=foot_difference/bodyheight)
        output['animations'][name]=row
        if render_root:
            composites={k:composite(mods[k],sample[k],sample_world[k],tex[k],port/'Models/Bolton') for k in ['stock','converted']}
            for view in ['front','side','back']:
                ax={'front':(1,2),'back':(1,2),'side':(0,2)}[view];sign=-1 if view in ('front','side') else 1
                combined=np.concatenate([composites['stock'][1][:,ax],composites['converted'][1][:,ax]]);combined[:,0]*=sign
                bounds=(combined.min(0),combined.max(0));pair=Image.new('RGB',(2048,1024))
                sample_time=float(a['times_ns'][len(qt)//3])/1e9 if 'times_ns' in a else float(a['duration'])/3
                for j,k in enumerate(['stock','converted']):
                    cm,cp,ct=composites[k];pair.paste(render(cm,cp,ct,view,f'{k} {name} t={sample_time:.3f}s',width=1024,bounds=bounds,outline=True),(1024*j,0))
                pair.save(render_root/f'{name}-{view}.png')
        print(name,{k:round(s['max_edge_ratio'],4) for k,s in stats.items()},flush=True)
    output['acceptance']=stock_relative_gate(output)
    output_path.parent.mkdir(parents=True,exist_ok=True);output_path.write_text(json.dumps(output,indent=2));print(output['acceptance'],flush=True)
    return output

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--port',type=Path,required=True);ap.add_argument('--mua',type=Path,required=True);ap.add_argument('--converted',type=Path,required=True)
    ap.add_argument('--animations',type=Path,default=ROOT/'evidence/animations')
    ap.add_argument('--target-id',default='14301');ap.add_argument('--source-id',default='0301');ap.add_argument('--animation-db',default='x1_03_wolverine.IGB')
    ap.add_argument('--report',type=Path,default=ROOT/'evidence/validation.json');ap.add_argument('--renders',type=Path,default=ROOT/'renders')
    ap.add_argument('--no-renders',action='store_true');ap.add_argument('--no-claws',action='store_true');args=ap.parse_args()
    source=args.mua if args.mua.is_file() else args.mua/'actors'/(args.source_id+'.igb')
    result=evaluate(args.port,source,args.converted,args.animations,args.report,None if args.no_renders else args.renders,args.animation_db,args.target_id,not args.no_claws,source.parent.parent/'models/bolton')
    if not result['acceptance']['numeric_pass']:raise SystemExit(2)

if __name__=='__main__':main()
