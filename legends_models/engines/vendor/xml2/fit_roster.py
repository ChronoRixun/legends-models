"""Round-2 fitter with all six explicit reference clips and welded UV seams."""
import argparse,json,traceback
from pathlib import Path
import numpy as np
from model import Model
from fit_weights import fit
from validate_roster import clips,measure
from convert_xml2 import convert

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--characters',nargs='+',required=True);ap.add_argument('--iterations',type=int,default=140);ap.add_argument('--renders',action='store_true');ap.add_argument('--source');a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];roster=json.loads((root/'evidence/inventory.json').read_text());reports={}
    for name in a.characters:
        row=dict(roster[name]);p=a.port/'Actors'
        if a.source:
            if len(a.characters)!=1:raise ValueError('--source requires one character')
            row['source']=a.source;name=name+'-'+a.source;row['character']=name
        work=root/'cache/fits'/name
        try:
            references=clips(a.port,row,root/'cache/animations')
            source,target=Model(p/(row['source']+'.IGB')),Model(p/(row['target']+'.IGB'))
            fitted,report=fit(source,target,a.port,work,iterations=a.iterations,animation_db=row['animation_db']+'.IGB',references=references,gate_limit=None)
            dest=root/'evidence/fitted'/(name+'.IGB');convert(source.path,target.path,dest,fitted)
            gate=measure(a.port,row,dest,root/'evidence/fitted-gates',a.renders)
            reports[name]=dict(fit=report,pass_numeric=gate['pass_numeric'])
        except Exception as e:
            reports[name]=dict(error=repr(e),traceback=traceback.format_exc());print(name,repr(e),flush=True)
            # Retain a failed solver result for independent measurement; never
            # stage it as a passing/active mod.
            if (work/'fit.npz').exists():
                try:
                    dest=root/'evidence/fitted'/(name+'.IGB');convert(source.path,target.path,dest,np.load(work/'fit.npz'))
                    gate=measure(a.port,row,dest,root/'evidence/fitted-gates',a.renders);reports[name]['pass_numeric']=gate['pass_numeric']
                except Exception as ex:reports[name]['diagnostic_error']=repr(ex)
        (root/('evidence/fitter-batch-'+a.source+'.json' if a.source else 'evidence/fitter-batch.json')).write_text(json.dumps(reports,indent=2))

if __name__=='__main__':main()
