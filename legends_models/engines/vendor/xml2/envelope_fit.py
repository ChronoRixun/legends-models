"""Optional tiny mesh scale refinement after the round-2 weight fit.

The port skeleton and bounds remain byte-identical. This changes actual mesh
positions, not measured limits; every gate must pass again after serialization.
"""
import argparse,json
from pathlib import Path
import numpy as np
from model import Model
from convert_xml2 import convert
from validate_roster import measure

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--character',required=True);ap.add_argument('--scale',type=float,default=.998);a=ap.parse_args()
    if not .995<=a.scale<=1:raise ValueError('Only a <=0.5% mesh refinement is supported')
    root=Path(__file__).resolve().parents[1];row=dict(json.loads((root/'evidence/inventory.json').read_text())[a.character]);name=a.character+'-envelope';row['character']=name
    f=dict(np.load(root/'cache/fits'/a.character/'fit.npz'));target=Model(a.port/'Actors'/(row['target']+'.IGB'));center=target.world[target.names.index('Bip01 Pelvis'),3,:3]
    before=f['pos'].copy();f['pos']=center+(f['pos']-center)*a.scale
    work=root/'cache/fits'/name;work.mkdir(parents=True,exist_ok=True);np.savez(work/'fit.npz',**f)
    (work/'refinement.json').write_text(json.dumps(dict(scale=a.scale,center=center.tolist(),max_vertex_displacement=float(np.linalg.norm(f['pos']-before,axis=1).max()),skeleton_and_bounds='unchanged'),indent=2))
    dest=root/'evidence/fitted'/(name+'.IGB');convert(a.port/'Actors'/(row['source']+'.IGB'),target.path,dest,f)
    measure(a.port,row,dest,root/'evidence/fitted-gates',False)
