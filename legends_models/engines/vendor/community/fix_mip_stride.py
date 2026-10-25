"""Repair compressed block-row pitches; preserve every mesh and pixel byte."""
import hashlib,struct
from community_igb import IgbFile

def row_pitch(width,format):
 if width<1:raise ValueError('Invalid image width')
 if format==15:return ((width+3)//4)*16
 if format==14:return ((width+3)//4)*8
 if format==7:return width*4
 raise ValueError('Unproven pixel format '+str(format))

def fix_candidate(data):
 f=IgbFile(data)
 if f.header[11]!=0xf0000006:raise ValueError('Expected v6 cosmetic candidate')
 changes=[]
 for im in f.objects_of('igImage'):
  pitch=row_pitch(im.get(2),im.get(11))
  if im.get(19)!=pitch:
   changes.append({'image':im.ref,'width':im.get(2),'height':im.get(3),'format':im.get(11),'old_pitch':im.get(19),'new_pitch':pitch})
   struct.pack_into('<i',f.buf,im.fields[19].offset,pitch)
 output=f.edited();a,b=IgbFile(data),IgbFile(output)
 for ref,(off,size) in a.blocks.items():
  if data[off:off+size]!=output[off:off+size]:raise ValueError('Pixel or mesh memory changed')
 for old,new in zip(a.objects,b.objects):
  for k,v in old.fields.items():
   if old.name!='igImage' or k!=19:
    if v.value!=new.get(k):raise ValueError('Protected object field changed')
 return output,{'old_sha256':hashlib.sha256(data).hexdigest(),'new_sha256':hashlib.sha256(output).hexdigest(),'changes':changes,'all_mesh_skeleton_bounds_pixel_memory_identical':True,'only_changed_field':'igImage slot19 compressed block-row pitch'}
