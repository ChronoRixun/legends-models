"""Independent structural/format checks on generated texture-only actors."""
import argparse,json,hashlib,struct
from pathlib import Path
from igb_file import IgbFile
from round1_converter import blob
from upscale import decode

def check(source,output):
    a,b=[IgbFile(p.read_bytes()) for p in [source,output]]
    images={o.ref for o in a.objects_of('igImage')};lists={o.ref for o in a.objects_of('igImageMipMapList')};textures={o.ref for o in a.objects_of('igTextureAttr')}
    allowed=set();fields=set()
    for o in a.objects_of('igImage'):allowed.add(o.get(13));fields.update((o.ref,s) for s in [2,3,12,19])
    for o in a.objects_of('igImageMipMapList'):
        if o.get(4)>=0:allowed.add(o.get(4))
        fields.update((o.ref,s) for s in [2,3,4])
    fields.update((o.ref,s) for o in a.objects_of('igTextureAttr') for s in [15,16])
    compared=0
    for o in a.objects:
        n=b.obj(o.ref);assert n and n.name==o.name
        for slot,v in o.fields.items():
            if (o.ref,slot) not in fields:assert v.value==n.get(slot),(o.ref,slot);compared+=1
    for ref in a.blocks:
        if ref not in allowed:assert blob(a,ref)==blob(b,ref),ref;compared+=1
    names=[o.get(22) for o in b.objects_of('igImage')];assert len(names)==len(set(names)),'Duplicate texture resource name'
    chains=[]
    for ta in b.objects_of('igTextureAttr'):
        if ta.get(12)<0:continue
        chain=[b.obj(ta.get(12))];ml=b.obj(ta.get(16))
        if ml:chain.extend(b.obj(r) for r in b.list_items(ml.ref))
        assert ta.get(15)==len(chain),'Mip count mismatch'
        original=a.obj(a.obj(ta.ref).get(12));assert chain[0].get(2)==original.get(2)*4
        for k,o in enumerate(chain):
            assert o.get(11)==original.get(11),'DXT format changed'
            assert (o.get(2),o.get(3))==(max(1,chain[0].get(2)>>k),max(1,chain[0].get(3)>>k))
            assert o.get(19)==max(4,o.get(2))*original.get(19)//original.get(2),'Incorrect small-mip pitch'
            decode(b,o)
        assert chain[-1].get(2)==chain[-1].get(3)==1,'Incomplete mip chain'
        chains.append([o.get(2) for o in chain])
    return dict(pass_structural=True,exact_nontexture_comparisons=compared,chains=chains,sha256=hashlib.sha256(output.read_bytes()).hexdigest())

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',required=True,type=Path);ap.add_argument('--mod',required=True,type=Path);a=ap.parse_args()
    report={}
    for p in sorted((a.mod/'Actors').glob('*.IGB')):
        report[p.name]=check(a.port/'Actors'/p.name,p);print(p.name,'PASS',report[p.name]['chains'],flush=True)
    Path('evidence/texture-independent-check.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':main()
