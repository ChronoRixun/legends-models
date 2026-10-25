"""Player-local MUA character cosmetic converter. Never changes source installs.

Only writes to --output, a staging game root or disposable game copy. The produced
mods directory can be copied to a player's game. No assets are bundled with code.
"""
import argparse,hashlib,json,re,struct,io
from pathlib import Path
import numpy as np
from model import Model
from igb_file import IgbFile
from round1_converter import blob,put_int,geometry_attribute
from characters import CHARACTERS,check_identity

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def refit(source,target):
    aliases={'Bip01':'Bip01 Pelvis','fx01':'Bip01 L Hand','fx02':'Bip01 R Hand'}
    mapped=[];transforms=[];decisions={}
    fallback_bone=next(i for i,b in enumerate(target.bones) if b.get(4)>=0)
    for i,b in enumerate(source.bones):
        name=source.names[i];dest=aliases.get(name,name)
        if dest not in target.names or target.bones[target.names.index(dest)].get(4)<0:
            mapped.append(fallback_bone);transforms.append(np.eye(4));continue
        j=target.names.index(dest);mapped.append(j)
        # Missing source bones bind through their shared ancestor, preserving
        # the mesh's existing anatomical position instead of reorienting it.
        src=source.names.index(dest) if name in aliases else i
        transforms.append(np.linalg.inv(source.world[src])@target.world[j])
        if name in aliases:decisions[name]=dest
    active=set(source.bone_indices[source.weights>1e-7].tolist())
    for i in active:
        dest=aliases.get(source.names[i],source.names[i])
        if dest not in target.names or target.bones[target.names.index(dest)].get(4)<0:
            raise ValueError(f'Unsupported weighted bone: {source.names[i]}')
    matrices=np.asarray(transforms)[source.bone_indices]
    hp=np.column_stack((source.pos,np.ones(len(source.pos))))
    pos=np.einsum('vi,vkij,vk->vj',hp,matrices,source.weights)[:,:3]
    normals=np.einsum('vi,vkij,vk->vj',source.norm,matrices[:,:,:3,:3],source.weights)
    normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
    bm2pal={m:i for i,m in enumerate(target.palette)}
    indices=np.array([[bm2pal[target.bones[mapped[int(i)]].get(4)] for i in row] for row in source.bone_indices],dtype='u1')
    # Coalesce influences that now share a parent. Fill zero-weight lanes with
    # the first active palette index, never an unused/out-of-range slot.
    weights=source.weights.copy()
    for v in range(len(pos)):
        merged={}
        for ix,w in zip(indices[v],weights[v]):
            if w>0:merged[int(ix)]=merged.get(int(ix),0)+float(w)
        pairs=sorted(merged.items(),key=lambda x:-x[1]);weights[v]=0;indices[v]=pairs[0][0]
        for k,(ix,w) in enumerate(pairs):indices[v,k]=ix;weights[v,k]=w
    return pos,normals,weights,indices,decisions

def dxt1_rgb_to_dxt3(data,swap_rb=True):
    """Preserve 4-color blocks exactly; correctly convert 3-color blocks to BC2.

    MUA's canonical RGB565 endpoint convention is opposite D3D's BGR565.
    BC2 always uses four colors, so a BC1 average or transparent black cannot
    simply be copied. Re-quantize just those exceptional blocks using Pillow.
    """
    import io
    from PIL import Image
    out=bytearray()
    for c0,c1,bits in struct.iter_unpack('<HHI',data):
        swap=lambda c:((c&31)<<11)|(c&0x7e0)|((c>>11)&31)
        a,b=(swap(c0),swap(c1)) if swap_rb else (c0,c1)
        if c0>c1 or c0==c1 and bits==0:
            out+=b'\xff'*8+struct.pack('<HHI',a,b,bits)
        else:
            # Palette computed from the original mode, then swapped if required.
            def rgb(c):return np.array([((c>>11)&31)*255//31,((c>>5)&63)*255//63,(c&31)*255//31],dtype=int)
            ca,cb=rgb(c0),rgb(c1);pal=np.array([ca,cb,(ca+cb)//2,[0,0,0]],dtype='uint8')
            ids=[(bits>>(2*k))&3 for k in range(16)];pix=pal[ids].reshape(4,4,3)
            if swap_rb:pix=pix[:,:,::-1]
            rgba=np.dstack((pix,np.where(np.array(ids).reshape(4,4)==3,0,255).astype('uint8')))
            buf=io.BytesIO();Image.fromarray(rgba).save(buf,format='DDS',pixel_format='DXT3')
            out+=buf.getvalue()[128:]
    return bytes(out)

def upscale_bc1_to_bc2(data,source_size,size,swap_rb=True):
    """Explicitly upscale a legacy diffuse while preserving BC1 alpha.

    This standardizes the output texture size; it does not invent source detail.
    Existing authored mips are used unchanged whenever their size is available.
    """
    from PIL import Image
    header=struct.pack('<7I',124,0x81007,source_size,source_size,len(data),0,1)+bytes(44)
    header+=struct.pack('<II4s5I',32,4,b'DXT1',0,0,0,0,0)+struct.pack('<5I',0x1000,0,0,0,0)
    rgba=Image.open(io.BytesIO(b'DDS '+header+data)).convert('RGBA')
    if swap_rb:
        p=np.asarray(rgba).copy();p=p[:,:,[2,1,0,3]];rgba=Image.fromarray(p)
    rgba=rgba.resize((size,size),Image.Resampling.LANCZOS)
    result=io.BytesIO();rgba.save(result,format='DDS',pixel_format='DXT3')
    return result.getvalue()[128:]

def convert_skin(source_path,template_path,dest,swap_rb=True,outline_width=.28,fit=None):
    source=Model(source_path);target=Model(template_path);f=target.f
    if source.f.header[11]!=0xf0000008 or f.header[11]!=0xf0000006:raise ValueError('Expected MUA v8 and port v6')
    pos,normals,weights,indices,aliases=refit(source,target)
    if fit is not None:
        pos,normals,weights,indices=[fit[k] for k in ['pos','norm','weights','indices']]
    hull_normals=fit['outline_norm'] if fit is not None and 'outline_norm' in fit else normals
    blocks={};n=len(pos)
    target_name=Path(template_path).stem
    for name,is_outline in [(target_name,False),(target_name+'_outline',True)]:
        g=next(g for g in f.objects_of('igGeometry') if g.get(2)==name);ga=geometry_attribute(f,g);va=f.obj(ga.get(4))
        indirect=struct.unpack('<20i',blob(f,va.get(2)))
        put_int(f,va,3,n);put_int(f,va,6,0x441 if is_outline else 0x10443)
        blocks[indirect[0]]=np.asarray(pos+hull_normals*outline_width if is_outline else pos,dtype='<f4').tobytes()
        if not is_outline:
            blocks[indirect[1]]=np.asarray(normals,dtype='<f4').tobytes()
            blocks[indirect[11]]=np.asarray(source.uv,dtype='<f4').tobytes()
        blocks[va.get(7)]=np.asarray(weights,dtype='<f4').tobytes();blocks[va.get(8)]=indices.tobytes()
        tris=source.tri[:,::-1] if is_outline else source.tri
        ia=f.obj(ga.get(5));blocks[ia.get(2)]=tris.astype('<u2').tobytes()
        put_int(f,ia,3,len(tris)*3);put_int(f,ga,6,3);put_int(f,ga,7,len(tris));put_int(f,ga,13,-1)
    source_images={im.get(2):im for im in source.f.objects_of('igImage') if re.search(r'_d(?:-\d+)?\.png$',im.get(22).lower())}
    if not source_images:
        # Legacy NPC models contain a single diffuse chain without _d suffix.
        images=source.f.objects_of('igImage')
        stems={re.sub(r'-\d+(?=\.png$)','',im.get(22).lower()) for im in images}
        if len(stems)!=1:raise ValueError('Ambiguous legacy diffuse image chains')
        source_images={im.get(2):im for im in images}
    source_max=max(source_images);target_max=max(im.get(2) for im in f.objects_of('igImage'))
    output_size=512
    if output_size%target_max:raise ValueError('Unsupported diffuse mip size relation')
    image_scale=output_size//target_max
    if len(f.objects_of('igTextureAttr'))!=1 or any(b.get(5)!=0 for b in f.objects_of('igTextureBindAttr')):
        raise ValueError('Production template must use one diffuse texture on stage zero')
    for im in f.objects_of('igImage'):
        size=im.get(2)*image_scale;sim=source_images.get(size,source_images[source_max])
        if sim.get(11)!=14:raise ValueError('Only source BC1 diffuse is currently supported')
        pixels=(dxt1_rgb_to_dxt3(blob(source.f,sim.get(13)),swap_rb) if sim.get(2)==size else
                upscale_bc1_to_bc2(blob(source.f,sim.get(13)),sim.get(2),size,swap_rb))
        put_int(f,im,2,size);put_int(f,im,3,size);put_int(f,im,11,15);put_int(f,im,12,len(pixels));put_int(f,im,19,size*4)
        blocks[im.get(13)]=pixels
    # Keep the port's bounds as well as its skeleton. A render bound may have
    # gameplay consumers; cosmetic conversion must not silently change it.
    result=f.rebuilt(blocks);check=IgbFile(result)
    if not all(o.decoded for o in check.objects):raise ValueError('Output has undecoded objects')
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(result)
    summary={'source':str(source_path),'source_sha256':sha(source_path),'template_sha256':sha(template_path),'output_sha256':sha(dest),
             'vertices':n,'triangles':len(source.tri),'palette_count':len(target.palette),'gameplay_skeleton':'unchanged byte-for-byte',
             'aliases':aliases,'diffuse_size':output_size,'source_native_diffuse_size':source_max,
             'diffuse_resampling':'legacy source upscaled with Lanczos; no added detail' if source_max<output_size else 'authored 512 mip and smaller mips',
             'diffuse_mips':len(f.objects_of('igImage')),'last_mip_size':min(source_images),'source_rb_to_d3d':swap_rb,
             'outline_width':outline_width,'normal_map':'not emitted: direct MUA stage mapping failed scope test; correct XML2 material integration unimplemented',
             'specular_map':'not emitted: direct MUA stage mapping failed scope test; correct XML2 material integration unimplemented',
             'attachments':'stock port bolt-ons and attachments retained','acceptance':'pending independent validation'}
    summary['bounds']='original port bounds retained'
    summary['last_mip_size']=min(im.get(2) for im in f.objects_of('igImage'))
    return summary

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mua',required=True,type=Path);ap.add_argument('--port',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path,help='Staging game root; never a source installation')
    ap.add_argument('--character',choices=list(CHARACTERS),default='wolverine')
    ap.add_argument('--skin',help='MUA costume ID; Wolverine defaults to 0302, with 0301 available as an alternative')
    ap.add_argument('--name');ap.add_argument('--raw-colors',action='store_true',help='Diagnostic only: skip channel conversion')
    ap.add_argument('--basic',action='store_true',help='Diagnostic only: skip full-frame skin-weight fitting (fails stretch gate)')
    ap.add_argument('--fit-iterations',type=int,default=450,help='Solver iteration ceiling; failed gates never emit an enabled mod')
    args=ap.parse_args();out=args.output.resolve();mua=args.mua.resolve();port=args.port.resolve()
    source_id,target_id,animation_db=CHARACTERS[args.character]
    source_id=args.skin or source_id
    check_identity(args.character,source_id)
    if not re.fullmatch(r'[0-9]{4,5}',source_id):ap.error('Skin must be a four- or five-digit source costume ID')
    args.name=args.name or 'MUA-'+args.character.title()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):ap.error('Unsafe mod name')
    if out==port or out==mua or out.is_relative_to(mua) or out.is_relative_to(port):ap.error('Output must be separate from both read-only inputs')
    cache=out/'.mua-converter-cache'/args.character/source_id
    # Reuse earlier private research caches, while new multi-character output
    # roots keep each source skin's fitting and animation evidence separate.
    quality=out/'.mua-converter-cache/quality'
    if not quality.exists():quality=cache/'quality'
    roots=[mua,mua/'Data',mua/'Setup/Data',mua/'files/Setup/Data']
    source=next((r/'actors'/(source_id+'.igb') for r in roots if (r/'actors'/(source_id+'.igb')).is_file()),None)
    if source is None:
        from mua_bin import Archive
        archive=Archive(mua/'actors.bin');entry=next((e for e in archive.entries if e[0].lower()=='actors/'+source_id+'.igb'),None)
        if entry is None:ap.error(f'actors/{source_id}.igb absent from actors.bin')
        source=cache/(source_id+'.igb');source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(archive.read(entry))
    mod=out/'mods'/args.name;dest=mod/'Actors'/(target_id+'.IGB')
    fitted=None;fit_report=None
    if not args.basic:
        from fit_weights import fit
        fitted,fit_report=fit(Model(source),Model(port/'Actors'/(target_id+'.IGB')),port,quality,iterations=args.fit_iterations,animation_db=animation_db,
                             regularizer=.001 if args.character=='storm' else .005,outline_smoothing=100 if args.character=='storm' else 10,
                             align_soles=args.character=='colossus')
    candidate=cache/'candidate.IGB'
    summary=convert_skin(source,port/'Actors'/(target_id+'.IGB'),candidate,not args.raw_colors,fit=fitted)
    summary['character']=args.character
    summary['skin']=source_id
    summary['weight_fit']=fit_report
    from validate import evaluate
    validation=evaluate(port,source,candidate,quality/'animations',out/'validation.json',
                        animation_db=animation_db,target_id=target_id,claws=args.character=='wolverine')
    summary['acceptance']=validation['acceptance']
    reports=out/'reports'/args.name/source_id;reports.mkdir(parents=True,exist_ok=True)
    (reports/'validation.json').write_text(json.dumps(validation,indent=2))
    (reports/'conversion.json').write_text(json.dumps(summary,indent=2))
    (out/'conversion.json').write_text(json.dumps(summary,indent=2))
    if not validation['acceptance']['numeric_pass']:raise ValueError('Candidate failed stock-relative gate; diagnostics saved, production mod not changed')
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(candidate.read_bytes())
    costume='Dark Psylocke NPC' if args.character=='psylocke' and source_id=='3005' else args.character.title()+' '+source_id
    (mod/'mod.json').write_text(json.dumps({'name':args.name,'character':args.character,'skin':source_id,
        'output_sha256':summary['output_sha256'],'description':f'Local MUA {costume} refit; stock-relative numeric gate passed; owned assets only'},indent=2))
    (mod/'readme.txt').write_text('Generated from your owned MUA assets. Do not distribute. Uses '+costume+'. Numeric gate passed; consult visual evidence before use.\nDisable by changing + to - in mods/load-order.txt, or remove this folder and restart.\n')
    order=out/'mods/load-order.txt';lines=order.read_text(encoding='utf-8-sig').splitlines() if order.exists() else []
    lines=[x for x in lines if x.lstrip('+- ').lower()!=args.name.lower()];lines.append('+ '+args.name)
    order.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
