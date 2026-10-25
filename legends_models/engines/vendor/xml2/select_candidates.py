"""Regenerate numerical winners with the final writer, then save gate evidence."""
import argparse,json
from pathlib import Path
import numpy as np
from convert_xml2 import convert
from validate_roster import measure

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--characters',nargs='*');ap.add_argument('--renders',action='store_true');a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];roster=json.loads((root/'evidence/inventory.json').read_text());choices={}
    for folder in ['gates','fitted-gates']:
        for p in sorted((root/'evidence'/folder).glob('*.json')):
            report=json.loads(p.read_text());name=p.stem.split('-')[0]
            if report['pass_numeric'] and name in roster:choices[name]=(p,report,folder)
    selection=json.loads((root/'evidence/selection.json').read_text()) if (root/'evidence/selection.json').exists() else {}
    for name,(gpath,gate,folder) in choices.items():
        if a.characters and name not in a.characters:continue
        row=dict(roster[name]);suffix=gpath.stem.split('-')[-1]
        source=gate.get('source') or (suffix if suffix.isdigit() else row['source']);row['source']=source
        fit=np.load(root/'cache/fits'/gpath.stem/'fit.npz') if folder=='fitted-gates' else None
        p=a.port/'Actors';dest=root/'evidence/selected'/(name+'.IGB');convert(p/(source+'.IGB'),p/(row['target']+'.IGB'),dest,fit)
        report=measure(a.port,row,dest,root/'evidence/final-gates',a.renders)
        selection[name]=dict(row=row,candidate=str(dest),gate=str(root/'evidence/final-gates'/(name+'.json')),pass_numeric=report['pass_numeric'],origin=str(gpath),source=source)
        (root/'evidence/selection.json').write_text(json.dumps(selection,indent=2));print('SELECTED',name,source,report['pass_numeric'],flush=True)

if __name__=='__main__':main()
