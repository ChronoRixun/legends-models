"""Resolve actor texture handles through exported IGZ handles; write native DDS.

No filename guessing: igSceneTexturesInfo paths locate files, and their exported
handle list maps each material's 64-bit texture handle to its igImage2.
"""
import argparse,json,struct
from pathlib import Path
from igz import IGZ,require
from mua_bin import Archive

def dds(image):
    w,h=image['width'],image['height'];levels=image['levels']
    header=struct.pack('<7I',124,0xa1007,h,w,len(image['mips'][0]),0,levels)+bytes(44)
    header+=struct.pack('<II4s5I',32,4,image['format'].encode(),0,0,0,0,0)+struct.pack('<5I',0x401008 if levels>1 else 0x1000,0,0,0,0)
    return b'DDS '+header+b''.join(image['mips'])

def resolve(actor,game,out):
    f=IGZ.read(actor);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    archives={};by_handle={};records=[]
    for info in f.of_type('igSceneTexturesInfo'):
        ref_list=f.u64(info+40)
        paths=[f.strings[i] for i in f.list(ref_list)]
        for path in paths:
            path=path.replace('\\','/');stem=path.split('/')[0]
            if stem not in archives:
                archive=Archive(Path(game)/(stem+'.bin'));archives[stem]=(archive,{e[0].lower():e for e in archive.entries})
            archive,entries=archives[stem];require(path.lower() in entries,'Missing texture archive member '+path)
            tf=IGZ(archive.read(entries[path.lower()]),path);images={im['address']:im for im in tf.images()}
            for handle,addr in tf.exported_handles().items():
                require(addr in images,'Texture handle does not export an image')
                im=images[addr];name=Path(path).stem+'.dds';(out/name).write_bytes(dds(im))
                record={k:v for k,v in im.items() if k!='mips'};record.update(handle=hex(handle),archive_path=path,dds=str(out/name))
                require(handle not in by_handle or by_handle[handle]['archive_path']==path,'Conflicting texture export')
                by_handle[handle]=record;records.append(record)
    sk=f.skeleton();meshes=f.meshes(sk)
    for m in meshes:
        for t in m['metadata']['textures']:
            handle=int(t['hash'],16);require(handle in by_handle,'Unresolved material texture '+t['hash'])
            t.update(archive_path=by_handle[handle]['archive_path'],dds=by_handle[handle]['dds'])
    report=dict(actor=str(actor),textures=records,meshes=[m['metadata'] for m in meshes])
    (out/'textures.json').write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('actor');ap.add_argument('--game',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();print(json.dumps(resolve(a.actor,a.game,a.out),indent=2))
