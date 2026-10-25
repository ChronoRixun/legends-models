"""Create a diagnostic MUA2 skin candidate. Never installs/enables a mod.

The separate acceptance report must pass every spec item before deployment.
"""
import argparse,hashlib,io,json,struct,os
from pathlib import Path
import numpy as np
from PIL import Image
from model import Model
from mua2_model import Source
from igz_textures import resolve
from igz import require
from igb_file import IgbFile
from round1_converter import blob,put_int,geometry_attribute
from convert import refit

PORT=Path(os.environ['LEGENDS_PORT'])  # legends-models: was a research-PC path
GAME=Path(os.environ['LEGENDS_MUA2'])  # legends-models: was a research-PC path
ROOT=Path(__file__).resolve().parents[1]

def atlas(source,report,out):
    materials={}
    for m in report['meshes']:
        t=next(t for t in m['textures'] if t['stage']==0)
        materials.setdefault(t['dds'],Image.open(t['dds']).convert('RGBA'))
    # Square power-of-two tiles retain each original's full texel density.
    tile=max(max(im.size) for im in materials.values());cols=2 if len(materials)>1 else 1
    rows=(len(materials)+cols-1)//cols;size=1
    while size<max(tile*cols,tile*rows):size*=2
    image=Image.new('RGBA',(size,size),(0,0,0,0));rects={}
    for i,(path,im) in enumerate(materials.items()):
        x,y=i%cols*tile,i//cols*tile
        for yy in range(0,tile,im.height):
            for xx in range(0,tile,im.width):image.paste(im,(x+xx,y+yy))
        rects[path]=(x,y,*im.size)
    offset=0
    for source_mesh,m in zip(source.meshes,report['meshes']):
        path=next(t['dds'] for t in m['textures'] if t['stage']==0);x,y,w,h=rects[path];n=len(source_mesh['pos'])
        # Repeat smaller sheets inside their tile so authored UVs just beyond
        # 1 sample the same wrapped image, without clipping or downsampling.
        uv=source.uv[offset:offset+n]
        require(np.all(uv>=0) and np.all(uv*[w,h]<=[tile,tile]),'UV wrapping exceeds atlas tile')
        uv[:]=((uv*[w,h])+[x,y])/size
        offset+=n
    image.save(out/'diffuse-atlas.png')
    return image,dict(size=size,tiles={p:list(r) for p,r in rects.items()},uv_policy='native resolution repeated within tile; no clamping')

def write_candidate(source,target,dest,fitted,texture):
    f=target.f;blocks={};n=len(source.pos)
    pos,norm,weights,indices=[fitted[k] for k in ('pos','norm','weights','indices')];hn=fitted.get('outline_norm',norm)
    require(n<=65535,'Too many vertices for port u16 indices')
    for g in f.objects_of('igGeometry'):
        outline='outline' in (g.get(2) or '')
        ga=geometry_attribute(f,g);va=f.obj(ga.get(4));ind=struct.unpack('<20i',blob(f,va.get(2)))
        put_int(f,va,3,n);put_int(f,va,6,0x441 if outline else 0x10443)
        blocks[ind[0]]=np.asarray(pos+hn*.28 if outline else pos,dtype='<f4').tobytes()
        if not outline:
            blocks[ind[1]]=np.asarray(norm,dtype='<f4').tobytes();blocks[ind[11]]=np.asarray(source.uv,dtype='<f4').tobytes()
        blocks[va.get(7)]=np.asarray(weights,dtype='<f4').tobytes();blocks[va.get(8)]=np.asarray(indices,dtype='u1').tobytes()
        tri=source.tri[:,::-1] if outline else source.tri;ia=f.obj(ga.get(5));blocks[ia.get(2)]=tri.astype('<u2').tobytes()
        put_int(f,ia,3,len(tri)*3);put_int(f,ga,6,3);put_int(f,ga,7,len(tri));put_int(f,ga,13,-1)
    maximum=max(i.get(2) for i in f.objects_of('igImage'))
    for im in f.objects_of('igImage'):
        w=texture.width*im.get(2)//maximum;h=texture.height*im.get(3)//maximum
        level=texture if (w,h)==texture.size else texture.resize((w,h),Image.Resampling.LANCZOS)
        out=io.BytesIO();level.save(out,format='DDS',pixel_format='DXT3');pixels=out.getvalue()[128:]
        put_int(f,im,2,w);put_int(f,im,3,h);put_int(f,im,11,15);put_int(f,im,12,len(pixels));put_int(f,im,19,w*4);blocks[im.get(13)]=pixels
    result=f.rebuilt(blocks);check=IgbFile(result);require(all(o.decoded for o in check.objects),'Undecoded output objects')
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(result)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--character',choices=['gambit','wolverine'],required=True)
    ap.add_argument('--iterations',type=int,default=120);ap.add_argument('--samples',type=int,default=24);ap.add_argument('--basic',action='store_true');ap.add_argument('--reuse-fit',action='store_true');args=ap.parse_args()
    char=args.character;source_id,target_id,db=('0601','15301','x1_13_gambit.IGB') if char=='gambit' else ('0301','14301','x1_03_wolverine.IGB')
    out=ROOT/'evidence'/char;out.mkdir(parents=True,exist_ok=True);path=ROOT/'evidence/actors'/(source_id+'.igz')
    target=Model(PORT/'Actors'/(target_id+'.IGB'));source=Source(path,target)
    report=resolve(path,GAME,out/'textures');image,atlas_report=atlas(source,report,out)
    np.savez(out/'source.npz',pos=source.pos,norm=source.norm,uv=source.uv,tri=source.tri,weights=source.weights,bone_indices=source.bone_indices)
    clips=[('idle','fightstyle_hero.IGB'),('run','common.IGB'),('attack_light1','fightstyle_hero.IGB'),('f_heavy_attack_4','x1_03_wolverine.IGB'),('power_2',db),('pain_high','common.IGB')] if char=='gambit' else [('idle',db),('run',db),('attack_light1',db),('f_heavy_attack_4',db),('power_2',db),('pain_high','common.IGB')]
    if args.reuse_fit:
        fitted=dict(np.load(out/'fit/fit.npz'));fit_report=json.loads((out/'fit/fit.json').read_text())
        require(len(fitted['pos'])==len(source.pos),'Cached fit vertex count mismatch')
    elif args.basic:
        p,n,w,i,_=refit(source,target);fitted=dict(pos=p,norm=n,weights=w,indices=i);fit_report=None
    else:
        from fit_weights import fit
        fitted,fit_report=fit(source,target,PORT,out/'fit',iterations=args.iterations,animation_db=db,clips=clips,samples=args.samples,diagnostic=True,coat_transfer=char=='gambit',stock_relative=True)
    dest=out/(target_id+'.IGB');write_candidate(source,target,dest,fitted,image)
    manifest=dict(character=char,source=str(path),template=str(target.path),candidate=str(dest),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  template_sha256=hashlib.sha256(target.path.read_bytes()).hexdigest(),candidate_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),
                  vertices=len(source.pos),triangles=len(source.tri),removed_embedded_claw_triangles=source.removed_claw_triangles,bone_folding=source.decisions,atlas=atlas_report,fit=fit_report,clips=clips,
                  missing_gate_clips=[],native_clip_absences=['native Gambit f_heavy_attack_4; port Wolverine clip used only as extra stress reference'] if char=='gambit' else [],
                  reference_clip_provenance='Gambit-specific power_2 plus inherited hero/common clips; f_heavy_attack_4 is a labeled port Wolverine stress reference' if char=='gambit' else 'Port Wolverine database plus common pain_high',
                  attachments='Stock port bolt-ons retained; hand-local verification pending',status='DIAGNOSTIC ONLY; full acceptance required')
    (out/'conversion.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
