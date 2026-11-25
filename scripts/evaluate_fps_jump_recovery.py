"""Test Mirage horizontal stall recovery; no new trajectory or wins simulated."""
import argparse
import ast
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import types

from evaluate_fps_navigation import ROOT, load_agent, load_module, reference_file, check_action, regression_checks
from evaluate_fps_retake import equal, synthetic_checks
from evaluate_fps_aim_hold import guards as aim_guards

BASE='65652c51f1ef60fb1f365b45bfff3eaa947c7f40'
ACTUAL={'67e64a8436febd4b5d4f3d1ea9659d03c9ebfca6b3ec71d375ad3b7049080b7b':BASE,
        '06bcdcc4ae3947ba07f7085d7d222b10c7b01f6931519b62ce7f5b4d85608e0f':'e27acc998e08d5dc3f59bac6e0fa5e4a99329058'}


def load(label,ref=None):
    if ref:
        nav=types.ModuleType(label+'_nav')
        exec(compile(reference_file(ref,'strategies/fps/waypoint_nav.py'),'<frozen-nav>','exec'),nav.__dict__)
    else:nav=load_module(label+'_nav',ROOT/'strategies/fps/waypoint_nav.py')
    return load_agent(label,nav,ref),nav


def fixture(tick=0):
    row=dict(id='mirage-cs2-v54',sites=[dict(id='A',center=[10.,0.,1.1],radius=4)],
        nav=dict(nodes=[dict(id=0,position=[0.,0.,0.]),dict(id=1,position=[1.2,0.,1.1]),dict(id=2,position=[10.,0.,1.1])],
                 edges=[{'from':0,'to':1,'kind':'walk'},{'from':1,'to':2,'kind':'walk'}]))
    return dict(map_id=row['id'],map=row,round_index=0,seat=0,side='defend',match_tick=tick,
        round_tick=360+tick,phase='live',teammates=[],visible=[],bomb=dict(state='held'),
        me=dict(x=0.,y=0.,z=0.,yaw=0.,pitch=0.,alive=True,grounded=True,crouch=False,
            blind_ticks=0,cash=800,armor=0,has_kit=False,carrying_bomb=False,
            weapons=[None,dict(ammo=12,reserve=24,reload_until=0),None]))


def checks(old,new,nav):
    # Input telemetry is prescribed: blocked XY, repeated vertical bob. No new physics.
    outputs=[]
    for module in (old,new):
        p=module.Candidate();jumps=0;air_detours=[];original=p._detour
        def detour(pos,goal,target,tick):
            air_detours.append((tick,current['me']['grounded']))
            return original(pos,goal,target,tick)
        p._detour=detour
        for tick in range(0,600,3):
            current=fixture(tick)
            if tick>=90:
                phase=(tick-90)%96
                current['me']['grounded']=phase==0 or phase>=90
                current['me']['z']=max(0.,1.3*math.sin(math.pi*phase/90)) if phase<90 else 0.
            a=p.act(current);check_action(a);jumps+=bool(a.get('jump'))
        outputs.append(dict(jumps=jumps,escapes=p.escape_count,airborne_detours=sum(not grounded for _,grounded in air_detours)))
    assert outputs[1]['jumps']<=1,outputs
    assert outputs[1]['escapes']>0 and outputs[1]['airborne_detours']==0,outputs
    # Arrival remains 3D: same XY on a lower floor is not a completed goal.
    p=new.Candidate();o=fixture();o['map']['sites'][0]['center']=[0.,0.,1.1]
    o['map']['nav']=dict(nodes=[dict(id=0,position=[0.,0.,0.]),dict(id=1,position=[0.,0.,1.1])],
        edges=[{'from':0,'to':1,'kind':'walk'}])
    a=p.act(o);assert a.get('move_forward') or a.get('move_right')
    o['match_tick']=3;o['me']['z']=1.1;a=p.act(o)
    assert not a.get('move_forward') and not a.get('move_right')
    labels=['vertical_bob_does_not_allow_repeated_jump','horizontal_stall_enters_escape',
            'detour_waits_for_landing','arrival_keeps_3d_height_check']
    # A normal supplied trajectory reaches the waypoint; it must not be classified as stalled.
    p=new.Candidate()
    for tick in range(0,180,3):
        o=fixture(tick);o['me']['x']=tick/15;o['me']['z']=min(1.1,tick/60*1.1)
        check_action(p.act(o));assert p.escape_count==0
    labels.append('normal_horizontal_and_height_progress_no_escape')
    # The jump budget is not reset by progress-window or callback resets.
    p=new.Candidate();o=fixture()
    for tick in range(0,93,3):o['match_tick']=tick;p.act(o)
    assert p._jump_cooldowns
    before=dict(p._jump_cooldowns);p._reset_window(100,[1.2,0,1.1],[0,0,.8])
    assert p._jump_cooldowns==before
    o['match_tick']=120;p.act(o);assert p._jump_cooldowns==before
    o['round_index']=1;o['match_tick']=123;p.act(o);assert not p._jump_cooldowns
    labels+=['progress_reset_keeps_jump_cooldown','callback_gap_keeps_jump_cooldown','round_resets_jump_budget']
    for mapid in ('dust2-cs2-v54','borgo-r3-v54','molo-r3-v54','nevaio-r3-v54'):
        players=[m.Candidate() for m in (old,new)]
        for tick in range(0,300,3):
            o=fixture(tick);o['map_id']=o['map']['id']=mapid
            a,b=[p.act(copy.deepcopy(o)) for p in players];assert equal(a,b)
        labels.append(mapid+'_unchanged_blocked_fixture')
    return dict(cases=labels,blocked_prescribed_telemetry=dict(baseline=outputs[0],candidate=outputs[1],
        limitation='Identical prescribed XY/Z inputs; fewer jump requests and escapes do not prove a physically traversable path.'))


def replay(path):
    old,_=load('jump_logged_old',BASE);new,_=load('jump_logged_new')
    p,q=old.Candidate(),new.Candidate();rows=raw=agree=changes=0;digest=hashlib.sha256();footer=None;rounds={}
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];ref=ACTUAL[own['source_hash']]
        actual=p if ref==BASE else load('jump_actual',ref)[0].Candidate()
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation']
            a,b=p.act(o),q.act(o);check_action(b);agree+=equal(a if actual is p else actual.act(o),r['action'])
            changed=not equal(a,b);changes+=changed
            c=rounds.setdefault(o['round_index'],Counter());c['changed']+=changed
            c['baseline_jump']+=bool(a.get('jump'));c['candidate_jump']+=bool(b.get('jump'))
            if o['phase']=='live' and o['me']['alive']:
                c['candidate_escape_callbacks']+=q.escape is not None
            if o['map_id']!='mirage-cs2-v54':assert not changed
            if o['phase']!='live' or not o['me']['alive'] or a.get('use') or (o['visible'] and not o['me']['blind_ticks']):
                assert not changed,'Non-navigation priority changed'
            if not o['visible'] and getattr(q,'aim_target',None) is not None and 'fire' in b:
                assert equal(a,b) and b['fire'] is False
        assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
        assert agree==rows and footer['seat_calls']=={str(own['seat']):rows}
    return dict(match_id=h['job_id'],actual_source_commit=ref,rows=rows,reproduced_actions=agree,
        decision_sha256=digest.hexdigest(),changed_vs_v3=changes,rounds=rounds,
        limitation='Historical own positions and visibility stay fixed; no new arrival, defuse, hit or win inferred.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',action='append',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    old,on=load('jump_old',BASE);new,nn=load('jump_new')
    assert (ROOT/'strategies/fps/starter_player.py').read_bytes().replace(b'\r\n',b'\n')==reference_file(BASE,'strategies/fps/starter_player.py')
    # Keep the exact aim-state and bomb-search implementation from v3.
    def methods(source,cls):
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name==cls)
        return {n.name:ast.get_source_segment(source,n) for n in node.body if isinstance(n,ast.FunctionDef)}
    before=methods(reference_file(BASE,'strategies/fps/agent.py').decode(),'Candidate')
    after=methods((ROOT/'strategies/fps/agent.py').read_text(),'Candidate')
    assert [n for n in before if before[n]!=after[n]]==['navigate']
    aim_old,_=load('jump_aim_v2','e27acc998e08d5dc3f59bac6e0fa5e4a99329058')
    result=dict(schema='fps-mirage-horizontal-recovery-v1',baseline_commit=BASE,
        candidate_lf_sha256={n:hashlib.sha256((ROOT/'strategies/fps'/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in ('agent.py','waypoint_nav.py')},
        recovery_checks=checks(old,new,nn),aim_guards=aim_guards(aim_old,new),
        retake_guards=synthetic_checks(old,new,combat_hold_ticks=18),
        unchanged_detour_graph_regression=regression_checks(on,nn),logs=[replay(path) for path in a.log],
        platform_validation='not_run',competition_activation='not_run',candidate_real_matches=0)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['recovery_checks'],indent=2));print(json.dumps([dict(match=r['match_id'],rows=r['rows'],changed=r['changed_vs_v3'],round14=r['rounds'].get(13)) for r in result['logs']],indent=2))
