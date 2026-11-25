"""Check a defensive buying candidate; no damage or match outcome simulated."""
import argparse
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from evaluate_fps_jump_recovery import load, checks as recovery_checks
from evaluate_fps_navigation import ROOT, reference_file, check_action
from evaluate_fps_retake import fixture, equal, synthetic_checks
from evaluate_fps_aim_hold import guards as aim_guards

BASE='c979ee08a85eb8da54f0c49f6f834a77af3f3dd5'
ACTUAL={'179919156da8b2506834a5b176e1ca6258a439a8b9d01f5c72dff0a09f0e58f2':BASE,
        '67e64a8436febd4b5d4f3d1ea9659d03c9ebfca6b3ec71d375ad3b7049080b7b':'65652c51f1ef60fb1f365b45bfff3eaa947c7f40'}
PRICES=dict(ak47=2700,m4a1=2900,armor=650,helmet=350,defusekit=400)


def observation(cash=800,side='defend',armor=0,helmet=False,primary=False,kit=False):
    o=fixture();o['phase']='buy';o['side']=side
    o['me'].update(cash=cash,armor=armor,helmet=helmet,has_kit=kit)
    if primary:o['me']['weapons'][0]=dict(id='ak47' if side=='attack' else 'm4a1',ammo=30,reserve=90,reload_until=0)
    return o


def project(module,source):
    """Apply only observed fixed purchase costs/state changes, not a full economy."""
    o=copy.deepcopy(source);p=module.Candidate();purchases=[]
    for _ in range(8):
        a=p.act(o);check_action(a)
        if not a:break
        assert set(a)=={'buy'} and a['buy'] in PRICES
        kind=a['buy'];cost=PRICES[kind]
        assert o['me']['cash']>=cost
        if kind=='helmet':assert o['me']['armor']>=100 and not o['me'].get('helmet',False)
        purchases.append(kind);o['me']['cash']-=cost
        if kind in ('ak47','m4a1'):o['me']['weapons'][0]=dict(id=kind,ammo=30,reserve=90,reload_until=0)
        elif kind=='armor':o['me']['armor']=100
        elif kind=='helmet':o['me']['helmet']=True
        elif kind=='defusekit':o['me']['has_kit']=True
        o['match_tick']+=3;o['receipts']=[dict(action='buy',ok=True,reason='ok')]
    else:raise AssertionError('Purchase loop did not settle')
    return dict(purchases=purchases,cash=o['me']['cash'],armor=o['me']['armor'],helmet=o['me'].get('helmet',False),
        has_kit=o['me']['has_kit'],primary=o['me']['weapons'][0]['id'] if o['me']['weapons'][0] else None)


def guards(old,new):
    cases=[]
    for label,o,expected in [
        ('helmet_boundary_below',observation(349,'attack',100,primary=True),{}),
        ('helmet_boundary',observation(350,'attack',100,primary=True),{'buy':'helmet'}),
        ('already_has_helmet',observation(350,'attack',100,helmet=True,primary=True),{}),
        ('partial_armor_no_discount_assumption',observation(350,'attack',99,primary=True),{}),
        ('armor_boundary_below',observation(649,'attack',0,primary=True),{}),
        ('armor_boundary',observation(650,'attack',0,primary=True),{'buy':'armor'}),
        ('ak_before_upgrades',observation(2700,'attack'),{'buy':'ak47'}),
        ('m4_before_kit',observation(2900,'defend'),{'buy':'m4a1'}),
        ('defend_pistol_armor_first',observation(800,'defend'),{'buy':'armor'}),
        ('kit_after_protection',observation(400,'defend',100,helmet=True,primary=True),{'buy':'defusekit'}),
        ('no_duplicate_kit',observation(400,'defend',100,helmet=True,primary=True,kit=True),{}),
        ('attack_never_buys_kit',observation(400,'attack',100,helmet=True,primary=True),{})]:
        p=new.Candidate();assert p.act(copy.deepcopy(o))==expected,label;cases.append(label)
    for phase in ('live','post','finished'):
        o=observation(800);o['phase']=phase
        assert new.Candidate().buy(o)=={};cases.append('direct_buy_rejects_'+phase)
    o=observation(800);o['me']['alive']=False
    assert new.Candidate().act(o)=={};cases.append('dead_no_purchase')
    for result in (False,True):
        p=new.Candidate();o=observation(350,'attack',100,primary=True)
        assert p.act(o)=={'buy':'helmet'}
        o['match_tick']=3;o['receipts']=[dict(action='buy',ok=result,reason='test_receipt')]
        if result:o['me']['helmet']=True;o['me']['cash']=0
        assert p.act(o)=={}
        if not result:
            o['receipts']=[];o['match_tick']=6;assert p.act(o)=={}
            o['round_index']+=1;assert p.act(o)=={'buy':'helmet'}
        cases.append('success_stops_duplicate' if result else 'failed_item_skipped_until_next_round')
    p=new.Candidate();o=observation(350,'attack',100,primary=True);p.act(o)
    o['match_tick']=3;o['receipts']=[dict(action='reload',ok=False)]
    assert p.act(o)=={'buy':'helmet'};cases.append('nonbuy_receipt_not_misattributed')
    scenarios={}
    for label,o in [('defend800',observation(800)),('defend1400',observation(1400,primary=True)),
                    ('defend4300',observation(4300)),('attack3700',observation(3700,side='attack'))]:
        scenarios[label]=dict(baseline=project(old,o),candidate=project(new,o))
    assert scenarios['defend800']['candidate']['armor']==100 and scenarios['defend800']['candidate']['has_kit'] is False
    assert scenarios['defend1400']['candidate']['purchases']==['armor','helmet','defusekit']
    assert scenarios['defend4300']['candidate']['purchases']==['m4a1','armor','helmet','defusekit']
    assert scenarios['attack3700']['candidate']['purchases']==['ak47','armor','helmet']
    sweep=0
    for side in ('attack','defend'):
        for cash in (0,349,350,399,400,649,650,799,800,999,1000,1399,1400,2699,2700,2899,2900,3700,4300,7000):
            for armor in (0,49,50,99,100):
                for helmet in (False,True):
                    for primary in (False,True):
                        out=project(new,observation(cash,side,armor,helmet,primary))
                        assert out['cash']>=0 and len(out['purchases'])==len(set(out['purchases']))
                        sweep+=1
    return dict(cases=cases,isolated_purchase_scenarios=scenarios,budget_sweep_cases=sweep,
        scope='Purchase transitions use recorded costs and assumed successful receipts; not official admission or combat simulation.')


def replay(path):
    old,_=load('buy_control',BASE);new,_=load('buy_candidate');p,q=old.Candidate(),new.Candidate()
    rows=raw=agree=changes=live=0;digest=hashlib.sha256();footer=None;changes_by_phase=Counter();starts=[];last_round=None
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];ref=ACTUAL[own['source_hash']]
        actual=p if ref==BASE else load('buy_actual',ref)[0].Candidate()
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation']
            a,b=p.act(o),q.act(o);check_action(b);agree+=equal(a if actual is p else actual.act(o),r['action'])
            delta=not equal(a,b);changes+=delta;changes_by_phase[o['phase']]+=delta
            if o['phase']!='buy':assert not delta;live+=o['phase']=='live'
            if delta:assert o['phase']=='buy' and o['me']['alive']
            if o['phase']=='buy' and o['round_index']!=last_round:
                starts.append(dict(round_1based=o['round_index']+1,baseline=project(old,o),candidate=project(new,o)))
                last_round=o['round_index']
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert agree==rows and footer['seat_calls']=={str(own['seat']):rows}
    return dict(match_id=h['job_id'],actual_source_commit=ref,rows=rows,reproduced_actions=agree,decision_sha256=digest.hexdigest(),
        changed_vs_v4=changes,changes_by_phase=changes_by_phase,unchanged_live_callbacks=live,
        isolated_round_start_purchase_projections=starts,
        note='Historical money/inventory/receipts remain old; repeated buy requests here are not real purchases. Round-start projections are isolated successful-cost models, not chained economy, official validation or new match outcomes.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    old,on=load('buy_guard_old',BASE);new,nn=load('buy_guard_new')
    for n in ('starter_player.py','waypoint_nav.py'):
        assert (ROOT/'strategies/fps'/n).read_bytes().replace(b'\r\n',b'\n')==reference_file(BASE,'strategies/fps/'+n)
    def methods(s):
        node=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='Candidate')
        return {n.name:ast.get_source_segment(s,n) for n in node.body if isinstance(n,ast.FunctionDef)}
    before=methods(reference_file(BASE,'strategies/fps/agent.py').decode());after=methods((ROOT/'strategies/fps/agent.py').read_text())
    assert all(before[n]==after[n] for n in before) and set(after)-set(before)=={'buy'}
    v2,_=load('buy_aim_reference','e27acc998e08d5dc3f59bac6e0fa5e4a99329058')
    v3,_=load('buy_recovery_reference','65652c51f1ef60fb1f365b45bfff3eaa947c7f40')
    result=dict(schema='fps-protection-buy-candidate-v1',baseline_commit=BASE,
        candidate_agent_lf_sha256=hashlib.sha256((ROOT/'strategies/fps/agent.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        unchanged_existing_candidate_methods_starter_and_navigation=True,buy_checks=guards(old,new),
        aim_checks=aim_guards(v2,new),retake_checks=synthetic_checks(old,new,combat_hold_ticks=18),
        recovery_checks=recovery_checks(v3,new,nn),logs=[replay(path) for path in a.log],
        platform_validation='not_run',competition_activation='not_run',candidate_real_matches=0)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Purchase guards',len(result['buy_checks']['cases']),'budget cases',result['buy_checks']['budget_sweep_cases'])
    for r in result['logs']:
        print(r['match_id'],r['rows'],'changed',r['changed_vs_v4'],'phases',r['changes_by_phase'],
            'projected_helmet_rounds',sum(x['candidate']['helmet'] for x in r['isolated_round_start_purchase_projections']))
