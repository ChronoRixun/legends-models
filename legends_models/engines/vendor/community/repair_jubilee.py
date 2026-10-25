"""Jubilee authored wrist repair; reuses proven Magma fit/seam helpers.

Only writes diagnostics under output/jubilee-repaired and evidence/jubilee-repair.md.
Old native screenshots and original candidates remain unchanged.
"""
import json,sys,shutil,hashlib
from pathlib import Path
import numpy as np
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
import community_convert
from community_model import Model
from repair_magma import repair,dense_weights,pad4


def main():
    import validate
    from fit_chain import refine
    port=Path(__import__('os').environ['LEGENDS_PORT'])
    oldwork=ROOT/'output/jubilee/ultimate-xml1'
    conversion=json.loads((oldwork/'conversion.json').read_text())
    source_path=ROOT/conversion['source'];template=Path(conversion['template'])
    source,target=Model(source_path),Model(template)
    previous=dict(np.load(oldwork/'fit.npz'));animations=oldwork/'animations'
    db=json.loads((animations/'reference-map.json').read_text())['hero_db']
    dest=ROOT/'output/jubilee-repaired';dest.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'CREDITS.md',dest/'CREDITS.md')
    attempts=[];chosen=None
    for mode in ['authored','localized']:
        fitted,report=repair(source,target,previous,mode=mode)
        out=dest/mode;out.mkdir(parents=True,exist_ok=True)
        candidate=out/'15601.IGB'
        summary=community_convert.convert_candidate(source_path,template,candidate,fitted)
        validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
            render_root=None,animation_db=db,target_id='15601',claws=False)
        report.update(conversion=summary,initial_acceptance=validation['acceptance'])
        failures=validation['acceptance']['failures']
        if failures and all('max_chain_distance' in failure for failure in failures):
            # Explicit 2.5% cosmetic bound authorized for this diagnostic pass.
            fitted,chain=refine(source,target,fitted,port,animations,db,max_displacement_fraction=.025)
            report['chain_refinement']=chain
            if chain['feasible']:
                summary=community_convert.convert_candidate(source_path,template,candidate,fitted)
                validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
                    render_root=None,animation_db=db,target_id='15601',claws=False)
                report['conversion']=summary
        report['final_acceptance']=validation['acceptance']
        np.savez(out/'fit.npz',**fitted)
        if validation['acceptance']['numeric_pass']:
            # Produce the complete required 20 PNG set using the unchanged validator.
            validation=validate.evaluate(port,source_path,candidate,animations,out/'validation.json',
                render_root=out/'renders',animation_db=db,target_id='15601',claws=False)
            report['final_acceptance']=validation['acceptance'];chosen=out
        (out/'repair.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        attempts.append(report)
        print('Jubilee repair',mode,validation['acceptance'],flush=True)
        if chosen is not None:break
    lines=['# Jubilee authored wrist overlap repair','',
        'The source uses separate cuff/hand shells with noncoincident overlapping boundary rings. Independent smoothing introduced finger weights at the hand root while the cuff stayed on hand/forearm weights. This tool restores authored influences and bind refit, shares exact source seam groups, and never welds the separated shell surfaces.','',
        '| Attempt | Restored vertices | Max bind position change | Numeric pass |','| --- | ---: | ---: | --- |']
    for report in attempts:
        lines.append(f"| {report['mode']} | {report['restored_vertices']} | {report['max_position_change_from_round2']:.6f} | {report['final_acceptance']['numeric_pass']} |")
        lines+=['',json.dumps(report['final_acceptance'],indent=2),'']
    if chosen is not None:
        lines+=['',f"Passing candidate: `{chosen.relative_to(ROOT).as_posix()}/15601.IGB`.",
            f"SHA256: `{hashlib.sha256((chosen/'15601.IGB').read_bytes()).hexdigest()}`.",
            'The unchanged full validator checked all reference frames, body and outline, exact seams, feet and gameplay matrices. Twenty 1024px-tall renders are saved in its renders subfolder. Visual review remains pending; native runtime acceptance remains pending.','']
    else:lines+=['','No passing candidate was produced. Existing candidates were not changed.','']
    lines+=['','Reproduce: `py -3.12 -B -m unittest discover -s tools -p test_repair_jubilee.py`; `py -3.12 -B tools/repair_jubilee.py`. No game or network is used.','']
    (ROOT/'evidence/jubilee-repair.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
