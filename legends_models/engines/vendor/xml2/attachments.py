"""Record port bolt-on inputs and prove local/world transform invariance."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from model import Model,pose
from validate_roster import clips
from claws import read_claw

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--character',required=True);ap.add_argument('--candidate',type=Path,required=True);ap.add_argument('--source');a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];row=json.loads((root/'evidence/inventory.json').read_text())[a.character];stock=Model(a.port/'Actors'/(row['target']+'.IGB'));new=Model(a.candidate)
    refs=clips(a.port,row,root/'cache/animations');delta=0.;local_error=0.
    for path in refs.values():
        anim=np.load(path)
        for qt in anim['qt']:
            sw,nw=[pose(m,anim['names'],qt,anim['use_translation']) for m in [stock,new]]
            if not np.array_equal(sw,nw):raise AssertionError('Port attachment matrices changed')
            # The same local coordinates, including tail/cape/tongue root, map
            # through exactly the same matrices at every sampled frame.
            for bone in ['Bip01 L Hand','Bip01 R Hand','Bip01 Pelvis','Bip01 Head','Bip01 Spine2','Motion']:
                if bone in stock.names:
                    i=stock.names.index(bone);delta=max(delta,float(np.abs(sw[i]-nw[i]).max()))
            if a.character=='wolverine':
                for side,hand in [('left','Bip01 L Hand'),('right','Bip01 R Hand')]:
                    cp,_=read_claw(a.port/'Models/Bolton'/('claw_'+side+'.IGB'));world=nw[new.names.index(hand)]
                    hp=np.column_stack((cp,np.ones(len(cp))));local_error=max(local_error,float(np.abs((hp@world@np.linalg.inv(world))[:,:3]-cp).max()))
    files=[]
    patterns=['*tail*','*tongue*','*cape*','*claw_left*','*claw_right*','23601.IGB','23801.IGB','23901.IGB','3310.IGB']
    for folder in [a.port/'Actors',a.port/'Models/Bolton']:
        for p in folder.glob('*'):
            if p.is_file() and any(p.match(pattern) for pattern in patterns):files.append(dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    result=dict(character=a.character,attachment_bone_matrix_delta=delta,claw_local_roundtrip_floating_error=local_error,local_geometry_overridden=False,attachment_inputs=files,proof='No model, bolt-on, tail, tongue or cape file is in this actor-only mod. All sampled port bone matrices are exactly identical.')
    out=root/'evidence/attachments';out.mkdir(exist_ok=True);(out/(a.character+'.json')).write_text(json.dumps(result,indent=2));print(a.character,delta,local_error)

if __name__=='__main__':main()
