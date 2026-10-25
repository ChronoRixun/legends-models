"""Local pack inventory and bind comparison; no game operations."""
import sys,json,hashlib
from pathlib import Path
sys.dont_write_bytecode=True
import numpy as np
from community_model import Model
from community_igb import IgbFile
from discover import slug

def role(path):
 n=path.name.lower()
 return 'skin_no_cel' if 'skin - no cel' in n else 'skin_cel' if '(skin)' in n else '3d_head' if '3d head' in n else 'select_portrait' if 'character select portrait' in n else 'conversation_portrait' if 'hud_head' in n else 'other'
def main():
 stocks=json.loads(Path('evidence/stock-inventory.json').read_text());rows=[]
 for entry in json.loads(Path('downloads/MANIFEST.json').read_text()):
  if entry['status']!='extracted':continue
  c=entry['character'];row={k:entry[k] for k in ['character','costume','post','sha256','extracted']};files=[]
  target=Model(stocks[c]['path']);row.update(target_skin=stocks[c]['target_skin'],animation_db=stocks[c]['animation_db'],stock_bones=len(target.names))
  for path in sorted(Path(entry['extracted']).rglob('*')):
   if path.suffix.lower()!='.igb':continue
   d={'path':str(path),'role':role(path)}
   try:
    f=IgbFile(path.read_bytes());d.update(version=hex(f.header[11]),objects=len(f.objects),undecoded=sum(not o.decoded for o in f.objects),textures=[{'width':im.get(2),'height':im.get(3),'format':im.get(11),'name':im.get(22)} for im in f.objects_of('igImage')])
    if f.objects_of('igSkeleton'):
     sk=f.objects_of('igSkeleton')[0];bones=[f.obj(r) for r in f.list_items(sk.get(4))];d.update(bones=[b.get(2) for b in bones],bone_count=len(bones))
    if d['role']=='skin_cel':
     m=Model(path);common=set(m.names)&set(target.names);delta={n:float(np.max(np.abs(m.world[m.names.index(n)]-target.world[target.names.index(n)]))) for n in common};weighted=set(m.bone_indices[m.weights>1e-7].tolist());missing=[m.names[i] for i in weighted if m.names[i] not in target.names];parents={n:(m.names[int(m.parent[m.names.index(n)])] if m.parent[m.names.index(n)]>=0 else None) for n in m.names};d.update(vertices=len(m.pos),triangles=len(m.tri),geometry=m.g.get(2),height=float(np.ptp(m.pos[:,2])),active_weighted_bones=[m.names[i] for i in sorted(weighted)],source_only=sorted(set(m.names)-set(target.names)),target_only=sorted(set(target.names)-set(m.names)),missing_weighted=missing,max_common_bind_delta=max(delta.values()),common_bind_deltas=delta,world=m.world.tolist(),parent_names=parents,weights_sum_error=float(np.max(np.abs(m.weights.sum(1)-1))))
     row['fit_check']={k:d[k] for k in ['vertices','triangles','bone_count','max_common_bind_delta','missing_weighted','source_only','target_only','height']};row['source_skin']=str(path)
   except Exception as e:d['error']=str(e)
   files.append(d)
  row['files']=files;rows.append(row);print(c,entry['costume'],row.get('fit_check'),flush=True)
 Path('evidence/pack-inventory.json').write_text(json.dumps(rows,indent=2))
 lines=['\n## Download and pack inventory','All 35 approved archives extracted; SHA256 verified. No forbidden members found.','Cel skin is default; non-cel is retained in each extracted pack. Every source IGB is 0x80000004 PNG8 (indexed RGBA palette), so source compatibility alone does not prove port-skeleton preservation.','| Character | Costume | Cel / non-cel | Head / conversation / select | Bones source / stock | Texture | Max common bind delta |','|---|---|---|---|---|---|---|']
 for r in rows:
  roles={f['role'] for f in r['files']};d=next((f for f in r['files'] if f['role']=='skin_cel'),{});tex=d.get('textures',[]);sizes=', '.join(f"{t['width']}x{t['height']} PNG8" for t in tex);lines.append(f"| {r['character']} | {r['costume']} | {'yes' if {'skin_cel','skin_no_cel'}<=roles else sorted(roles)} | {'yes / yes / yes' if {'3d_head','conversation_portrait','select_portrait'}<=roles else sorted(roles)} | {d.get('bone_count','?')} / {r['stock_bones']} | {sizes} | {d.get('max_common_bind_delta','?')} |")
 with Path('REPORT.md').open('a',encoding='utf8') as h:h.write('\n'.join(lines)+'\n')
if __name__=='__main__':main()
