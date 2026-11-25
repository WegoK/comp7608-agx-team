"""Audit a complete own-seat FPS export; replay events are diagnosis only.

No alternative movement, visibility, gunfire or match result is simulated.
"""
import argparse
from collections import Counter, deque
import hashlib
import json
import math
from pathlib import Path
import types

from evaluate_fps_navigation import load_agent, reference_file, check_action
from evaluate_fps_retake import equal

POLICIES = {
    '06bcdcc4ae3947ba07f7085d7d222b10c7b01f6931519b62ce7f5b4d85608e0f': 'e27acc998e08d5dc3f59bac6e0fa5e4a99329058',
    '67e64a8436febd4b5d4f3d1ea9659d03c9ebfca6b3ec71d375ad3b7049080b7b': '65652c51f1ef60fb1f365b45bfff3eaa947c7f40',
    '179919156da8b2506834a5b176e1ca6258a439a8b9d01f5c72dff0a09f0e58f2': 'c979ee08a85eb8da54f0c49f6f834a77af3f3dd5',
}


def review(path, events):
    with path.open('rb') as f:metadata=json.loads(next(f))
    bundle=metadata['participants'][0]['source_hash'];ref=POLICIES[bundle]
    nav = types.ModuleType('review_frozen_nav')
    exec(compile(reference_file(ref,'strategies/fps/waypoint_nav.py'),'<frozen-nav>','exec'),nav.__dict__)
    policy = load_agent('review_frozen_fps',nav,ref).Candidate()
    digest=hashlib.sha256(); rows=raw=agreement=faults=0
    rounds={}; footer=outcome=None; previous=None; history=deque(); sites=[]
    with path.open('rb') as f:
        header=json.loads(next(f)); own=header['participants'][0]; seat=own['seat']
        assert header['game_id']=='fps' and len(header['participants'])==1
        assert header['observation_semantics']=='recorded_policy_input'
        assert header['decision_encoding']=='original-jsonl-bytes' and own['source_hash']==bundle
        for line in f:
            row=json.loads(line)
            if 'observation' not in row:
                if row.get('type')=='export_complete': footer=row
                if row.get('type')=='outcome': outcome=row
                continue
            rows+=1; raw+=len(line); digest.update(line)
            o=row['observation']; m=o['me']; a=row['action']; tick=o['match_tick']
            if 'map' in o:sites=o['map']['sites']
            assert row['seat']==o['seat']==seat
            reconstructed=policy.act(o); check_action(reconstructed)
            agreement+=equal(reconstructed,a); faults+=bool(row['fault'])
            r=rounds.setdefault(o['round_index'],dict(side=o['side'],counts=Counter(),
                visible_targets=Counter(),weapon_combat=Counter(),uses=Counter(),
                bomb_states=Counter(),ammo_spent=0,damage_taken=0,
                position_start=None,position_last=None,slow_navigation_windows=[]))
            c=r['counts']; c[o['phase']+'_calls']+=1
            if o['phase']=='live' and m['alive']:
                c['alive_live_calls']+=1
                pos=[m[k] for k in ('x','y','z')]
                if r['position_start'] is None:r['position_start']=pos
                r['position_last']=pos; r['bomb_states'][o['bomb']['state']]+=1
                if a.get('use'):r['uses'][a['use']]+=1
                combat=bool(o['visible'] and not m['blind_ticks'])
                held=(not o['visible'] and getattr(policy,'aim_target',None) is not None and 'fire' in a)
                c['hidden_aim_hold_calls']+=held
                if held:
                    age=tick-policy.aim_seen_tick
                    assert 0<=age<=18 and a['fire'] is False and a['crouch'] is True
                    assert not a.get('move_forward') and not a.get('move_right')
                    r['max_aim_hold_age_ticks']=max(r.get('max_aim_hold_age_ticks',0),age)
                c['blind_calls']+=bool(m['blind_ticks'])
                c['fire_requested']+=bool(a.get('fire'))
                c['reload_requested']+=bool(a.get('reload'))
                c['plant_carrier_calls']+=bool(m['carrying_bomb'])
                navigation=(not combat and not a.get('use') and
                    abs(a.get('move_forward',0))+abs(a.get('move_right',0))>.1)
                c['navigation_calls']+=navigation
                c['retake_unknown_calls']+=o['side']=='defend' and o['bomb']['state']=='planted' and not o['bomb'].get('position')
                c['retake_known_calls']+=o['side']=='defend' and o['bomb']['state']=='planted' and bool(o['bomb'].get('position'))
                if combat:
                    c['combat_calls']+=1
                    slot=a.get('weapon_slot',m['active_slot']); gun=m['weapons'][slot]
                    c['combat_empty_calls']+=bool(gun and gun['ammo']==0)
                    c['combat_reload_calls']+=bool(gun and gun['reload_until'])
                    c['combat_fire_calls']+=bool(a.get('fire'))
                    c['combat_moving_calls']+=math.hypot(m['vx'],m['vy'])>.5
                    c['combat_airborne_calls']+=not m['grounded']
                    r['weapon_combat'][gun['id'] if gun else 'none']+=1
                    for enemy in o['visible']:r['visible_targets'][str(enemy.get('seat','unknown'))]+=1
                    r.setdefault('first_combat_tick',tick)
                if 'first_combat_tick' in r and len(r.setdefault('first_combat_frame_trace',[]))<12:
                    r['first_combat_frame_trace'].append(dict(tick=tick,visible=bool(o['visible']),
                        yaw=m['yaw'],pitch=m['pitch'],action=a))
                if previous and previous['round_index']==o['round_index'] and previous['phase']=='live' and previous['me']['alive']:
                    prior_combat=bool(previous['visible'] and not previous['me']['blind_ticks'])
                    c['combat_after_combat']+=combat and prior_combat
                    c['combat_after_not_visible']+=combat and not prior_combat
                    c['visibility_lost_after_combat']+=not combat and prior_combat
                    c['pose_switches_live']+=bool(m['crouch'])!=bool(previous['me']['crouch'])
                if o['side']=='defend' and o['bomb']['state']=='planted':
                    ret=r.setdefault('retake',dict(first_tick=tick,last_tick=tick,min_site_distance={},search_switches=[],scan_calls=0))
                    ret['last_tick']=tick
                    for site in sites:
                        dist=math.dist(pos,site['center'])
                        ret['min_site_distance'][site['id']]=min(ret['min_site_distance'].get(site['id'],999),dist)
                    target=policy.retake_site
                    if target is not None and (not ret['search_switches'] or ret['search_switches'][-1]['site']!=sites[target]['id']):
                        ret['search_switches'].append(dict(tick=tick,site=sites[target]['id']))
                    ret['scan_calls']+=policy.scan_tick==tick
                if previous and previous['round_index']==o['round_index'] and previous['me']['alive']:
                    pm=previous['me'];r['damage_taken']+=max(0,pm['hp']-m['hp'])
                    for g,h in zip(pm['weapons'],m['weapons']):
                        if g and h and g.get('item_id')==h.get('item_id'):
                            r['ammo_spent']+=max(0,g['ammo']-h['ammo'])
                if not navigation:history.clear()
                history.append((tick,pos))
                while history and tick-history[0][0]>600:history.popleft()
                if navigation and history and tick-history[0][0]>=597 and math.dist(pos,history[0][1])<1:
                    intervals=r['slow_navigation_windows']
                    if not intervals or tick-intervals[-1]['end_tick']>3:
                        intervals.append(dict(start_tick=history[0][0],end_tick=tick,position=pos))
                    else:intervals[-1]['end_tick']=tick
            else:history.clear()
            if previous and previous['round_index']==o['round_index'] and previous['me']['alive'] and not m['alive']:
                r['death_tick']=tick;r['death_position']=[m[k] for k in ('x','y','z')]
            previous=o
            if rows%10000==0:print('replayed',rows,flush=True)
    assert footer and footer['complete'] and outcome
    assert (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(seat):rows} and agreement==rows
    result=dict(schema='fps-own-export-review-v1',match_id=header['job_id'],map_id=previous['map_id'],
        rules_version=header['rules_version'],source_commit=ref,zip_sha256=bundle,
        frozen_version_id=own['version_id'],own_seat_zero_based=seat,own_team_zero_based=own['team'],
        integrity=dict(rows=rows,raw_bytes=raw,sha256=digest.hexdigest(),faults=faults,
            reproduced_requested_actions=agreement),outcome=outcome,rounds=rounds,
        note='Real logged match only. Full action reproduction uses legal own observations; public replay is read afterwards. No candidate or counterfactual result.')
    counts=Counter(); samples={}; kills=deaths=0; damage=0.; hit_targets=Counter()
    with events.open(encoding='utf-8') as f:
        for line in f:
            e=json.loads(line); typ=e['type'];counts[typ]+=1
            if typ not in samples and typ in ('hit','kill','round_end','match_end','shot','fire','plant_complete_detail','defuse_complete'):
                samples[typ]=e
            r=rounds.get(e.get('round_index'))
            if typ=='kill':
                kills+=e.get('killer')==seat; deaths+=e.get('target')==seat
                if r is not None and (e.get('killer')==seat or e.get('target')==seat):r.setdefault('own_kill_events',[]).append(e)
            if typ=='hit' and e.get('player')==seat:
                damage+=e.get('hp_damage',0);hit_targets[str(e.get('target'))]+=1
                if r is not None:
                    r['counts']['own_hit_events']+=1;r.setdefault('own_damage',0);r['own_damage']+=e.get('hp_damage',0)
            if typ=='fire' and e.get('player')==seat and r is not None:r['counts']['own_actual_fire_events']+=1
            if r is not None and typ in ('round_end','plant_complete_detail','defuse_complete','bomb_explode'):
                r.setdefault('objective_events',[]).append(e)
    result['public_replay']=dict(event_counts=counts,event_samples=samples,kills=kills,deaths=deaths,
        hp_damage=damage,hit_targets=hit_targets,events_sha256=hashlib.sha256(events.read_bytes()).hexdigest())
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log',type=Path,required=True);p.add_argument('--events',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=review(a.log,a.events)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('integrity','outcome')},indent=2))
    print('K/D/damage',result['public_replay']['kills'],result['public_replay']['deaths'],result['public_replay']['hp_damage'])
