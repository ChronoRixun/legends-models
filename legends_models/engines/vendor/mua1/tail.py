"""Render Nightcrawler's separate, unchanged tail in its explicit bind pose.

The independent 98_tail animation state is not inferred from body animation
names. Native captures show its actual runtime animation; offline views label
this attachment as a bind-pose reference, mounted on the sampled pelvis.
"""
import copy
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image
from model import Model,texture

@lru_cache(maxsize=4)
def tail_data(path,swap_rb):
    m=Model(path);t=texture(m)
    if swap_rb:t=Image.fromarray(np.asarray(t)[:,:,::-1])
    return m,t

def attach_tail(model,positions,world,tex,path,swap_rb=False):
    tail,tail_tex=tail_data(Path(path),swap_rb)
    parent=model.names.index('Bip01 Pelvis')
    points=(np.column_stack((tail.pos,np.ones(len(tail.pos))))@world[parent])[:,:3]
    width=max(tex.width,tail_tex.width);height=tex.height+tail_tex.height
    atlas=Image.new('RGB',(width,height));atlas.paste(tex,(0,0));atlas.paste(tail_tex,(0,tex.height))
    body_uv=np.mod(model.uv,1)*[tex.width/width,tex.height/height]
    tail_uv=np.mod(tail.uv,1)*[tail_tex.width/width,tail_tex.height/height]+[0,tex.height/height]
    composite=copy.copy(model);composite.tri=np.concatenate([model.tri,tail.tri+len(positions)])
    composite.uv=np.concatenate([body_uv,tail_uv])
    return composite,np.concatenate([positions,points]),atlas
