"""Resolve the six named stress clips without inventing absent hero tracks.

Prefer the hero DB. Common locomotion/pain and generic hero fighting fill absent
tracks. f_heavy_attack_4 exists only in Wolverine's DB, used explicitly as the
shared heavy stress reference. This does not install or retarget animations.
"""
import json
from pathlib import Path
from igb_file import IgbFile
from export_animations import export
from validate import REFERENCE_CLIPS

def prepare(port,animation_db,out):
    port,out=Path(port),Path(out);out.mkdir(parents=True,exist_ok=True)
    todo=set(REFERENCE_CLIPS);mapping={};details={}
    for db in dict.fromkeys([animation_db,'common.IGB','fightstyle_hero.IGB','x1_03_wolverine.IGB']):
        f=IgbFile((port/'Actors'/db).read_bytes())
        names={a.get(2) for a in f.objects_of('igAnimation')} & todo
        if not names:continue
        manifest=export(port/'Actors'/db,port,out/db,names)
        for name in manifest:mapping[name]=db;details[name]=manifest[name]
        todo-=set(manifest)
    if todo:raise ValueError('Missing reference animations: '+', '.join(sorted(todo)))
    (out/'reference-map.json').write_text(json.dumps({'hero_db':animation_db,'clips':mapping,'details':details},indent=2))
    return [(n,mapping[n]) for n in REFERENCE_CLIPS]
