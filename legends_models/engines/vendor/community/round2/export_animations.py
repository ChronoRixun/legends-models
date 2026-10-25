"""Decode owned XML2 Enbaya streams with a local x86 DLL helper, preserving track masks."""
import argparse, hashlib,json,struct,subprocess
from pathlib import Path
import numpy as np
from igb_file import IgbFile
from model import array
from round1_converter import blob

def export(path,dll_dir,out,only=None):
    digest=hashlib.sha256((Path(dll_dir)/'libIGSg.dll').read_bytes()).hexdigest()
    if digest!='832e64ede714b47116d5cc0885507d24fe3793fbbcfae3d987d66dfd5820f72c':
        raise ValueError('Unsupported libIGSg.dll build; refusing to invoke version-specific decoder RVAs')
    f=IgbFile(Path(path).read_bytes());out=Path(out);out.mkdir(parents=True,exist_ok=True)
    result={}
    for anim in f.objects_of('igAnimation'):
        name=anim.get(2)
        if only and name not in only:continue
        duration=anim.get(9)/1e9
        tracks=[f.obj(r) for r in f.list_items(anim.get(5))]
        sources={f.obj(t.get(3)).get(3) for t in tracks if f.obj(t.get(3)) and f.obj(t.get(3)).name=='igEnbayaTransformSource'}
        rates={struct.unpack_from('<I',blob(f,f.obj(ref).get(2)),16)[0] for ref in sources}
        ticks={0,anim.get(9)}
        for rate in rates|{60}:
            if not 0<rate<=120:raise ValueError(f'Unsupported native sample rate {rate}')
            ticks.update(round(k*1e9/rate) for k in range(int(np.floor(duration*rate))+1))
        for t in tracks:
            s=f.obj(t.get(3))
            if s and s.name=='igTransformSequence1_5' and s.get(11)>=0:
                ticks.update(int(x) for x in array(f,f.obj(s.get(11)).get(4),'<i8') if 0<=x<=anim.get(9))
        times_ns=np.array(sorted(ticks),dtype='<i8')
        if len(times_ns)<2:times_ns=np.array([0,0],dtype='<i8')
        count=len(times_ns);times_path=out/f'{name}.times';times_path.write_bytes(times_ns.tobytes())
        decoded={}
        for ref in sources:
            obj=f.obj(ref);data=blob(f,obj.get(2));ip=out/f'{name}-{ref}.stream';op=out/f'{name}-{ref}.samples'
            if obj.get(3)!=66305 or obj.get(4)!=3:raise ValueError('Sampler requires the verified linear/slerp channel configuration')
            if abs(struct.unpack_from('<f',data,12)[0]-duration)>1e-4:raise ValueError('Animation/stream duration mismatch')
            ip.write_bytes(data)
            subprocess.run([str(Path(__file__).with_name('sample_enbaya.exe')),str(dll_dir),str(ip),str(op),str(count),str(times_path)],check=True,capture_output=True)
            b=op.read_bytes();nb,nf,fps,dur=struct.unpack('<IIIf',b[:16]);decoded[ref]=np.frombuffer(b[16:],'<f4').reshape(nf,nb,7)
        qt=np.zeros((count,len(tracks),7));names=[]
        for j,t in enumerate(tracks):
            names.append(t.get(2));s=f.obj(t.get(3))
            if s is None:
                qt[:,j,:4]=t.get(4);qt[:,j,4:]=t.get(5)
            elif s.name=='igEnbayaTransformSource':qt[:,j]=decoded[s.get(3)][:,s.get(2)]
            elif s.name=='igTransformSequence1_5':
                times=array(f,f.obj(s.get(11)).get(4),'<i8')/1e9
                p=array(f,f.obj(s.get(2)).get(4),width=3) if s.get(2)>=0 else np.zeros((len(times),3))
                q=array(f,f.obj(s.get(3)).get(4),width=4)
                for k in range(count):
                    tm=times_ns[k]/1e9;ix=max(0,min(len(times)-2,np.searchsorted(times,tm)-1))
                    frac=np.clip((tm-times[ix])/(times[ix+1]-times[ix]),0,1)
                    qa,qb=q[ix].copy(),q[ix+1].copy()
                    if np.dot(qa,qb)<0:qb=-qb
                    v=qa*(1-frac)+qb*frac;v/=np.linalg.norm(v)
                    qt[k,j,:4]=v;qt[k,j,4:]=p[ix]*(1-frac)+p[ix+1]*frac
            else:raise ValueError(f'unsupported track source {s.name}')
        bitmask=f.obj(anim.get(10));words=array(f,bitmask.get(4),'<u4') if bitmask else []
        mask=np.array([bool(words[i//32] & (1<<(i%32))) if len(words) else False for i in range(len(tracks))])
        np.savez(out/f'{name}.npz',qt=qt,names=names,use_translation=mask,duration=duration,times_ns=times_ns)
        result[name]={'duration':duration,'samples':count,'native_key_rates':sorted(rates),'includes_exact_keys':True,'track_count':len(tracks),'dynamic_translation_tracks':[n for n,m in zip(names,mask) if m]}
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    manifests={}
    for filename in ['x1_03_wolverine.IGB','common.IGB']:
        path=args.port/'Actors'/filename
        if path.exists():manifests[filename]=export(path,args.port,args.out/filename, None if 'wolverine' in filename else {'pain_high'})
    manifests['dll_sha256']=hashlib.sha256((args.port/'libIGSg.dll').read_bytes()).hexdigest()
    (args.out/'manifest.json').write_text(json.dumps(manifests,indent=2))
    print(json.dumps(manifests,indent=2))
