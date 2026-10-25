"""Rebuild selected private candidates from owned inputs and compare exact hashes."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from convert_xml2 import convert

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--selection',type=Path,default=Path('evidence/selection.json'));a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];selection=json.loads(a.selection.read_text());results={}
    for name,row in selection.items():
        if not row['pass_numeric']:continue
        origin=Path(row['origin']);fp=root/'cache/fits'/origin.stem/'fit.npz';fit=np.load(fp) if origin.parent.name=='fitted-gates' else None
        dest=root/'cache/reproduced'/(name+'.IGB');p=a.port/'Actors';convert(p/(row['source']+'.IGB'),p/(row['row']['target']+'.IGB'),dest,fit)
        expected=hashlib.sha256(Path(row['candidate']).read_bytes()).hexdigest();actual=hashlib.sha256(dest.read_bytes()).hexdigest()
        results[name]=dict(exact_match=actual==expected,sha256=actual);print(name,actual==expected,flush=True)
        if actual!=expected:raise AssertionError(name+' did not reproduce')
    (root/'evidence/reproduction.json').write_text(json.dumps(results,indent=2))
