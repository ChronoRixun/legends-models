"""Local texture-only IGB upscaler. Baseline adds no converter dependencies.

Requires the converter's Pillow. Optional AI uses numpy + ONNX Runtime CPU and
a separately obtained Real-ESRGAN x4 ONNX model. Never downloads implicitly.
"""
import argparse,hashlib,io,json,struct,time
from pathlib import Path
from PIL import Image,ImageFilter,ImageDraw
from igb_file import IgbFile
from round1_converter import blob,put_int
from append_v6 import Appender

FORMATS={14:'DXT1',15:'DXT3',16:'DXT5'}

def decode(f,im):
    w,h,fmt=im.get(2),im.get(3),im.get(11)
    if fmt not in FORMATS:raise ValueError(f'Unsupported format {fmt}')
    data=blob(f,im.get(13));expected=max(1,(w+3)//4)*max(1,(h+3)//4)*(8 if fmt==14 else 16)
    if len(data)!=expected:raise ValueError(f'Bad block byte count {len(data)} != {expected}')
    hdr=struct.pack('<7I',124,0x81007,h,w,len(data),0,1)+bytes(44)
    hdr+=struct.pack('<II4s5I',32,4,FORMATS[fmt].encode(),0,0,0,0,0)+struct.pack('<5I',0x1000,0,0,0,0)
    return Image.open(io.BytesIO(b'DDS '+hdr+data)).convert('RGBA')

def encode(image,fmt):
    w,h=image.size
    # Pillow's encoder requires complete blocks, including 1px and 2px mips.
    if w<4 or h<4:
        padded=Image.new('RGBA',(max(w,4),max(h,4)))
        for y in range(padded.height):
            for x in range(padded.width):padded.putpixel((x,y),image.getpixel((min(x,w-1),min(y,h-1))))
        image=padded
    b=io.BytesIO();image.save(b,format='DDS',pixel_format=FORMATS[fmt]);return b.getvalue()[128:]

def baseline(image):
    # Sharpen colour only: preserving alpha avoids bright halos around cutouts.
    size=(image.width*4,image.height*4)
    rgb=image.convert('RGB').resize(size,Image.Resampling.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.2,percent=90,threshold=3))
    rgb.putalpha(image.getchannel('A').resize(size,Image.Resampling.LANCZOS));return rgb

class AI:
    def __init__(self,path):
        import onnxruntime as ort
        opt=ort.SessionOptions();opt.intra_op_num_threads=4
        self.session=ort.InferenceSession(str(path),sess_options=opt,providers=['CPUExecutionProvider'])
    def __call__(self,image):
        import numpy as np
        x=np.asarray(image.convert('RGB'),dtype=np.float32).transpose(2,0,1)[None]/255
        y=self.session.run(None,{self.session.get_inputs()[0].name:x})[0]
        out=Image.fromarray(np.clip(np.rint(y[0].transpose(1,2,0)*255),0,255).astype('uint8'))
        if out.size!=(image.width*4,image.height*4):raise ValueError('AI model must produce exactly 4x')
        out.putalpha(image.getchannel('A').resize(out.size,Image.Resampling.LANCZOS));return out

def texture_invariant(before,after,allowed_blocks,allowed_fields,new_refs=()):
    """Exact object/field/block comparison, independent of file offsets."""
    for o in before.objects:
        n=after.obj(o.ref)
        if n is None or n.name!=o.name:raise AssertionError('Object graph changed')
        for slot,v in o.fields.items():
            if (o.ref,slot) not in allowed_fields and v.value!=n.get(slot):raise AssertionError(f'Nontexture field changed {o.ref}:{slot}')
    for ref in before.blocks:
        if ref not in allowed_blocks and blob(before,ref)!=blob(after,ref):raise AssertionError(f'Nontexture block changed {ref}')
    return dict(nontexture_fields_and_blocks_identical=True,hand_head_motion_matrix_delta=0,mesh_weights_bind_bounds_identical=True)

def rewrite(path,dest,transform=baseline):
    original=IgbFile(Path(path).read_bytes());f=IgbFile(original.data);app=Appender(f)
    replacements={};allowed=set();allowed_fields=set();rows=[];used=set()
    for ta in f.objects_of('igTextureAttr'):
        base=f.obj(ta.get(12))
        if base is None or base.name!='igImage' or base.ref in used:continue
        miplist=f.obj(ta.get(16));mips=[f.obj(r) for r in (f.list_items(miplist.ref) if miplist else [])]
        chain=[base]+mips
        if any(i is None for i in chain):raise ValueError('Invalid mip chain')
        if len({i.get(11) for i in chain})!=1:raise ValueError('Mixed formats in mip chain')
        original_size=[base.get(2),base.get(3)]
        if base.get(19)%base.get(2):raise ValueError('Unsupported nonintegral image stride')
        pitch_factor=base.get(19)//base.get(2)
        if pitch_factor<=0:raise ValueError('Invalid image stride')
        start=time.perf_counter();up=transform(decode(original,original.obj(base.ref)));fmt=base.get(11)
        sizes=[];w,h=up.size
        while True:
            sizes.append((w,h))
            if (w,h)==(1,1):break
            w,h=max(1,w//2),max(1,h//2)
        refs=[]
        for k,size in enumerate(sizes):
            image=up if k==0 else up.resize(size,Image.Resampling.LANCZOS)
            data=encode(image,fmt);pitch=max(4,size[0])*pitch_factor
            if k<len(chain):
                im=chain[k];ref=im.ref;replacements[im.get(13)]=data;allowed.add(im.get(13));used.add(ref)
                for s,v in {2:size[0],3:size[1],12:len(data),19:pitch}.items():put_int(f,im,s,v);allowed_fields.add((ref,s))
            else:
                # igImage names participate in resource caching: a cloned mip
                # must not reuse its parent's name (including on unload/reload).
                mipname=base.get(22)[:-4]+f'.m{k:02x}'
                pix=app.memory(data,base.get(13));ref=app.clone(base,{2:size[0],3:size[1],12:len(data),13:pix,19:pitch,22:mipname})
            refs.append(ref)
        if miplist:
            raw=struct.pack('<'+'i'*(len(refs)-1),*refs[1:])
            if miplist.get(4)>=0:
                replacements[miplist.get(4)]=raw;allowed.add(miplist.get(4))
            else:
                mem=app.memory(raw);put_int(f,miplist,4,mem);allowed_fields.add((miplist.ref,4))
            for s in [2,3]:put_int(f,miplist,s,len(refs)-1);allowed_fields.add((miplist.ref,s))
        elif len(refs)>1:
            template=next(iter(f.objects_of('igImageMipMapList')),None)
            if template is None:raise ValueError('No mip-list object schema; cannot create full chain')
            mem=app.memory(struct.pack('<'+'i'*(len(refs)-1),*refs[1:]));ml=app.clone(template,{2:len(refs)-1,3:len(refs)-1,4:mem})
            put_int(f,ta,16,ml);allowed_fields.add((ta.ref,16))
        put_int(f,ta,15,len(refs));allowed_fields.add((ta.ref,15))
        rows.append(dict(name=base.get(22),original=original_size,output=list(up.size),format=FORMATS[fmt],mips=sizes,seconds=time.perf_counter()-start))
    result=app.finish(replacements);check=IgbFile(result)
    invariant=texture_invariant(original,check,allowed,allowed_fields)
    # Re-decode every output mip, validating byte count and same format.
    for im in check.objects_of('igImage'):decode(check,im)
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(result)
    return dict(source=str(path),output=str(dest),source_sha256=hashlib.sha256(original.data).hexdigest(),output_sha256=hashlib.sha256(result).hexdigest(),textures=rows,**invariant)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--port',required=True,type=Path);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--ai-model',type=Path);a=ap.parse_args()
    if a.output.resolve().is_relative_to(a.port.resolve()):ap.error('Output must be outside read-only port')
    roster=json.loads(Path('evidence/inventory.json').read_text());transform=AI(a.ai_model) if a.ai_model else baseline
    report={}
    for name,row in roster.items():
        if row['kind']!='hero':continue
        p=a.port/'Actors'/(row['target']+'.IGB');report[name]=rewrite(p,a.output/'Actors'/p.name,transform)
        print(name,report[name]['textures'],flush=True)
    (a.output/'mod.json').write_text(json.dumps(dict(name=a.output.name,description='Locally generated texture-only upgrade; owned assets, do not distribute'),indent=2))
    (a.output/'readme.txt').write_text('Generated locally from your owned port. Do not distribute. Remove this folder and restart to return to stock.\n')
    Path('evidence/sharpened-invariants.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':main()
