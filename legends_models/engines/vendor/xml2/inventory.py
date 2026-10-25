"""Read-only roster, rig and embedded texture inventory of an owned port."""
import argparse,json,sys,hashlib
from pathlib import Path
import numpy as np
from igb_file import IgbFile
from model import Model

HEROES=['beast','colossus','cyclops','frost','gambit','iceman','jubilee','magma','nightcrawler','phoenix','profxastral','psylocke','rogue','storm','wolverine']
VILLAINS=['magneto','juggernaut','sabretooth','mystique','toad','blob','pyro','avalanche']
SOURCES=dict(zip(HEROES,['0501','0901','0101','9001','1301','0801',None,None,'0601','0201','1104',None,'0701','0401','0301']))
SOURCES.update(dict(zip(VILLAINS,['2501','3401','2901','2801','3301','3001','3201','3101'])))
FORMATS={14:'DXT1/BC1',15:'DXT3/BC2',16:'DXT5/BC3'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def image_inventory(f):
    return [dict(ref=o.ref,name=o.get(22),width=o.get(2),height=o.get(3),format_id=o.get(11),format=FORMATS.get(o.get(11),'unknown'),bytes=o.get(12),pixel_ref=o.get(13),mips_ref=o.get(14)) for o in f.objects_of('igImage')]

def rig(f):
    sk=f.objects_of('igSkeleton')
    if not sk:return None
    sk=sk[0];bs=[f.obj(r) for r in f.list_items(sk.get(4))]
    return dict(count=len(bs),names=[b.get(2) for b in bs],parents=[b.get(3) for b in bs],bmidx=[b.get(4) for b in bs])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--out',type=Path,default=Path('evidence/inventory.json'));a=ap.parse_args()
    import port_xmlb as xmlb
    stats={};talents={}
    for name in ['herostat','npcstat']:
        for s in xmlb.decode((a.port/f'Data/{name}.engb').read_bytes()).iter('stats'):
            stats[s.get('name','').lower()]=dict(s.attrib)
            talents[s.get('name','').lower()]=[e.get('name') for e in s if e.tag=='talent']
    result={}
    for name in HEROES+VILLAINS:
        st=stats.get(name+'scripted',{}) if name in VILLAINS else stats.get(name,{})
        target=st.get('skin');db=st.get('characteranims');source=SOURCES[name]
        representative=st
        if name in VILLAINS:
            choices=[s for s in stats.values() if s.get('skin')==target and s.get('characteranims')==db and s.get('powerstyle')]
            if choices:representative=max(choices,key=lambda s:len(talents.get(s.get('name','').lower(),[])))
        styles=[n for n in talents.get(representative.get('name','').lower(),[]) if n.startswith('fightstyle_')]
        row=dict(character=name,kind='hero' if name in HEROES else 'villain',target=target,source=source,animation_db=db,stats=st,fightstyles=styles,reference_stats=representative)
        for key,stem in [('stock',target),('source',source),('animation',db)]:
            if not stem:continue
            p=a.port/'Actors'/(stem+'.IGB')
            if not p.exists():row[key+'_error']='missing '+str(p);continue
            try:
                f=IgbFile(p.read_bytes());r=dict(path=str(p),sha256=sha(p),rig=rig(f),images=image_inventory(f),geometries=[g.get(2) for g in f.objects_of('igGeometry')],animations=[x.get(2) for x in f.objects_of('igAnimation')]);row[key+'_info']=r
                if key in ('stock','source'):
                    m=Model(p);r.update(vertices=len(m.pos),triangles=len(m.tri),bounds=[m.pos.min(0).tolist(),m.pos.max(0).tolist()],palette=m.palette.tolist())
            except Exception as e:row[key+'_error']=repr(e)
        if source is None:row['source_error']='No XML2 mesh found; the named animation DB is XML1 content. Numeric XML2 IDs belong to other characters.'
        if 'stock_info' in row and 'source_info' in row:
            s,t=row['source_info'],row['stock_info'];row['same_rig_records']=s['rig']==t['rig']
            try:
                sm,tm=[Model(a.port/'Actors'/(stem+'.IGB')) for stem in [source,target]]
                row['shared_bones']=len(set(sm.names)&set(tm.names));row['source_only_bones']=sorted(set(sm.names)-set(tm.names));row['target_only_bones']=sorted(set(tm.names)-set(sm.names))
                row['max_shared_bind_delta']=max(float(np.abs(sm.world[sm.names.index(n)]-tm.world[tm.names.index(n)]).max()) for n in set(sm.names)&set(tm.names))
            except Exception as e:row['compare_error']=repr(e)
        result[name]=row
        print(name,'stock',target,'source',source,'db',db,'rig',row.get('same_rig_records'),'bind',row.get('max_shared_bind_delta'),'errors',[v for k,v in row.items() if k.endswith('_error')],flush=True)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    costumes={}
    for name,row in result.items():
        prefix=row['target'][:-2]
        for p in sorted((a.port/'Actors').glob(prefix+'??.IGB')):
            if not p.stem.isdigit():continue
            f=IgbFile(p.read_bytes());costumes[p.name]=dict(character=name,sha256=sha(p),images=image_inventory(f))
    (a.out.parent/'costume-textures.json').write_text(json.dumps(costumes,indent=2))

if __name__=='__main__':main()
