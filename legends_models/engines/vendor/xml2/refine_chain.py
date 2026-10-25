"""Fit real vertex positions to stock chain envelopes; never changes limits/bones.

Seam vertices share one position variable. Smooth displacements and explicit
body/hull edge penalties preserve the authored garment while correcting poses.
Final serialized output still requires the independent full gate and review.
"""
import argparse,json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from model import Model,pose
from validate import edges
from validate_roster import clips,measure
from convert_xml2 import convert

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,required=True);ap.add_argument('--character',required=True);ap.add_argument('--iterations',type=int,default=100);a=ap.parse_args()
    root=Path(__file__).resolve().parents[1];roster=json.loads((root/'evidence/inventory.json').read_text());row=dict(roster[a.character]);name=a.character+'-chainfit';row['character']=name
    old=dict(np.load(root/'cache/fits'/a.character/'fit.npz'));m=Model(a.port/'Actors'/(row['target']+'.IGB'));source=Model(a.port/'Actors'/(row['source']+'.IGB'))
    # Existing fitter makes positions at every UV seam exactly identical.
    p0,rep,group=np.unique(old['pos'],axis=0,return_index=True,return_inverse=True);G=len(p0)
    hn=old['outline_norm'][rep];weights=old['weights'][rep];indices=old['indices'][rep]
    by={b.get(4):i for i,b in enumerate(m.bones) if b.get(4)>=0};bi=np.array([by[int(i)] for i in m.palette])[indices]
    e=np.unique(np.sort(group[edges(source)],axis=1),axis=0);e=e[e[:,0]!=e[:,1]]
    ref=clips(a.port,row,root/'cache/animations')
    gatepath=root/'evidence/final-gates'/(a.character+'.json')
    if not gatepath.exists():
        original=dict(row);original['character']=a.character
        measure(a.port,original,root/'evidence/fitted'/(a.character+'.IGB'),root/'evidence/final-gates',False)
    stock=json.loads(gatepath.read_text())
    worlds=[];caps=[]
    for clip,path in ref.items():
        anim=np.load(path)
        for qt in anim['qt']:
            worlds.append(pose(m,anim['names'],qt,anim['use_translation']));caps.append(stock['clips'][clip]['models']['stock']['max_chain_distance']*.99)
    worlds=np.asarray(worlds);caps=np.asarray(caps);F=len(worlds)
    matrices=m.inverse_by_bone[None]@worlds
    transforms=np.einsum('fnkij,nk->nfij',matrices[:,bi],weights)
    A=transforms[:,:,:3,:3];B=transforms[:,:,3,:3]
    segs=[]
    for ids,ws in zip(bi,weights):
        pairs=set()
        for bone,w in zip(ids,ws):
            if w<=1e-5:continue
            if m.parent[bone]>=0:pairs.add((int(bone),int(m.parent[bone])))
            pairs.update((int(bone),int(j)) for j in np.where(m.parent==bone)[0])
        segs.append(sorted(pairs))
    S=max(map(len,segs));segs=np.array([s+[s[0]]*(S-len(s)) for s in segs])
    bonepos=worlds[:,:,3,:3];sa=bonepos[:,segs[:,:,0]].transpose(1,0,2,3);sv=bonepos[:,segs[:,:,1]].transpose(1,0,2,3)-sa;ss=np.maximum((sv*sv).sum(3),1e-15)
    edgeweight=1/np.maximum(np.linalg.norm(p0[e[:,0]]-p0[e[:,1]],axis=1),.2)**2
    calls=0;last={}
    def fun(x):
        global calls,last
        p=x.reshape(G,3);grad=np.zeros_like(p);loss=0.;worst=0.;maxedge=0.
        for offset in [np.zeros_like(hn),hn*.28]:
            q=p+offset;world=np.einsum('ni,nfij->nfj',q,A)+B
            diff=world[:,:,None,:]-sa;u=np.clip((diff*sv).sum(3)/ss,0,1);d=diff-u[:,:,:,None]*sv;d2=(d*d).sum(3);ix=d2.argmin(2)
            nearest=np.take_along_axis(d,ix[:,:,None,None],axis=2)[:,:,0,:];dist=np.sqrt(np.take_along_axis(d2,ix[:,:,None],axis=2)[:,:,0]);over=np.maximum(dist-caps[None],0)
            loss+=20*(over*over).sum();gw=40*over[:,:,None]*nearest/np.maximum(dist[:,:,None],1e-12);grad+=np.einsum('nfj,nfij->ni',gw,A);worst=max(worst,float((dist/caps[None]).max()))
            ew=world[e[:,0]]-world[e[:,1]];dn=np.linalg.norm(ew,axis=2);bind=q[e[:,0]]-q[e[:,1]];le=np.maximum(np.linalg.norm(bind,axis=1),1e-7);ratio=dn/le[:,None];over=np.maximum(ratio-1.8,0);loss+=5*(over*over).sum();maxedge=max(maxedge,float(ratio.max()))
            g=10*over[:,:,None]*ew/(np.maximum(dn[:,:,None],1e-12)*le[:,None,None]);ga=np.einsum('efj,efij->ei',g,A[e[:,0]]);gb=-np.einsum('efj,efij->ei',g,A[e[:,1]])
            gl=10*np.sum(over*dn,axis=1)[:,None]*bind/le[:,None]**3;ga-=gl;gb+=gl
            np.add.at(grad,e[:,0],ga);np.add.at(grad,e[:,1],gb)
        disp=p-p0;dd=disp[e[:,0]]-disp[e[:,1]];loss+=.1*(disp*disp).sum()+.5*(edgeweight[:,None]*dd*dd).sum();grad+=.2*disp
        gg=edgeweight[:,None]*dd;np.add.at(grad,e[:,0],gg);np.add.at(grad,e[:,1],-gg)
        calls+=1;last=dict(calls=calls,loss=float(loss),chain_to_internal_target=worst,max_edge=maxedge,max_displacement=float(np.linalg.norm(disp,axis=1).max()))
        if calls%10==0:print(last,flush=True)
        return loss,grad.ravel()
    result=minimize(fun,p0.ravel(),jac=True,method='L-BFGS-B',options=dict(maxiter=a.iterations,maxcor=10,ftol=1e-10));fun(result.x)
    old['pos']=result.x.reshape(G,3)[group];work=root/'cache/fits'/name;work.mkdir(parents=True,exist_ok=True);np.savez(work/'fit.npz',**old)
    (work/'refinement.json').write_text(json.dumps(dict(**last,frames=F,groups=G,solver=str(result.message),method='Real vertex displacement; smooth garment correction; unchanged stock limits and port skeleton/bounds'),indent=2))
    dest=root/'evidence/fitted'/(name+'.IGB');convert(source.path,m.path,dest,old);measure(a.port,row,dest,root/'evidence/fitted-gates',False)
