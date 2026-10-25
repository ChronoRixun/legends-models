"""Optional bounded cosmetic refinement, with unchanged full validation."""
from pathlib import Path
import sys,json,shutil,datetime,argparse,os
sys.dont_write_bytecode=True
os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
import community_convert
import numpy as np
from community_model import Model
from fit_chain import refine
import validate

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--max-fraction',type=float,default=.025);ap.add_argument('--character');ap.add_argument('--no-render',action='store_true');args=ap.parse_args()
 root=Path(__file__).resolve().parents[1];port=Path(os.environ['LEGENDS_PORT']);results=[];slug=lambda s:s.lower().replace(' ','-')
 rows=json.loads((root/'evidence/pack-inventory.json').read_text());priority=['Emma Frost','Magma','Rogue','Wolverine','Pyro'];rows=sorted(rows,key=lambda r: (priority.index(r['character']) if r['character'] in priority else 99,r['costume']!='Ultimate XML1'))
 for r in rows:
  if args.character and r['character'].lower()!=args.character.lower():continue
  work=root/'output'/slug(r['character'])/slug(r['costume']);validation=work/'validation.json'
  if not validation.exists() or not (work/'fit.npz').exists():continue
  original=json.loads(validation.read_text())['acceptance']
  if original['numeric_pass'] or any('max_chain_distance' not in f for f in original['failures']):continue
  dest=root/'output/chain-refined'/slug(r['character'])/slug(r['costume']);dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/'CREDITS.md',dest/'CREDITS.md');report_path=dest/'refinement.json'
  if report_path.exists():
   old=json.loads(report_path.read_text())
   if old.get('displacement_height_fraction')==args.max_fraction:
    results.append({'character':r['character'],'costume':r['costume'],'refinement':str(report_path),'feasible':old['feasible'],'acceptance':old.get('acceptance')});continue
  print(datetime.datetime.now().isoformat(),'REFINE',r['character'],r['costume'],flush=True)
  record={'character':r['character'],'costume':r['costume'],'refinement':str(report_path)}
  try:
   source=Path(r['source_skin']);template=port/'Actors'/(r['target_skin']+'.IGB');db=r['animation_db']+'.IGB';fit=dict(np.load(work/'fit.npz'))
   fitted,report=refine(Model(source),Model(template),fit,port,work/'animations',db,max_displacement_fraction=args.max_fraction)
   record['feasible']=report['feasible']
   if report['feasible']:
    np.savez(dest/'fit.npz',**fitted);candidate=dest/(r['target_skin']+'.IGB');summary=community_convert.convert_candidate(source,template,candidate,fitted)
    rendered=root/'renders/chain-refined'/slug(r['character'])/slug(r['costume']) if not args.no_render else None
    measured=validate.evaluate(port,source,candidate,work/'animations',dest/'validation.json',render_root=rendered,animation_db=db,target_id=r['target_skin'],claws=r['character']=='Wolverine');report['acceptance']=measured['acceptance'];record['acceptance']=report['acceptance'];summary.update(character=r['character'],costume=r['costume'],acceptance=report['acceptance'],refinement=report);(dest/'conversion.json').write_text(json.dumps(summary,indent=2))
   report_path.write_text(json.dumps(report,indent=2));print('RESULT',r['character'],r['costume'],'feasible',record['feasible'],'numeric',record.get('acceptance',{}).get('numeric_pass'),flush=True)
  except Exception as e:record['error']=str(e);print('ERROR',str(e),flush=True)
  results.append(record);(root/'evidence/refined-results.json').write_text(json.dumps(results,indent=2))
 (root/'evidence/refined-results.json').write_text(json.dumps(results,indent=2))
if __name__=='__main__':main()
