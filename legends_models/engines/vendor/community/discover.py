from pathlib import Path
from html.parser import HTMLParser
import html,json,re,urllib.request,time
class Node:
 def __init__(self,tag='',attrs=()): self.tag=tag;self.attrs=dict(attrs);self.children=[]
 def text(self): return ''.join(c.text() if isinstance(c,Node) else c for c in self.children)
 def walk(self):
  yield self
  for c in self.children:
   if isinstance(c,Node):yield from c.walk()
class Tree(HTMLParser):
 def __init__(self,s):
  super().__init__();self.root=Node();self.stack=[self.root];self.feed(s)
 def handle_starttag(self,t,a):
  n=Node(t,a);n.parent=self.stack[-1];self.stack[-1].children.append(n)
  if t not in ['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']:self.stack.append(n)
  if t in ['br','hr']:self.stack[-1].children.append('\n')
 def handle_endtag(self,t):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==t:self.stack=self.stack[:i];break
 def handle_data(self,d):self.stack[-1].children.append(d)
def fetch(u):
 req=urllib.request.Request(u.replace('http://','https://'),headers={'User-Agent':'Mozilla/5.0'})
 with urllib.request.urlopen(req,timeout=45) as r:return r.read()
ROSTER=['Beast','Colossus','Cyclops','Emma Frost','Gambit','Iceman','Jubilee','Magma','Nightcrawler','Jean Grey','Professor X','Psylocke','Rogue','Storm','Wolverine','Magneto','Juggernaut','Sabretooth','Toad','Pyro']
def slug(s):return s.lower().replace(' ','-')
def main():
 p=Path('evidence/index.html');t=Tree(p.read_text(encoding='utf8'));nodes=list(t.root.walk());found={}
 last=''
 for n in nodes:
  if n.tag=='b' and n.text().strip().lower() in [c.lower() for c in ROSTER]:last=next(c for c in ROSTER if c.lower()==n.text().strip().lower())
  if n.tag=='a' and n.text().strip() in ['For XML2','For XML1/XML2'] and last:
   found[last]=n.attrs['href'].replace('http://','https://');last=''
 Path('evidence/sources.json').write_text(json.dumps(found,indent=2))
 print('Found',len(found),found,flush=True)
 for c in ROSTER:
  if c not in found:continue
  u=found[c];p=Path('evidence')/(slug(c)+'.html')
  if not p.exists():p.write_bytes(fetch(u));time.sleep(1)
  tree=Tree(p.read_text(encoding='utf8'));mid=re.search(r'#msg(\d+)',u)[1]
  body=next((n for n in tree.root.walk() if n.attrs.get('id')=='msg_'+mid),None)
  if body is None:
   print('NO BODY',c,[n.attrs.get('id') for n in tree.root.walk() if 'msg' in n.attrs.get('id','')][:15]);continue
  Path('evidence',slug(c)+'.txt').write_text(body.text(),encoding='utf8')
  links=[{'text':n.text(),'url':n.attrs['href']} for n in body.walk() if n.tag=='a' and 'mediafire.com' in n.attrs.get('href','')]
  images=[n.attrs['src'] for n in body.walk() if n.tag=='img']
  Path('evidence',slug(c)+'-links.json').write_text(json.dumps({'character':c,'post':u,'links':links,'images':images},indent=2))
  print(c,len(links),[x['text'] for x in links],flush=True)
if __name__=='__main__':main()
