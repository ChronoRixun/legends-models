"""Fit community PNG8 mesh into an unchanged player-local port skeleton."""
from pathlib import Path
import sys,json,struct,io,hashlib,argparse,os
sys.dont_write_bytecode=True
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parent/"round2"))
import community_model
# Existing round-2 fit and validator consume this more general model reader.
sys.modules['model']=community_model
from community_model import Model,texture
from community_igb import IgbFile
from round1_converter import put_int,blob
from convert import refit

def convert_candidate(source_path,template_path,dest,fit=None,outline_width=.28):
 source,target=Model(source_path),Model(template_path);f=target.f
 if source.f.header[11]!=0x80000004 or f.header[11]!=0xf0000006:raise ValueError('Expected community v4 and stock v6')
 pos,norm,weights,indices,_=refit(source,target) if fit is None else (fit['pos'],fit['norm'],fit['weights'],fit['indices'],{})
 hn=norm if fit is None else fit.get('outline_norm',norm)
 if len(source.pos)!=len(pos):raise ValueError('Source vertex correspondence changed')
 blocks={};n=len(pos)
 for part,outline in [(target,False),(Model(template_path,'__outline__'),True)]:
  ga,va=part.ga,part.va;indirect=part.indirect
  put_int(f,va,3,n);put_int(f,va,6,0x441 if outline else 0x10443)
  blocks[indirect[0]]=np.asarray(pos+hn*outline_width if outline else pos,dtype='<f4').tobytes()
  if not outline:
   blocks[indirect[1]]=np.asarray(norm,dtype='<f4').tobytes();blocks[indirect[11]]=np.asarray(source.uv,dtype='<f4').tobytes()
  blocks[va.get(7)]=np.asarray(weights,dtype='<f4').tobytes();blocks[va.get(8)]=np.asarray(indices,dtype='u1').tobytes()
  tri=source.tri[:,::-1] if outline else source.tri;ia=f.obj(ga.get(5));blocks[ia.get(2)]=tri.astype('<u2').tobytes()
  put_int(f,ia,3,len(tri)*3);put_int(f,ga,6,3);put_int(f,ga,7,len(tri));put_int(f,ga,13,-1)
 tex=texture(source).convert('RGBA');base=max(im.get(2) for im in f.objects_of('igImage'))
 for im in f.objects_of('igImage'):
  w=max(1,round(im.get(2)*tex.width/base));h=max(1,round(im.get(3)*tex.height/base));mip=tex.resize((w,h),Image.Resampling.LANCZOS);buf=io.BytesIO();mip.save(buf,format='DDS',pixel_format='DXT3');pixels=buf.getvalue()[128:]
  put_int(f,im,2,w);put_int(f,im,3,h);put_int(f,im,11,15);put_int(f,im,12,len(pixels));put_int(f,im,19,((w+3)//4)*16);blocks[im.get(13)]=pixels
 result=f.rebuilt(blocks);check=IgbFile(result)
 if not all(o.decoded for o in check.objects):raise ValueError('Output contains undecoded objects')
 dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(result)
 # Independently reread serialization: target names, parent map, blend cache,
 # inverse binds, local offsets, all bone fields and render bounds unchanged.
 new=Model(dest)
 if target.names!=new.names or not np.array_equal(target.parent,new.parent) or not np.array_equal(target.ib,new.ib) or not np.array_equal(target.local_t,new.local_t) or not np.array_equal(target.palette,new.palette):raise ValueError('Serialized skeleton changed')
 for a,b in zip(target.bones,new.bones):
  if {k:v.value for k,v in a.fields.items()}!={k:v.value for k,v in b.fields.items()}:raise ValueError('Bone field changed')
 if [(o.get(2),o.get(3)) for o in f.objects_of('igAABox')]!=[(o.get(2),o.get(3)) for o in check.objects_of('igAABox')]:raise ValueError('Bounds changed')
 return {'source':str(source_path),'template':str(template_path),'output':str(dest),'vertices':n,'triangles':len(source.tri),'texture_size':list(tex.size),'skeleton_unchanged':True,'sha256':hashlib.sha256(result).hexdigest()}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--character',required=True);ap.add_argument('--costume');ap.add_argument('--iterations',type=int,default=450);ap.add_argument('--render',action='store_true');ap.add_argument('--basic',action='store_true');args=ap.parse_args()
 rows=json.loads(Path('evidence/pack-inventory.json').read_text());eligible=[r for r in rows if r['character'].lower()==args.character.lower()]
 r=next((r for r in eligible if r['costume']==args.costume),None) if args.costume else next((r for r in eligible if r['costume']=='Ultimate XML1'),eligible[0])
 if r is None:raise ValueError('Selected costume not downloaded')
 from discover import slug
 work=Path('output',slug(r['character']),slug(r['costume']));work.mkdir(parents=True,exist_ok=True)
 import shutil
 shutil.copyfile('CREDITS.md',work/'CREDITS.md')
 port=Path(os.environ['LEGENDS_PORT']);source=Path(r['source_skin']);template=port/'Actors'/(r['target_skin']+'.IGB');db=r['animation_db']+'.IGB'
 import validate
 # Source RGB palette already decoded; the MUA-specific red/blue reversal in
 # the existing source-render branch is inapplicable. Render source separately.
 from reference_clips import prepare
 prepare(port,db,work/'animations')
 fitted,fit_report=None,None
 if not args.basic:
  from fit_weights import fit
  fitted,fit_report=fit(Model(source),Model(template),port,work,iterations=args.iterations,animation_db=db)
 candidate=work/(r['target_skin']+'.IGB');summary=convert_candidate(source,template,candidate,fitted)
 summary.update(character=r['character'],costume=r['costume'],weight_fit=fit_report)
 validation=validate.evaluate(port,source,candidate,work/'animations',work/'validation.json',render_root=Path('renders',slug(r['character']),slug(r['costume'])) if args.render else None,animation_db=db,target_id=r['target_skin'],claws=r['character']=='Wolverine')
 summary['acceptance']=validation['acceptance'];(work/'conversion.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
