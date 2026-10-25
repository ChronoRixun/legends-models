"""Deterministic orthographic textured triangle rasterizer; evidence, not beautification."""
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont

def render(model,positions,tex,view='front',title='',height=1024,width=768,bounds=None,outline=False):
    # Wolverine faces +X; Z up. Front image left corresponds to his right side.
    axes={'front':(1,2,0),'back':(1,2,0),'side':(0,2,1)}[view]
    p=np.asarray(positions)[:,axes].copy()
    if view=='front':p[:,0]*=-1
    if view=='back':p[:,2]*=-1
    if view=='side':p[:,0]*=-1
    lo,hi=(p[:,:2].min(0),p[:,:2].max(0)) if bounds is None else bounds
    scale=min((width-100)/max(hi[0]-lo[0],1),(height-100)/max(hi[1]-lo[1],1))
    xy=p[:,:2].copy();xy[:,0]=(xy[:,0]-(lo[0]+hi[0])/2)*scale+width/2
    xy[:,1]=height-40-(xy[:,1]-lo[1])*scale
    rgb=np.full((height,width,3),[42,47,56],dtype=np.uint8);zbuf=np.full((height,width),-np.inf)
    tx=np.asarray(tex);th,tw=tx.shape[:2];uv=model.uv
    for tri in model.tri:
        pts=xy[tri];z=p[tri,2]
        xmin=max(0,int(np.floor(pts[:,0].min())));xmax=min(width-1,int(np.ceil(pts[:,0].max())))
        ymin=max(0,int(np.floor(pts[:,1].min())));ymax=min(height-1,int(np.ceil(pts[:,1].max())))
        if xmin>xmax or ymin>ymax:continue
        a,b,c=pts;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(den)<1e-8:continue
        yy,xx=np.mgrid[ymin:ymax+1,xmin:xmax+1];xx=xx+.5;yy=yy+.5
        wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
        wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den;wc=1-wa-wb
        depth=wa*z[0]+wb*z[1]+wc*z[2]
        sub=zbuf[ymin:ymax+1,xmin:xmax+1];mask=(wa>=-1e-7)&(wb>=-1e-7)&(wc>=-1e-7)&(depth>sub)
        if not mask.any():continue
        texuv=wa[mask,None]*uv[tri[0]]+wb[mask,None]*uv[tri[1]]+wc[mask,None]*uv[tri[2]]
        u=np.mod(texuv[:,0],1);v=np.mod(texuv[:,1],1)
        color=tx[np.minimum((v*th).astype(int),th-1),np.minimum((u*tw).astype(int),tw-1)]
        # Soft fixed illumination exposes silhouette and folds without replacing diffuse detail.
        cross=np.cross(p[tri[1]]-p[tri[0]],p[tri[2]]-p[tri[0]])
        norm=cross/(np.linalg.norm(cross) or 1)
        light=.65+.35*abs(np.dot(norm,[.2,.4,.8944]))
        rgb[ymin:ymax+1,xmin:xmax+1][mask]=np.clip(color*light,0,255).astype(np.uint8);sub[mask]=depth[mask]
    if outline:
        mask=np.isfinite(zbuf);edge=np.zeros_like(mask)
        for dy,dx in [(0,2),(0,-2),(2,0),(-2,0)]:edge|=np.roll(mask,(dy,dx),(0,1))
        rgb[edge & ~mask]=[8,9,12]
    im=Image.fromarray(rgb);draw=ImageDraw.Draw(im)
    try:font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',22)
    except OSError:font=ImageFont.load_default()
    draw.text((18,14),title+' | '+view,fill=(240,240,245),font=font)
    return im

def triptych(model,pos,tex,path,title,outline=False):
    ims=[render(model,pos,tex,v,title,width=1200,outline=outline) for v in ('front','side','back')]
    result=Image.new('RGB',(sum(i.width for i in ims),ims[0].height))
    for j,im in enumerate(ims):result.paste(im,(j*im.width,0))
    result.save(path)
