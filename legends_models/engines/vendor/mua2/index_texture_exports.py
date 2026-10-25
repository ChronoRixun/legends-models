"""Index exported texture handles for actors that omit scene texture filenames."""
import argparse,collections,json
from pathlib import Path
from mua_bin import Archive
from igz import IGZ

def index(game,out):
    rows={};errors=[];files=0
    for stem in ['materials','textures']:
        archive=Archive(Path(game)/(stem+'.bin'))
        for entry in archive.entries:
            if not entry[0].lower().endswith('.igz'):continue
            try:
                f=IGZ(archive.read(entry),entry[0])
                for handle,addr in f.exported_handles().items():
                    if f.objects.get(addr)=='igImage2':rows.setdefault(hex(handle),[]).append(entry[0])
            except (ValueError,IndexError,KeyError) as ex:errors.append({'name':entry[0],'error':str(ex)})
            files+=1
            if files%500==0:print('exports',files,'handles',len(rows),'errors',len(errors),flush=True)
    result={'files':files,'handles':rows,'errors':errors};Path(out).write_text(json.dumps(result,indent=2))
    print('complete',files,'handles',len(rows),'errors',len(errors))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('game');ap.add_argument('--json',required=True);a=ap.parse_args();index(a.game,a.json)
