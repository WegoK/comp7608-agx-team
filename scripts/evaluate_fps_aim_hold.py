"""Check bounded last-seen aiming; recorded positions do not follow new actions."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import types

from evaluate_fps_navigation import ROOT, load_agent, reference_file, check_action
from evaluate_fps_retake import equal, fixture, synthetic_checks

BASE = 'e27acc998e08d5dc3f59bac6e0fa5e4a99329058'
ACTUAL = {'06bcdcc4ae3947ba07f7085d7d222b10c7b01f6931519b62ce7f5b4d85608e0f': BASE,
          '2fb651073041be55b178e189c43631c96fc1ac0aae794ca0107a4959a60d6ba1': '4f5747cbde4296a235b042e60b7486a224f3dfd4'}


def load(label, ref=None):
    nav=types.ModuleType(label+'_nav')
    exec(compile(reference_file(BASE,'strategies/fps/waypoint_nav.py'),'<nav>','exec'),nav.__dict__)
    return load_agent(label,nav,ref)


def guards(old,new):
    labels=[]
    def obs(tick=0,visible=True):
        o=fixture();o['bomb']={'state':'held'};o['match_tick']=tick
        o['visible']=[dict(x=10.,y=10.,z=0.,crouch=False,parts=['chest'])] if visible else []
        return o
    def act(p,o):
        a=p.act(copy.deepcopy(o));check_action(a);return a
    p=new.Candidate();a=act(p,obs());assert a==act(old.Candidate(),obs())
    for t in range(3,19,3):
        a=act(p,obs(t,False));assert a['crouch'] and a['fire'] is False
        assert not a.get('move_forward') and not a.get('move_right')
        assert p.aim_seen_tick==0 and p.scan_start is None and p.motion_task is None
    assert p.aim_target is not None
    a=act(p,obs(21,False));assert p.aim_target is None and 'fire' not in a
    labels.extend(['visible_combat_preserved','bounded_18_tick_hold_without_fire_or_motion','hidden_frames_do_not_refresh_memory','expiry_returns_to_objective'])
    for label in ('round','map','side','buy','dead','blind','airborne','backwards_tick','large_gap'):
        p=new.Candidate();act(p,obs());o=obs(3,False)
        if label=='round':o['round_index']+=1
        elif label=='map':o['map_id']=o['map']['id']='borgo-r3-v54'
        elif label=='side':o['side']='attack'
        elif label=='buy':o['phase']='buy'
        elif label=='dead':o['me']['alive']=False
        elif label=='blind':o['me']['blind_ticks']=3
        elif label=='airborne':o['me']['grounded']=False
        elif label=='backwards_tick':o['match_tick']=-3
        elif label=='large_gap':o['match_tick']=60
        act(p,o);assert p.aim_target is None,label
        labels.append(label+'_clears_memory')
    for use in ('plant','defuse'):
        p=new.Candidate();o=obs();o['side']='attack' if use=='plant' else 'defend';act(p,o)
        o['match_tick']=3;o['visible']=[]
        if use=='plant':o['me']['carrying_bomb']=True
        else:o['bomb']=dict(state='planted',position=[0,0,0],fuse_end_tick=2400)
        a=act(p,o);assert a['use']==use and p.aim_target is None
        labels.append(use+'_preempts_memory')
    p=new.Candidate();act(p,obs());o=obs(3);o['visible'][0]['x']=-10
    a=act(p,o);assert p.aim_target['x']==-10 and p.aim_seen_tick==3
    q=old.Candidate();assert a==act(q,o)
    labels.append('new_visible_target_replaces_memory')
    # A limited aim/pose model, NOT visibility, bullet, collision or match simulation.
    # The target alternates visible/hidden by construction, at observed 3-tick cadence.
    def aiming(module):
        p=module.Candidate();o=obs();o['map_id']=o['map']['id']='dust2-cs2-v54'
        o['me'].update(x=151.8827,y=145.0278,z=20.1524,yaw=2.10628,pitch=.0272)
        o['round_index']=21;o['seat']=7
        target=dict(x=127.14,y=129.81,z=19.95,crouch=True,parts=['chest','head'])
        fire=0;hidden_fire=0;poses=[]
        for t in range(0,120,3):
            o['match_tick']=t;o['visible']=[target] if t%6==0 else []
            a=act(p,o);fire+=bool(a.get('fire')) and bool(o['visible'])
            hidden_fire+=bool(a.get('fire')) and not o['visible']
            o['me']['yaw']+=.15*a.get('yaw_rate',0);o['me']['pitch']+=.1*a.get('pitch_rate',0)
            o['me']['crouch']=a.get('crouch',False);poses.append(o['me']['crouch'])
        return dict(visible_fire_requests=fire,hidden_fire_requests=hidden_fire,pose_switches=sum(a!=b for a,b in zip(poses,poses[1:])))
    a,b=aiming(old),aiming(new)
    assert a['visible_fire_requests']==0 and b['visible_fire_requests']>0
    assert b['hidden_fire_requests']==0 and b['pose_switches']==0
    return dict(cases=labels,alternating_visibility_aim_model=dict(baseline=a,candidate=b,
        limitation='Only angle/pose integration with prescribed alternating visibility; no real hits or wins simulated.'))


def replay(path):
    old=load('logged_old',BASE).Candidate();new=load('logged_new').Candidate()
    rounds={};rows=raw=agree=faults=changes=held=0;digest=hashlib.sha256();footer=None
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];ref=ACTUAL[own['source_hash']]
        actual=old if ref==BASE else load('logged_actual',ref).Candidate()
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation']
            a,b=old.act(o),new.act(o);check_action(b)
            agree+=equal(a if actual is old else actual.act(o),r['action']);faults+=bool(r['fault'])
            q=rounds.setdefault(o['round_index'],Counter());changed=not equal(a,b)
            q['rows']+=1;q['changed']+=changed;changes+=changed
            hidden_hold=not o['visible'] and new.aim_target is not None and 'fire' in b
            q['hidden_hold']+=hidden_hold;held+=hidden_hold
            if hidden_hold:
                assert b['fire'] is False and b['crouch'] and not b.get('move_forward') and not b.get('move_right')
                assert 0<=o['match_tick']-new.aim_seen_tick<=18
            if b.get('fire'):assert o['visible'] and not o['me']['blind_ticks']
            if o['phase']!='live' or not o['me']['alive'] or a.get('use'):
                assert equal(a,b),'lifecycle or objective priority changed'
            if o['phase']=='live' and o['me']['alive'] and o['visible'] and not o['me']['blind_ticks']:
                assert equal(a,b),'visible combat changed on identical observations'
        assert footer and footer['complete']
        assert (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
        assert agree==rows and footer['seat_calls']=={str(own['seat']):rows}
    return dict(match_id=h['job_id'],actual_source_commit=ref,rows=rows,reproduced_requested_actions=agree,
        raw_bytes=raw,decision_sha256=digest.hexdigest(),faults=faults,changed_actions_vs_v2=changes,
        hidden_hold_callbacks=held,rounds=rounds,
        limitation='Recorded own positions and visibility remain old; no candidate hits, kill count, trajectories or result inferred.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    for name in ('starter_player.py','waypoint_nav.py'):
        assert (ROOT/'strategies/fps'/name).read_bytes().replace(b'\r\n',b'\n')==reference_file(BASE,'strategies/fps/'+name)
    old,new=load('guard_old',BASE),load('guard_new')
    result=dict(schema='fps-aim-hold-candidate-v1',baseline_commit=BASE,
        candidate_agent_lf_sha256=hashlib.sha256((ROOT/'strategies/fps/agent.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        unchanged_starter_and_navigation=True,guards=guards(old,new),
        existing_retake_guards=synthetic_checks(old,new,combat_hold_ticks=new.AIM_HOLD_TICKS),logs=[replay(path) for path in a.log],
        platform_validation='not_run',competition_activation='not_run',candidate_real_matches=0)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['guards'],indent=2))
    print(json.dumps([{k:r[k] for k in ('match_id','rows','changed_actions_vs_v2','hidden_hold_callbacks')} for r in result['logs']],indent=2))
