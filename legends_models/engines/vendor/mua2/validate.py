"""Full-frame deformation measurements and 1024px per-view comparison renders."""
import argparse,json,os
from pathlib import Path
import numpy as np
from PIL import Image
from model import Model,texture,pose
from render import render,triptych
from claws import attach,read_claw

ROOT=Path(__file__).resolve().parents[1]
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

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--mua',type=Path,required=True);ap.add_argument('--converted',type=Path,required=True)
    ap.add_argument('--animations',type=Path,help='Decoded animation cache; defaults to the converted mod cache when present')
    ap.add_argument('--no-renders',action='store_true');args=ap.parse_args()
    stock=Model(args.port/'Actors/14301.IGB');new=Model(args.converted);source=Model(args.mua/'actors/0301.igb')
    old=Model(os.environ['LEGENDS_ROUND1_PROTOTYPE'])  # legends-models: research-only standalone entry point
    candidate=args.converted.resolve().parents[3]/'.mua-converter-cache/quality/animations'
    animation_root=args.animations or (candidate if candidate.exists() else ROOT/'evidence/animations')
    hull=Model(args.converted,'14301_outline');stock_hull=Model(args.port/'Actors/14301.IGB','14301_outline')
    mods={'stock':stock,'converted':new,'round1':old,'converted_outline':hull,'stock_outline':stock_hull}
    tex={k:texture(m) for k,m in mods.items()}
    st=texture(source);st=Image.fromarray(np.asarray(st)[:,:,::-1])
    if not args.no_renders:
        cm,cp,ct=attach(source,source.pos,source.world,st,args.mua/'models/bolton')
        triptych(cm,cp,ct,ROOT/'renders/mua2006-bind-reference.png','MUA 2006 original bind / extended claws')
        cm,cp,ct=attach(new,new.pos,new.world,tex['converted'],args.port/'Models/Bolton')
        triptych(cm,cp,ct,ROOT/'renders/converted-bind.png','Converted / port bind / stock claws',True)
    bodyheight=float(np.ptp(new.pos[:,2]));output={'body_height':bodyheight,'method':'native sequential inverse-bind LBS; port Enbaya decoder; explicit translation bitmask; 60 Hz plus exact compressed/uncompressed key times when present','animation_cache':str(animation_root),'animations':{}}
    edge={k:edges(m) for k,m in mods.items()}
    # Readable spelling for the nondegenerate bind edges used by the hard gate.
    length={k:np.linalg.norm(m.pos[edge[k][:,0]]-m.pos[edge[k][:,1]],axis=1) for k,m in mods.items()}
    clips=[('idle','x1_03_wolverine.IGB'),('run','x1_03_wolverine.IGB'),('attack_light1','x1_03_wolverine.IGB'),('f_heavy_attack_4','x1_03_wolverine.IGB'),('power_2','x1_03_wolverine.IGB'),('pain_high','common.IGB')]
    for name,db in clips:
        a=np.load(animation_root/db/(name+'.npz'));qt=a['qt'];stats={k:{'max_edge_ratio':0,'max_chain_distance':0,'worst_frame':0} for k in mods}
        foot_difference=0;sample={};sample_world={};claw_gap=0;claw_local_error=0;hand_head_delta=0
        for frame,v in enumerate(qt):
            poses={};pts={}
            for k,m in mods.items():
                world=pose(m,a['names'],v,a['use_translation']);p=m.skin(world);poses[k]=world;pts[k]=p
                e=edge[k];ratio=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)/np.maximum(length[k],1e-9)
                # True zero-length edges cannot stretch; coincident UV seam vertices
                # may have unequal weights, so list their absolute separation too.
                valid=length[k]>1e-6;mx=float(ratio[valid].max())
                if mx>stats[k]['max_edge_ratio']:
                    ix=np.flatnonzero(valid)[np.argmax(ratio[valid])];stats[k].update(max_edge_ratio=mx,worst_frame=frame,worst_edge=e[ix].tolist(),bind_edge_length=float(length[k][ix]))
                dist=chain_distance(m,p,world);stats[k]['max_chain_distance']=max(stats[k]['max_chain_distance'],float(dist.max()))
            if name=='idle':
                minima={}
                for k in ['stock','converted','stock_outline','converted_outline']:
                    m=mods[k];footbones=[i for i,n in enumerate(m.names) if ' Foot' in n or ' Toe' in n]
                    mask=np.any(np.isin(m.bone_indices,footbones)&(m.weights>0),axis=1)
                    minima[k]=float(pts[k][mask,2].min())
                foot_difference=max(foot_difference,abs(min(minima['stock'],minima['stock_outline'])-min(minima['converted'],minima['converted_outline'])))
            for bone in ['Bip01 L Hand','Bip01 R Hand','Bip01 Head','Motion']:
                hand_head_delta=max(hand_head_delta,float(np.max(np.abs(poses['converted'][new.names.index(bone)]-poses['stock'][stock.names.index(bone)]))))
            for side in ['left','right']:
                cp,_=read_claw(args.port/'Models/Bolton'/('claw_'+side+'.IGB'))
                hand='Bip01 '+('L' if side=='left' else 'R')+' Hand';hi=new.names.index(hand);w=poses['converted'][hi]
                world_cp=np.column_stack((cp,np.ones(len(cp))))@w
                claw_local_error=max(claw_local_error,float(np.max(np.abs((world_cp@np.linalg.inv(w))[:,:3]-cp))))
                roots=world_cp[cp[:,0]<cp[:,0].min()+.6,:3]
                handbones=[i for i,n in enumerate(new.names) if n.startswith(hand.replace(' Hand','')) and (' Hand' in n or ' Finger' in n)]
                hv=pts['converted'][np.any(np.isin(new.bone_indices,handbones)&(new.weights>.05),axis=1)]
                d=np.linalg.norm(roots[:,None,:]-hv[None,:,:],axis=2).min(1)
                claw_gap=max(claw_gap,float(d.max()))
            if frame==len(qt)//3:sample=pts;sample_world=poses
        for k in stats:stats[k]['chain_fraction']=stats[k]['max_chain_distance']/bodyheight
        row={'samples':len(qt),'duration':float(a['duration']),'has_exact_key_times':'times_ns' in a,'models':stats,'claw_hand_local_transform_error':claw_local_error,'claw_base_to_hand_vertex_distance_max':claw_gap,'hand_head_motion_matrix_delta':hand_head_delta}
        if name=='idle':row.update(max_foot_difference=foot_difference,foot_height_fraction=foot_difference/bodyheight)
        output['animations'][name]=row
        if not args.no_renders:
            composites={k:attach(mods[k],sample[k],sample_world[k],tex[k],args.port/'Models/Bolton') for k in ['stock','converted']}
            for view in ['front','side','back']:
                # Same camera scale and framing for the stock/new pair.
                ax={'front':(1,2),'back':(1,2),'side':(0,2)}[view];sign=-1 if view in ('front','side') else 1
                combined=np.concatenate([composites['stock'][1][:,ax],composites['converted'][1][:,ax]]);combined[:,0]*=sign
                bounds=(combined.min(0),combined.max(0))
                pair=Image.new('RGB',(2048,1024))
                sample_time=float(a['times_ns'][len(qt)//3])/1e9 if 'times_ns' in a else float(a['duration'])/3
                for j,k in enumerate(['stock','converted']):
                    cm,cp,ct=composites[k]
                    pair.paste(render(cm,cp,ct,view,f'{k} {name} t={sample_time:.3f}s',width=1024,bounds=bounds,outline=True),(1024*j,0))
                pair.save(ROOT/f'renders/{name}-{view}.png')
        print(name,{k:round(s['max_edge_ratio'],4) for k,s in stats.items()},flush=True)
    output['acceptance']={'edge_ratio_max':max(r['models'][k]['max_edge_ratio'] for r in output['animations'].values() for k in ['converted','converted_outline']),
                          'body_edge_ratio_max':max(r['models']['converted']['max_edge_ratio'] for r in output['animations'].values()),
                          'outline_edge_ratio_max':max(r['models']['converted_outline']['max_edge_ratio'] for r in output['animations'].values()),
                          'chain_fraction_max':max(r['models'][k]['chain_fraction'] for r in output['animations'].values() for k in ['converted','converted_outline']),
                          'idle_feet_fraction_max':output['animations']['idle']['foot_height_fraction']}
    (ROOT/'evidence/validation.json').write_text(json.dumps(output,indent=2));print(output['acceptance'])

if __name__=='__main__':main()
