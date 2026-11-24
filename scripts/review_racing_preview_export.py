"""Audit a frozen non-Parkland Racing export; no new race is simulated."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from evaluate_racing_recovery import Policy, ROOT, blob, equal, lateral
from review_racing_export import verify_package

BASE = 'ee12c93d76f6ecefc9569776b925139adbd7c860'


def review(path, events, manifest):
    policy=Policy('preview_review',manifest['git_sha']);old=Policy('preview_old',BASE)
    assert blob(BASE,'preview_driver.py')==blob(manifest['git_sha'],'preview_driver.py')
    d=policy.preview._driver; saved={}; parkland_calls=0
    original_plan=policy.preview.plan_speed;original_traffic=policy.preview.traffic_plan
    original_pass=d.passing_line
    def main_act(obs):
        nonlocal parkland_calls
        parkland_calls+=1
        raise AssertionError('Unexpected Parkland dispatch')
    policy.main._driver.act=main_act
    def plan(o,curve):
        target=original_plan(o,curve);saved.update(free_target=target,current_curve=curve)
        return target
    def traffic(o,target,offset,reserve=False):
        result=original_traffic(o,target,offset,reserve)
        saved.update(traffic_target=result[0],offset=result[1],passing_enabled=reserve)
        return result
    def passing(o,target):
        before=d.pass_seat; result=original_pass(o,target)
        saved['pass_started']=d.pass_seat is not None and d.pass_seat!=before
        fronts=[r for r in o['rivals'] if r['status']=='racing' and r['gap_m']>0 and abs(r['dn'])<2.7]
        front=min(fronts,key=lambda r:r['gap_m']) if fronts else None
        saved['nearest_front_screen']=('no_same_column_front' if front is None else
            'gap_le5' if front['gap_m']<=5 else 'gap_ge27' if front['gap_m']>=27 else
            'speed_advantage_le6' if target-front['speed_mps']<=6 else 'eligible')
        leaders=[r for r in o['rivals'] if r['status']=='racing' and 5<r['gap_m']<27
                 and abs(r['dn'])<2.7 and target-r['speed_mps']>6]
        saved['pass_gate']=('active' if d.pass_seat is not None else
            'no_close_slow_leader' if not leaders else
            'waiting_1point5s' if o['time']-d.wait_since<1.5 else
            'heading_or_curvature' if abs(o['self']['heading_error'])>.1 or max(abs(r[1]) for r in o['preview']['rows'][:6])>.004 else
            'cooldown' if o['time']<d.retry_at else 'lane_rejected')
        if before is not None and d.pass_seat is None:
            saved['pass_release']=dict(prior_seat=before,visible_leader=next((r for r in o['rivals'] if r['seat']==before),None))
        return result
    policy.preview.plan_speed=plan;policy.preview.traffic_plan=traffic;d.passing_line=passing
    rows=raw=agreement=old_agreement=faults=0;digest=hashlib.sha256();groups={};bounds={}
    footer=outcome=None;previous=None;start=None;pit=[];pass_starts=[];pass_releases=[];slow=[]
    with path.open('rb') as f:
        h=json.loads(next(f));own=h['participants'][0];seat=own['seat']
        assert own['source_hash']==manifest['zip_sha256'] and len(h['participants'])==1
        assert h['game_id']=='racing' and h['observation_semantics']=='recorded_policy_input'
        assert h['decision_encoding']=='original-jsonl-bytes'
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                if r.get('type')=='outcome':outcome=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation'];c=o['self']
            assert r['seat']==o['seat']==seat and abs(o['track_length_m']-5803.167)<.5
            saved.clear();a=policy.act(o);lateral.legal(a);b=old.act(o)
            agreement+=equal(a,r['action']);old_agreement+=equal(a,b);faults+=bool(r['fault'])
            phase=o['phase'];key=phase if phase!='race' else 'race_lap_'+str(c['lap']+1)
            q=groups.setdefault(key,dict(calls=0,flags=Counter(),wet_min=1.,wet_max=0.,
                min_fuel=999.,max_wear=0.,min_tyre_temp=999.,max_tyre_temp=0.,
                speed_sum=0.,free_target_sum=0.,final_target_sum=0.,ers_modes=Counter(),
                min_soc=999.,max_soc=0.,heading_max=0.,pass_seats=Counter()))
            q['calls']+=1;flags=q['flags'];wet=o['weather']['track_wetness']
            q['wet_min']=min(q['wet_min'],wet);q['wet_max']=max(q['wet_max'],wet)
            q['min_fuel']=min(q['min_fuel'],c['fuel_kg']);q['max_wear']=max(q['max_wear'],max(c['tyre_wear']))
            q['min_tyre_temp']=min(q['min_tyre_temp'],min(c['tyre_temp_c']));q['max_tyre_temp']=max(q['max_tyre_temp'],max(c['tyre_temp_c']))
            q['speed_sum']+=c['speed_mps'];q['free_target_sum']+=saved['free_target']
            n=c['n'];heading=c['heading_error'];off=saved['offset']
            lat_speed=c.get('vx',c['speed_mps'])*math.sin(heading)+c.get('vy',0)*math.cos(heading)
            projected=n+.6*lat_speed;left,right=o['preview']['rows'][0][3:5]
            margin=min(left-max(n,projected),right+min(n,projected))
            travel=c['distance']-d.start_distance
            lateral_error=abs(n-off) if phase=='race' and (travel<150 or d.pass_seat is not None) else abs(n)
            recovery=lateral_error>3 or margin<=2
            target=min(saved['traffic_target'],15.) if recovery else saved['traffic_target']
            q['final_target_sum']+=target;q['ers_modes'][str(a['ers_mode'])]+=1
            q['min_soc']=min(q['min_soc'],c['ers_soc_mj']);q['max_soc']=max(q['max_soc'],c['ers_soc_mj'])
            q['heading_max']=max(q['heading_max'],abs(heading))
            flags['pit_callbacks']+=bool(c['pit_state']);flags['traffic_cap_gt_3mps']+=saved['traffic_target']<saved['free_target']-3
            flags['recovery_cap_gt_3mps']+=recovery and saved['traffic_target']>18
            flags['large_heading']+=abs(heading)>.3;flags['passing_enabled']+=saved['passing_enabled']
            flags['pass_active']+=d.pass_seat is not None;flags['pass_started']+=bool(saved.get('pass_started'))
            flags['brake_gt_point2']+=a['brake']>.2;flags['fuel_under5']+=c['fuel_kg']<5
            if saved.get('passing_enabled'):flags['pass_gate:'+saved['pass_gate']]+=1
            if saved.get('passing_enabled') and saved['traffic_target']<saved['free_target']-3:
                flags['traffic_front_screen:'+saved['nearest_front_screen']]+=1
            flags['soc_ge3']+=c['ers_soc_mj']>=3
            if d.pass_seat is not None:q['pass_seats'][str(d.pass_seat)]+=1
            if phase=='race':
                start=o['time'] if start is None else start
                state=c['pit_state']
                if not pit or pit[-1]['state']!=state:pit.append(dict(time_s=o['time'],elapsed_s=o['time']-start,state=state,lap=c['lap']+1,station_m=c['s']))
                if saved.get('pass_started'):pass_starts.append(dict(time_s=o['time'],elapsed_s=o['time']-start,lap=c['lap']+1,station_m=c['s'],target_seat=d.pass_seat,offset_m=d.pass_offset))
                if saved.get('pass_release'):pass_releases.append(dict(time_s=o['time'],elapsed_s=o['time']-start,lap=c['lap']+1,**saved['pass_release']))
                if previous:
                    distance,t=previous
                    for number in range(1,o['laps_total']):
                        boundary=number*o['track_length_m']
                        if distance<boundary<=c['distance']:bounds[number]=t+(o['time']-t)*(boundary-distance)/(c['distance']-distance)
                previous=c['distance'],o['time']
                if not state and saved['traffic_target']<saved['free_target']-3 and (not slow or o['time']-slow[-1]['time_s']>20):
                    slow.append(dict(time_s=o['time'],lap=c['lap']+1,station_m=c['s'],speed_mps=c['speed_mps'],free_target_mps=saved['free_target'],traffic_target_mps=saved['traffic_target']))
    assert footer and footer['complete'] and outcome
    assert (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(seat):rows} and agreement==old_agreement==rows and parkland_calls==0
    own_result=next(c for c in outcome['classification'] if c['seat']==seat)
    for key,q in groups.items():
        for field in ('speed','free_target','final_target'):q['mean_'+field+'_mps']=q.pop(field+'_sum')/q['calls']
        if key.startswith('race_lap_'):
            lap=int(key[-1]);a=start if lap==1 else bounds.get(lap-1)
            b=start+own_result['total_time_s'] if lap==own_result['laps'] else bounds.get(lap)
            if a is not None and b is not None:q['estimated_lap_s']=b-a
    counts=Counter();selected=[]
    with events.open(encoding='utf-8') as f:
        for line in f:
            e=json.loads(line)
            if seat not in e['seats']:continue
            counts[e['phase']+':'+e['kind']]+=1
            if e['kind'] in ('incident','pit_entry','pit_exit','qualifying_lap','overtake','finished','fastest_lap'):
                selected.append(dict(phase=e['phase'],kind=e['kind'],time_s=e['time'],elapsed_s=e['time']-start if e['phase']=='race' else None,payload=e['payload']))
    return dict(schema='racing-crossover-frozen-review-v1',match_id=h['job_id'],track='crossover',rules_version=h['rules_version'],
        own_seat_zero_based=seat,source_commit=manifest['git_sha'],zip_sha256=own['source_hash'],frozen_version_id=own['version_id'],
        integrity=dict(rows=rows,raw_bytes=raw,sha256=digest.hexdigest(),action_agreement=agreement,faults=faults),
        dispatch=dict(controller='preview_driver.py',parkland_calls=0,unchanged_preview_vs_previous_source=BASE,identical_actions_vs_previous=old_agreement,
            wet_early_preparation_executed=False),classification=own_result,all_classification=outcome['classification'],
        groups=groups,pit_state_transitions=pit,pass_start_proposals=pass_starts,pass_releases=pass_releases,traffic_samples=slow,
        public_events=dict(own_counts=counts,selected=selected,sha256=hashlib.sha256(events.read_bytes()).hexdigest()),
        note='Recorded legal observations only; public events read afterwards. Lap estimates use interpolated distance crossings and grid start. Final classification is authoritative, not finished-event position. No strategy changed or counterfactual race simulated.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('log','events','manifest','archive','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();m=json.loads(a.manifest.read_text());verify_package(m,a.archive,BASE)
    r=review(a.log,a.events,m);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(integrity=r['integrity'],classification=r['classification'],dispatch=r['dispatch']),indent=2))
