"""Inspect Parkland pass, power and arrival-control opportunities on old inputs.

A temporary activation-only probe reuses the original 0.4s cap check. It is
restored before returning the actual action; no new race is simulated.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from evaluate_racing_recovery import Policy, equal

REF='9c58b2da29a7d90949ece7aadfc2413301e7929f'
BUNDLE='b0957ee4e8625a1e46cf622afc54d2362d3eb560a6bbc141f54f5b71203cacba'


def audit(path):
    policy=Policy('opportunity_audit',REF);original=policy.main.Driver.trajectory_acceleration
    frame={};groups={};episodes=[];episode=None;rows=raw=agree=0;digest=hashlib.sha256();footer=None
    def arrival(self,acceleration,target,speed_at,yielding,braking,launch_goal=None):
        actual=original(self,acceleration,target,speed_at,yielding,braking,launch_goal=launch_goal)
        frame.update(original_acceleration=acceleration,actual_arrival_acceleration=actual,
                     guard=braking,yielding=yielding,probe_attempted=False)
        if (self.o['phase']=='race' and self.c['lap']>=1 and not self.c['pit_state']
                and self.wet<.5 and self.mode in ('LINE','DRAFT') and not self.chasing
                and acceleration<0 and target>self.v+.5 and not yielding and braking==0
                and self.gg_active and not self.c['front_sliding'] and not self.c['rear_sliding']
                and abs(self.goal-self.offset)<=.25 and self.tracking_error()<=.4):
            saved_mode=self.mode;saved_diag=dict(self.diagnostic)
            try:
                # Only bypass the activation mode gate. The recorded envelope,
                # target, braking/road caps and acceleration solver stay intact.
                self.mode='PASS'
                candidate=original(self,acceleration,target,speed_at,yielding,braking,launch_goal=launch_goal)
                frame.update(probe_attempted=True,probe_acceleration=candidate)
            finally:self.mode=saved_mode;self.diagnostic=saved_diag
        return actual
    policy.main.Driver.trajectory_acceleration=arrival
    with path.open('rb') as f:
        h=json.loads(next(f));assert h['participants'][0]['source_hash']==BUNDLE
        for line in f:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line);o=r['observation'];frame.clear()
            a=policy.act(o);agree+=equal(a,r['action']);d=policy.main._driver;c=o['self'];diag=d.diagnostic
            if o['phase']!='race':continue
            q=groups.setdefault(c['lap'],dict(rows=0,driving_rows=0,flags=Counter(),modes=Counter(),ers_modes=Counter(),
                energy_vetoes=Counter(),min_soc=99.,max_soc=0.,soc_sum=0.,min_pu_temp=999.,max_pu_temp=0.,
                tyre_by_compound={},probe_samples=[],pass_forecast_samples=[],last_sample_s=-999.,
                first_soc_under_point1=None,straight_entries=[],last_straight=None))
            q['rows']+=1
            if c['pit_state']:
                if episode:episodes.append(episode);episode=None
                continue
            q['driving_rows']+=1;flags=q['flags'];q['modes'][d.mode]+=1;q['ers_modes'][str(a['ers_mode'])]+=1
            q['min_soc']=min(q['min_soc'],c['ers_soc_mj']);q['max_soc']=max(q['max_soc'],c['ers_soc_mj']);q['soc_sum']+=c['ers_soc_mj']
            q['min_pu_temp']=min(q['min_pu_temp'],c['pu_temp_c']);q['max_pu_temp']=max(q['max_pu_temp'],c['pu_temp_c'])
            tyre=q['tyre_by_compound'].setdefault(c['compound'],dict(calls=0,mean_temp_sum=0.,below_optimum_gt20_calls=0,public_plan_optimum_c=d.p['compound_optimum'][c['compound']]))
            mean_temp=sum(c['tyre_temp_c'])/4;tyre['calls']+=1;tyre['mean_temp_sum']+=mean_temp
            tyre['below_optimum_gt20_calls']+=mean_temp<tyre['public_plan_optimum_c']-20
            flags['soc_ge3point8']+=c['ers_soc_mj']>=3.8
            flags['soc_lt_point1']+=c['ers_soc_mj']<.1
            if c['ers_soc_mj']<.1 and q['first_soc_under_point1'] is None:
                q['first_soc_under_point1']=dict(global_s=o['time'],station_m=c['s'],soc_mj=c['ers_soc_mj'])
            zone=next((i for i,(start,end,_) in enumerate(d.energy_windows) if start<=d.s%d.length<end),None)
            if zone is not None and zone!=q['last_straight']:
                q['straight_entries'].append(dict(zone=zone,global_s=o['time'],station_m=c['s'],soc_mj=c['ers_soc_mj']))
            q['last_straight']=zone
            flags['ers_ge2_outside_long_straight']+=a['ers_mode']>=2 and zone is None
            flags['aid_available']+=bool(c['aid_available']);flags['override_available']+=bool(c['override_available'])
            flags['aid_requested']+=bool(a.get('aid'));flags['override_requested']+=bool(a.get('override'))
            flags['power_substitution']+=bool(diag.get('deployment_substitution'))
            if diag.get('energy_veto'):q['energy_vetoes'][diag['energy_veto']]+=1
            flags['guard_deceleration_requested']+=frame.get('guard',0)>0
            flags['actual_brake_with_guard']+=a['brake']>.01 and frame.get('guard',0)>0
            flags['brake_while_below_target']+=a['brake']>.01 and diag['target']>d.v+.5
            flags['arrival_probe_attempted']+=bool(frame.get('probe_attempted'))
            improvement=(frame.get('probe_attempted') and frame['probe_acceleration']>=0
                         and frame['probe_acceleration']>frame['actual_arrival_acceleration']+.5 and a['brake']>.01)
            flags['arrival_probe_nonnegative_instead_of_actual_brake']+=bool(improvement)
            if improvement and (not q['probe_samples'] or o['time']-q['probe_samples'][-1]['global_s']>2):
                q['probe_samples'].append(dict(global_s=o['time'],station_m=c['s'],mode=d.mode,speed_mps=d.v,
                    target_mps=diag['target'],actual_brake=a['brake'],actual_acceleration=frame['actual_arrival_acceleration'],
                    alternative_model_acceleration=frame['probe_acceleration']))
            target=next((r for r in d.rivals if r['seat']==d.target_car),None)
            if d.mode=='PASS' and target:
                flags['pass_target_gap_gt60']+=target['gap_m']>60
                flags['pass_target_gap_gt100']+=target['gap_m']>100
                flags['pass_lane_tracking_error_gt1']+=abs(d.goal-d.offset)>1.
                if c['lap']<2 and o['time']-q['last_sample_s']>=1:
                    saved_diag=dict(d.diagnostic)
                    try:
                        catch=d.catch_window(target)
                        q['pass_forecast_samples'].append(dict(global_s=o['time'],gap_m=target['gap_m'],
                            target_seat=target['seat'],goal_m=d.goal,offset_m=d.offset,catch_window_s=catch))
                    finally:d.diagnostic=saved_diag
                    q['last_sample_s']=o['time']
                key=(d.target_car,c['lap'])
                if episode is None or episode['key']!=list(key):
                    if episode:episodes.append(episode)
                    episode=dict(key=list(key),start_s=o['time'],end_s=o['time'],start_gap_m=target['gap_m'],end_gap_m=target['gap_m'],
                                 min_gap_m=target['gap_m'],max_gap_m=target['gap_m'],calls=0,goal_abs_sum=0.,brake_calls=0)
                episode['end_s']=o['time'];episode['end_gap_m']=target['gap_m'];episode['calls']+=1
                episode['min_gap_m']=min(episode['min_gap_m'],target['gap_m']);episode['max_gap_m']=max(episode['max_gap_m'],target['gap_m'])
                episode['goal_abs_sum']+=abs(d.goal);episode['brake_calls']+=a['brake']>.01
            elif episode:episodes.append(episode);episode=None
    if episode:episodes.append(episode)
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(h['participants'][0]['seat']):rows} and agree==rows
    for q in groups.values():
        q.pop('last_sample_s');q.pop('last_straight')
        q['pass_sample_count']=len(q['pass_forecast_samples'])
        q['pass_sample_no_six_second_catch']=sum(e['catch_window_s'] is None for e in q['pass_forecast_samples'])
        q['mean_soc']=q.pop('soc_sum')/max(1,q['driving_rows'])
        for tyre in q['tyre_by_compound'].values():tyre['mean_temp_c']=tyre.pop('mean_temp_sum')/tyre['calls']
    for e in episodes:
        e['duration_s']=e['end_s']-e['start_s'];e['mean_abs_goal_m']=e.pop('goal_abs_sum')/e['calls']
    return dict(schema='racing-parkland-opportunity-audit-v1',match_id=h['job_id'],source_commit=REF,
        recorded_action_agreement=agree,decision_sha256=digest.hexdigest(),laps_zero_based=groups,pass_episodes=episodes,
        note='Recorded observations only. Activation probe temporarily labels LINE/DRAFT as PASS solely to exercise the unchanged arrival solver, then restores mode and diagnostics before actual action. All recorded actions reproduced. No new trajectory, tyre evolution, overtake or lap time simulated.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=audit(a.log);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    for lap,q in r['laps_zero_based'].items():print(lap,q['flags'],q['ers_modes'],'SOC',q['min_soc'],q['max_soc'],q['mean_soc'])
