"""Sequential owner-scoped acquisition; inspected archives only, no execution."""
from pathlib import Path
from urllib.parse import urlparse,unquote
import json,re,hashlib,subprocess,time,shutil
from discover import Tree,fetch,slug,ROSTER
from safe_archive import check_members,archive_name
UNRAR=r'C:\Program Files\WinRAR\UnRAR.exe'
CHOICES={c:'Original' for c in ROSTER}
CHOICES.update({'Beast':'Classic','Cyclops':'Classic','Emma Frost':'Hellfire Club','Jubilee':'90s','Jean Grey':'Phoenix','Professor X':'Astral','Wolverine':'Original','Magneto':'Classic','Juggernaut':'Classic','Sabretooth':'Classic','Toad':'Classic','Pyro':'Classic'})
def save(m):Path('downloads/MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf8')
def main():
 manifest=json.loads(Path('downloads/MANIFEST.json').read_text()) if Path('downloads/MANIFEST.json').exists() else []
 selections=[]
 for c in ROSTER:
  d=json.loads(Path('evidence',slug(c)+'-links.json').read_text());wanted=[CHOICES[c],'Ultimate XML1']
  for cost in wanted:
   link=next((x for x in d['links'] if x['text']==cost),None)
   if not link:continue
   if not re.fullmatch(r'https://www\.mediafire\.com/file_premium/[^/]+/[^/]+\.rar/file',link['url']):raise ValueError('Not approved archive link '+link['url'])
   selections.append({'character':c,'costume':cost,'post':d['post'],**link})
 Path('evidence/selections.json').write_text(json.dumps(selections,indent=2))
 for row in selections:
  c,cost=row['character'],row['costume'];existing=next((x for x in manifest if x['url']==row['url']),None)
  if existing and existing.get('status')=='extracted':continue
  rec=dict(row);folder=Path('downloads',slug(c));folder.mkdir(parents=True,exist_ok=True);shutil.copyfile('CREDITS.md',folder/'CREDITS.md')
  archive=folder/archive_name(row['url']);rec['archive']=str(archive)
  try:
   if not archive.exists():
    cache=Path('evidence',slug(c)+'-'+slug(cost)+'-mediafire.html');page=cache.read_bytes() if cache.exists() else fetch(row['url']);cache.write_bytes(page)
    if page.startswith(b'Rar!'):
     data=page;rec['delivery']='MediaFire file_premium returned archive directly'
    else:
     tree=Tree(page.decode('utf8'));direct=next((n.attrs['href'] for n in tree.root.walk() if n.tag=='a' and n.attrs.get('id')=='downloadButton'),None)
     if not direct:raise ValueError('No MediaFire downloadButton')
     host=urlparse(direct).hostname or ''
     if not host.endswith('.mediafire.com'):raise ValueError('Unexpected direct host')
     data=fetch(direct)
    if len(data)>30_000_000:raise ValueError('Unexpected archive size')
    archive.write_bytes(data)
   rec.update(bytes=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
   result=subprocess.run([UNRAR,'lb','-c-',str(archive)],capture_output=True,text=True,errors='replace')
   listing=subprocess.run([UNRAR,'lt','-c-',str(archive)],capture_output=True,text=True,errors='replace')
   Path('evidence',slug(c)+'-'+slug(cost)+'-archive.txt').write_text(listing.stdout,encoding='utf8')
   if result.returncode or listing.returncode:raise ValueError('UnRAR listing failed')
   names=result.stdout.splitlines();rec['members']=names
   # UnRAR lb includes directory records without trailing slash; classify from lt.
   dirs=set(re.findall(r'(?m)^\s*Name: (.+)\n\s*Type: Directory',listing.stdout))
   check_members([x+'/' if x in dirs else x for x in names])
   if re.search(r'(?i)Type:.*(?:link|junction)|\bTarget:',listing.stdout):raise ValueError('Archive link refused')
   dest=Path('extracted',slug(c),slug(cost));dest.mkdir(parents=True,exist_ok=True)
   subprocess.run([UNRAR,'x','-o-','-y',str(archive),str(dest)+'\\'],check=True,capture_output=True)
   shutil.copyfile('CREDITS.md',dest/'CREDITS.md');rec.update(status='extracted',extracted=str(dest))
  except Exception as e:rec.update(status='refused_or_unavailable',error=str(e))
  if existing:manifest[manifest.index(existing)]=rec
  else:manifest.append(rec)
  save(manifest);print(c,cost,rec['status'],rec.get('bytes'),rec.get('error',''),flush=True);time.sleep(2)
if __name__=='__main__':main()
