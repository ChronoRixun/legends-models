"""Batch generate diagnostics and measure the roster. No enabled mod by default."""
import argparse,json,traceback
from pathlib import Path
from convert_xml2 import convert
from validate_roster import measure

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--characters',nargs='*');ap.add_argument('--renders',action='store_true');a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];r=json.loads((root/'evidence/inventory.json').read_text());report={}
    for name,row in r.items():
        if a.characters and name not in a.characters:continue
        if not row.get('source'):report[name]=dict(error=row['source_error']);continue
        try:
            p=a.port/'Actors';dest=root/'evidence/candidates'/(name+'.IGB')
            c=convert(p/(row['source']+'.IGB'),p/(row['target']+'.IGB'),dest)
            v=measure(a.port,row,dest,root/'evidence/gates',a.renders);report[name]=dict(conversion=c,pass_numeric=v['pass_numeric'])
        except Exception as e:report[name]=dict(error=repr(e),traceback=traceback.format_exc());print(name,repr(e),flush=True)
        (root/'evidence/batch.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':main()
