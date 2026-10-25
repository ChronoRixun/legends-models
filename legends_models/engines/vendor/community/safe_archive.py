"""Strict local quarantine validation. Never execute archive members."""
from pathlib import PurePosixPath
ALLOWED={'.igb','.dds','.png','.xmlb','.engb','.txt','.md','.jpg','.jpeg','.bmp','.tga','.gif','.webp','.psd','.rtf'}
def check_members(names):
 if not names:raise ValueError('Empty archive')
 for name in names:
  q=name.replace('\\','/');p=PurePosixPath(q)
  if p.is_absolute() or ':' in q or any(s in {'.','..'} or s.endswith(('.', ' ')) for s in q.rstrip('/').split('/')):raise ValueError('Unsafe path: '+name)
  if q.endswith('/'):continue
  if p.suffix.lower() not in ALLOWED:raise ValueError('Forbidden or unknown archive member: '+name)
 return True


def archive_name(url):
 """Return a single owner-approved archive basename, never a decoded path."""
 from urllib.parse import urlparse,unquote
 import re
 parts=urlparse(url).path.split('/')
 if len(parts)<3 or parts[-1]!='file':raise ValueError('Unexpected MediaFire path')
 name=unquote(parts[-2])
 if not re.fullmatch(r'[0-9]{2,3}XX_[A-Za-z0-9_()-]+\.rar',name):raise ValueError('Unsafe or unapproved archive filename: '+name)
 return name
