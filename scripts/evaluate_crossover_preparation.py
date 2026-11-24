"""Check one bounded Crossover lane-preparation candidate, not race outcomes."""
import argparse
import ast
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from evaluate_racing_recovery import ROOT, Policy, blob, equal, lateral, module

BASE='cb01eb0460ac4ebb8c5cf67565c8e15b57e74b78'
ACTUAL={'fc2cf829681145f14b372980fa506839b2118869474599793dec8ab60b79a6cd':BASE,
        '0ce291ac5e307cb97bb8f04666adbcb286d0abcdaa18c0a20acef31a2a941ee4':'ee12c93d76f6ecefc9569776b925139adbd7c860',
        'bfaeb741839032f0bffc0671592ac48860374ed44213f6a89aff3cd7d5a2e4a9':'d6268a7f50adcfec63b79ca706c627401ae2af2d'}


def guards(old,new):
    def fixture():
        o=lateral.observation(time=2.);o['rivals']=[lateral.rival(1,40.,0.,15.)]
        return o
    def driver(cls):
        d=cls.Driver();d.retry_at=0.;d.waiting_for=1;d.wait_since=0.
        return d
    o=fixture();a,b=driver(old),driver(new)
    assert a.passing_line(o,60.)==0 and abs(b.passing_line(o,60.))==3.8
    assert b.pass_seat==1 and b.pass_early_until==8.
    cases=['far_leader_proposal_when_baseline_cannot_enter']
    for label in ('far_boundary','not_closing','no_traffic_loss','sliding','heading','curve','narrow','occupied','cut_in','waiting','cooldown','wrong_track'):
        o=fixture();d=driver(new);target=60.
        if label=='far_boundary':o['rivals'][0]['gap_m']=60.
        elif label=='not_closing':o['self']['speed_mps']=16.
        elif label=='no_traffic_loss':target=20.
        elif label=='sliding':o['self']['front_sliding']=True
        elif label=='heading':o['self']['heading_error']=.11
        elif label=='curve':o['preview']['rows'][0][1]=.005
        elif label=='narrow':
            for r in o['preview']['rows']:r[3]=r[4]=5.
        elif label=='occupied':o['rivals'] += [lateral.rival(2,0,3.8),lateral.rival(3,0,-3.8)]
        elif label=='cut_in':
            o['rivals'] += [lateral.rival(2,0,8.,30.,-3.),lateral.rival(3,0,-8.,30.,3.)]
        elif label=='waiting':d.wait_since=1.
        elif label=='cooldown':d.retry_at=3.
        elif label=='wrong_track':o['track_length_m']=6999.367
        assert d.passing_line(o,target)==0. and d.pass_seat is None,label
        cases.append(label+'_veto')
    o=fixture();d=driver(new);d.passing_line(o,60.)
    o['time']=3.;assert abs(d.passing_line(o,60.))==3.8  # No premature 50m-range exit.
    o['rivals'][0]['gap_m']=55.;assert abs(d.passing_line(o,60.))==3.8
    o['time']=8.;assert d.passing_line(o,60.)==0. and d.pass_seat is None and d.retry_at==10.
    cases.append('six_second_expiry_and_cooldown')
    o=fixture();d=driver(new);d.passing_line(o,60.);o['time']=3.;o['rivals'][0]['gap_m']=20.
    assert abs(d.passing_line(o,60.))==3.8 and d.pass_early_until==0.
    cases.append('close_gap_transfers_to_original_pass')
    o=fixture();d=driver(new);d.passing_line(o,60.);o['phase']='qualifying';d.act(o)
    assert d.pass_seat is None and d.pass_early_until==0.
    cases.append('phase_reset')
    return cases


def replay(path):
    old,new=Policy('cross_control',BASE),Policy('cross_new')
    rows=raw=agree=changed=0;digest=hashlib.sha256();footer=None;byphase=Counter();starts=[];last=None
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];ref=ACTUAL[own['source_hash']]
        actual=old if ref==BASE else Policy('cross_actual',ref)
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation']
            a,b=old.act(o),new.act(o);lateral.legal(b)
            agree+=equal(a if actual is old else actual.act(o),r['action'])
            delta=not equal(a,b);changed+=delta;byphase[o['phase']]+=delta
            if delta:assert o['phase']=='race' and abs(o['track_length_m']-5803.167)<.5
            if abs(o['track_length_m']-5803.167)<.5:
                d=new.preview._driver
                if d.pass_seat is not None and d.pass_seat!=last:
                    front=next(r for r in o['rivals'] if r['seat']==d.pass_seat)
                    starts.append(dict(global_s=o['time'],lap=o['self']['lap']+1,seat=d.pass_seat,
                        gap_m=front['gap_m'],early=bool(d.pass_early_until)))
                last=d.pass_seat
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert agree==rows and footer['seat_calls']=={str(own['seat']):rows}
    return dict(match_id=h['job_id'],actual_source_commit=ref,rows=rows,action_reproduction=agree,
        decision_sha256=digest.hexdigest(),changed_vs_control=changed,changes_by_phase=byphase,
        recorded_pose_proposals=starts,note='Fixed recorded own poses and rival responses; proposals do not establish real overtakes or lap-time gains.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',action='append',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    before=blob(BASE,'preview_driver.py').decode();after=(ROOT/'strategies/racing/preview_driver.py').read_text()
    def segments(s):
        return {n.name:ast.get_source_segment(s,n) for n in ast.parse(s).body if isinstance(n,ast.FunctionDef)}
    assert segments(before)==segments(after),'Top-level speed/traffic/pit functions changed'
    for name in ('agent.py','design.json','weights/plan.npz','weights/mechanics.npz'):
        b=(ROOT/'strategies/racing'/name).read_bytes()
        assert (b if name.endswith('.npz') else b.replace(b'\r\n',b'\n'))==blob(BASE,name)
    old=module('cross_old_preview',before);new=module('cross_new_preview',after)
    checks=dict(guards=guards(old,new),estimator=lateral.check_estimator(new),
        regression=lateral.check_regression(old,new))
    # Preserve the existing lateral cut-in safety regressions against their original baseline.
    historical=module('cross_historical',lateral.git_bytes('strategies/racing/preview_driver.py'))
    checks.update(corridors=lateral.check_corridors(historical,new),passing=lateral.check_passing(historical,new))
    result=dict(schema='crossover-preparation-candidate-v1',baseline_commit=BASE,
        unchanged_speed_traffic_pit_functions_and_other_bundle_files=True,
        candidate_preview_lf_sha256=hashlib.sha256(after.replace('\r\n','\n').encode()).hexdigest(),
        checks=checks,logs=[replay(path) for path in a.log],platform_validation='not_run',competition_activation='not_run',candidate_real_matches=0)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(guards=len(checks['guards']),logs=[dict(match=r['match_id'],changed=r['changed_vs_control'],proposals=r['recorded_pose_proposals']) for r in result['logs']]),indent=2))
