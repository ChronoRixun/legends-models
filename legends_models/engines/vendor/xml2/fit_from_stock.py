"""Seed a round-2 fit from nearby stock skin weights for disconnected clothing.

This is a real weight transfer, not tiny auxiliary weights added for a metric.
The four stock influences nearest each target-bind donor point seed the same
seam-welded animation solver. Original port bones/animations remain untouched.
"""
import argparse,copy,json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from model import Model
from convert import refit
from convert_xml2 import convert,weld
from fit_weights import fit
from validate_roster import clips,measure

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--character',required=True);ap.add_argument('--iterations',type=int,default=140);a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];row=dict(json.loads((root/'evidence/inventory.json').read_text())[a.character]);name=a.character+'-stockweights';row['character']=name
    p=a.port/'Actors';source=Model(p/(row['source']+'.IGB'));target=Model(p/(row['target']+'.IGB'))
    pos,norm,w,ix,_=refit(source,target);(pos,w,ix,norm),group=weld(pos,w,ix,norm,source.pos)
    distances,nearest=cKDTree(target.pos).query(pos)
    seed=copy.copy(source)
    for key in ['names','bones','parent','world','local','local_t','inverse_by_bone','palette']:setattr(seed,key,getattr(target,key))
    seed.pos=pos;seed.norm=norm;seed.weights=target.weights[nearest].copy();seed.indices=target.indices[nearest].copy();seed.bone_indices=target.bone_indices[nearest].copy()
    work=root/'cache/fits'/name;references=clips(a.port,row,root/'cache/animations')
    fitted,report=fit(seed,target,a.port,work,iterations=a.iterations,references=references,gate_limit=None)
    (work/'weight-transfer.json').write_text(json.dumps(dict(method='Nearest stock skin vertex -> its real four weights; seam-welded solver over all six references',max_nearest_distance=float(distances.max()),mean_nearest_distance=float(distances.mean()),source=row['source']),indent=2))
    dest=root/'evidence/fitted'/(name+'.IGB');convert(source.path,target.path,dest,fitted)
    measure(a.port,row,dest,root/'evidence/fitted-gates',False)
