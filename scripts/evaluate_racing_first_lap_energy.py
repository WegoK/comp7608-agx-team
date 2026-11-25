"""Test first-lap ERS allocation on frozen observations, not a new race."""
import argparse
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import types

from evaluate_racing_recovery import ROOT, Policy, CAPS, blob, equal, lateral

BASE='9c58b2da29a7d90949ece7aadfc2413301e7929f'
ACTUAL={'b0957ee4e8625a1e46cf622afc54d2362d3eb560a6bbc141f54f5b71203cacba':BASE,
        '0ce291ac5e307cb97bb8f04666adbcb286d0abcdaa18c0a20acef31a2a941ee4':'ee12c93d76f6ecefc9569776b925139adbd7c860',
        'fc2cf829681145f14b372980fa506839b2118869474599793dec8ab60b79a6cd':'cb01eb0460ac4ebb8c5cf67565c8e15b57e74b78'}


def fixture(cls):
    p=types.SimpleNamespace(energy_windows=((0.,1000.,4.),(2000.,2500.,2.),(3000.,4000.,4.)),
        o=dict(phase='race',tick=600),c=dict(lap=0,status='racing',pit_state=0,
            aid_available=False,override_available=False,front_sliding=False,rear_sliding=False,
            fuel_kg=10.,pu_temp_c=80.,ers_soc_mj=3.),mode='PASS',wet=.2,v=50.,s=300.,
        launch_tick=0,launch_distance=0.,length=5000.,energy_lap=None,energy_zone=None,
        energy_floor=.04,diagnostic={},deployment_approach_safe=lambda:True)
    p.energy_budget_active=lambda yielding,braking:cls.energy_budget_active(p,yielding,braking)
    return p


def guards(old,new):
    cases=[];p=fixture(new.Driver)
    assert not old.Driver.energy_budget_active(copy.copy(p),False,0.)
    assert new.Driver.energy_budget_active(p,False,0.)
    cases.append('first_lap_after_both_protected_boundaries')
    for label in ('time_before600','distance_before300','speed_below20','wet_boundary',
                  'not_racing','qualifying','pit','defend','yielding','guard','aid','override','front_slide','rear_slide','no_fuel','overheat','no_windows'):
        p=fixture(new.Driver);y=False;b=0.
        if label=='time_before600':p.o['tick']=599
        elif label=='distance_before300':p.s=299.99
        elif label=='speed_below20':p.v=19.99
        elif label=='wet_boundary':p.wet=.5
        elif label=='not_racing':p.c['status']='finished'
        elif label=='qualifying':p.o['phase']='qualifying'
        elif label=='pit':p.c['pit_state']=1
        elif label=='defend':p.mode='DEFEND'
        elif label=='yielding':y=True
        elif label=='guard':b=4.
        elif label=='aid':p.c['aid_available']=True
        elif label=='override':p.c['override_available']=True
        elif label=='front_slide':p.c['front_sliding']=True
        elif label=='rear_slide':p.c['rear_sliding']=True
        elif label=='no_fuel':p.c['fuel_kg']=0.
        elif label=='overheat':p.c['pu_temp_c']=115.01
        elif label=='no_windows':p.energy_windows=()
        assert not p.energy_budget_active(y,b),label;cases.append(label+'_preserved')
    p=fixture(new.Driver);p.s=1500.
    assert new.Driver.budget_deployment(p,3,False,0.)==1
    assert p.diagnostic['energy_veto']=='STORE_FOR_STRAIGHT';cases.append('outside_straight_reserves_energy')
    p=fixture(new.Driver);assert new.Driver.budget_deployment(p,3,False,0.)==3
    floor=p.energy_floor;assert abs(floor-1.816)<1e-10
    for soc in (2.9,2.5,1.9):
        p.c['ers_soc_mj']=soc;new.Driver.budget_deployment(p,3,False,0.);assert p.energy_floor==floor
    p.c['ers_soc_mj']=floor;assert new.Driver.budget_deployment(p,3,False,0.)==1
    cases+=['fixed_entry_floor_not_geometrically_recomputed','floor_stops_deployment']
    p.s=2100.;assert new.Driver.budget_deployment(p,3,False,0.)==3 and p.energy_floor<floor
    cases.append('next_straight_releases_its_share')
    p=fixture(new.Driver);p.deployment_approach_safe=lambda:False
    assert new.Driver.budget_deployment(p,3,False,0.)==1 and p.diagnostic['energy_veto']=='PREDICTED_BRAKE_QUEUE'
    cases.append('predicted_queue_veto_preserved')
    p=fixture(new.Driver);p.c['lap']=1;p.energy_lap=0;p.energy_zone=2;p.energy_floor=2.
    new.Driver.budget_deployment(p,3,False,0.);assert p.energy_lap==1 and p.energy_zone==0
    cases.append('next_lap_resets_floor')
    # Later-lap normal, safety and wet conditions retain the previous predicate.
    compared=0
    for lap in (1,2,3):
        for wet in (0.,.3,.9):
            for mode in ('LINE','DRAFT','PASS','DEFEND'):
                for braking in (0.,5.):
                    p=fixture(new.Driver);p.c['lap']=lap;p.wet=wet;p.mode=mode
                    assert old.Driver.energy_budget_active(p,False,braking)==new.Driver.energy_budget_active(p,False,braking)
                    compared+=1
    return dict(cases=cases,unchanged_later_lap_predicate_cases=compared,
        scope='Budget/state checks only. No battery regeneration, vehicle dynamics or lap time simulated.')


def replay(path):
    old,new=Policy('energy_control',BASE),Policy('energy_candidate')
    rows=raw=agree=changes=0;digest=hashlib.sha256();footer=None;phase_changes=Counter();laps={};samples=[]
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];ref=ACTUAL[own['source_hash']]
        actual=old if ref==BASE else Policy('energy_actual',ref)
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation'];c=o['self']
            a,b=old.act(o),new.act(o);lateral.legal(b)
            agree+=equal(a if actual is old else actual.act(o),r['action']);delta=not equal(a,b)
            changes+=delta;phase_changes[o['phase']]+=delta
            parkland=abs(o['track_length_m']-new.main._driver.length)<.5
            if not parkland:assert not delta
            else:
                p,q=old.main._driver,new.main._driver
                for name in CAPS:assert equal(p.diagnostic[name],q.diagnostic[name]),name
                assert (p.mode,p.target_car,p.goal)==(q.mode,q.target_car,q.goal)
                for key in ('brake','steer','aero_mode','pit_request','pit_compound','override','aid'):
                    assert equal(a[key],b[key]),key
                if delta:
                    assert o['phase']=='race' and c['lap']==0 and q.wet<.5
                    assert o['tick']-q.launch_tick>=600 and q.s-q.launch_distance>=300 and q.v>=20
                if o['phase']=='race':
                    z=laps.setdefault(str(c['lap']),Counter());z['calls']+=1;z['changed']+=delta
                    z['lower_ers_request']+=b['ers_mode']<a['ers_mode'];z['higher_ers_request']+=b['ers_mode']>a['ers_mode']
                    if delta and len(samples)<8 and (not samples or o['time']-samples[-1]['global_s']>=5):
                        samples.append(dict(global_s=o['time'],elapsed_s=(o['tick']-q.launch_tick)/60.,station_m=c['s'],
                            old_ers=a['ers_mode'],new_ers=b['ers_mode'],old_throttle=a['throttle'],new_throttle=b['throttle'],
                            observed_old_soc_mj=c['ers_soc_mj'],veto=q.diagnostic.get('energy_veto'),zone=q.diagnostic.get('energy_zone')))
            if rows%5000==0:print(path.stem,rows,flush=True)
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(own['seat']):rows} and agree==rows
    return dict(match_id=h['job_id'],actual_source_commit=ref,rows=rows,reproduced_actions=agree,
        decision_sha256=digest.hexdigest(),changed_vs_control=changes,phase_changes=phase_changes,laps_zero_based=laps,
        samples=samples,scope='Old poses, SOC, thermal state and opponents stay fixed. Request changes do not establish saved battery, lap gain or new safety outcomes.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    for name in ('preview_driver.py','design.json','weights/plan.npz','weights/mechanics.npz'):
        b=(ROOT/'strategies/racing'/name).read_bytes()
        assert (b if name.endswith('.npz') else b.replace(b'\r\n',b'\n'))==blob(BASE,name)
    def methods(s):
        cls=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='Driver')
        return {n.name:ast.get_source_segment(s,n) for n in cls.body if isinstance(n,ast.FunctionDef)}
    before=methods(blob(BASE,'agent.py').decode());after=methods((ROOT/'strategies/racing/agent.py').read_text())
    assert [n for n in before if before[n]!=after[n]]==['energy_budget_active']
    old,new=Policy('energy_guard_old',BASE),Policy('energy_guard_new')
    result=dict(schema='racing-first-lap-energy-candidate-v1',baseline_commit=BASE,
        candidate_agent_lf_sha256=hashlib.sha256((ROOT/'strategies/racing/agent.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        unchanged_other_methods_and_bundle_files=True,guards=guards(old.main,new.main),
        logs=[replay(path) for path in a.log],platform_validation='not_run',competition_activation='not_run',candidate_real_matches=0)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Guards',len(result['guards']['cases']))
    print(json.dumps([dict(match=r['match_id'],rows=r['rows'],changed=r['changed_vs_control'],laps=r['laps_zero_based']) for r in result['logs']],indent=2))
