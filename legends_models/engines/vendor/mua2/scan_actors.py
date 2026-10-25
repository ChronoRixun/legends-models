"""Validate every actor's container and skin, retaining explicit failures."""
import argparse,collections,json,time
from pathlib import Path
from mua_bin import Archive
from igz import IGZ

def scan(archive,output):
    start=time.monotonic();results=[];archive=Archive(archive)
    for e in archive.entries:
        row={'name':e[0]}
        try:
            f=IGZ(archive.read(e),e[0]);row['container']=True
            if not f.of_type('igSkeleton2') and not f.of_type('igGeometry'):
                row.update(parsed=True,skin=False,kind='metadata-only actor; no skeleton or geometry')
            else:
                sk=f.skeleton();meshes=f.meshes(sk)
                row.update(parsed=True,skin=True,bones=len(sk['names']),meshes=len(meshes),vertices=sum(len(m['pos']) for m in meshes),triangles=sum(len(m['tri']) for m in meshes),
                           weight_sum_valid=all(m['metadata']['weight_sum_valid'] for m in meshes))
        except (ValueError,IndexError,KeyError) as ex:row['error']=str(ex)
        results.append(row)
    report={'total':len(results),'container_pass':sum(r.get('container',False) for r in results),'parse_pass':sum(r.get('parsed',False) for r in results),'skin_pass':sum(r.get('skin',False) for r in results),
            'errors':dict(collections.Counter(r['error'] for r in results if 'error' in r)), 'seconds':time.monotonic()-start,'files':results}
    Path(output).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('archive');ap.add_argument('--json',required=True);a=ap.parse_args();scan(a.archive,a.json)
