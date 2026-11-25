"""Measure actual FPS motion and recovery loops, without simulating new paths."""
import argparse
from collections import Counter, deque
import json
import math
from pathlib import Path
import types

from evaluate_fps_navigation import load_agent, reference_file
from evaluate_fps_retake import equal
from review_fps_export import POLICIES

V3='65652c51f1ef60fb1f365b45bfff3eaa947c7f40'
V2='e27acc998e08d5dc3f59bac6e0fa5e4a99329058'


def run(path):
    with path.open(encoding='utf-8') as f:metadata=json.loads(next(f))
    ref=POLICIES[metadata['participants'][0]['source_hash']]
    control_ref=V2 if ref==V3 else V3
    delta_key='changed_vs_v2' if control_ref==V2 else 'changed_vs_v3'
    nav=types.ModuleType('navigation_audit_nav')
    exec(compile(reference_file(ref,'strategies/fps/waypoint_nav.py'),'<frozen-nav>','exec'),nav.__dict__)
    p=load_agent('navigation_actual',nav,ref).Candidate()
    control_nav=types.ModuleType('navigation_control_nav')
    exec(compile(reference_file(control_ref,'strategies/fps/waypoint_nav.py'),'<control-nav>','exec'),control_nav.__dict__)
    old=load_agent('navigation_control',control_nav,control_ref).Candidate()
    rounds={};frames={};current=None;detour=p._detour;window=deque();still_run=0
    def instrument(pos,goal,target,tick):
        blocked=p._node_at.get(tuple(target))
        repeated=p._failed_anchors.get(blocked,-1)>tick
        route=detour(pos,goal,target,tick)
        q=rounds[current];q['detour_calls']+=1
        q['detours_to_still_blacklisted_target']+=repeated
        key=','.join(f'{v:.3f}' for v in target);q['blocked_target_counts'][key]+=1
        if len(q['first_detours'])<8:
            q['first_detours'].append(dict(tick=tick,pos=list(pos),goal=list(goal),target=list(target),
                chosen_anchor=route[0] if route else None,route_points=len(route),long_avoid=p.long_avoid))
        return route
    p._detour=instrument
    agree=rows=0
    with path.open(encoding='utf-8') as f:
        header=json.loads(next(f))
        assert header['participants'][0]['source_hash'] in POLICIES
        for line in f:
            row=json.loads(line)
            if 'observation' not in row:continue
            rows+=1;o=row['observation'];m=o['me'];tick=o['match_tick'];current=o['round_index']
            q=rounds.setdefault(current,dict(side=o['side'],detour_calls=0,
                blocked_target_counts=Counter(),first_detours=[],jump_requests=0,visible_no_fire=0,
                longest_stationary_xy_calls=0,known_bomb_seen=False,scan_site_changes=[],jump_samples=[],
                detours_to_still_blacklisted_target=0))
            a=p.act(o);agree+=equal(a,row['action']);before=old.act(o);q[delta_key]=q.get(delta_key,0)+(not equal(a,before))
            if o['phase']=='live' and m['alive']:
                pos=[m[k] for k in ('x','y','z')];q['jump_requests']+=bool(a.get('jump'))
                if a.get('jump') and len(q['jump_samples'])<8:
                    q['jump_samples'].append(dict(tick=tick,pos=pos,target=p.path[0] if p.path else None,
                        grounded=m['grounded'],jump_tried=p._jump_tried,
                        progress_window_tick=getattr(p,'_window_tick',None),escape=p.escape))
                q['visible_no_fire']+=bool(o['visible']) and not a.get('fire')
                q['known_bomb_seen']|=o['bomb']['state']=='planted' and bool(o['bomb'].get('position'))
                frames.setdefault(current,[]).append((tick,pos))
                navigating=not o['visible'] and not a.get('use') and abs(a.get('move_forward',0))+abs(a.get('move_right',0))>.1
                if not navigating or (window and (window[-1][0]!=tick-3 or window[-1][2]!=current)):
                    window.clear();still_run=0
                if navigating:
                    window.append((tick,pos,current))
                    while window and window[0][0]<tick-120:window.popleft()
                    still=(window and tick-window[0][0]>=120 and max(math.dist(pos[:2],p0[:2]) for _,p0,_ in window)<.2)
                    still_run=still_run+1 if still else 0
                    q['longest_stationary_xy_calls']=max(q['longest_stationary_xy_calls'],still_run)
                if p.retake_site is not None:
                    switches=q['scan_site_changes'];site=p.sites[p.retake_site]['id']
                    if not switches or switches[-1]['site']!=site:switches.append(dict(tick=tick,site=site))
            else:window.clear();still_run=0
    assert agree==rows
    for i,q in rounds.items():
        if i not in frames:continue
        data=frames[i];end=data[-1][0];tail=[(t,pos) for t,pos in data if t>=end-3600]
        q['last_alive_60s_region']=dict(start_tick=tail[0][0],end_tick=tail[-1][0],
            x_range=[min(pos[0] for _,pos in tail),max(pos[0] for _,pos in tail)],
            y_range=[min(pos[1] for _,pos in tail),max(pos[1] for _,pos in tail)],
            horizontal_travel_m=sum(math.dist(a[:2],b[:2]) for (_,a),(_,b) in zip(tail,tail[1:])))
        q['stationary_xy_seconds_after_initial_2s']=q['longest_stationary_xy_calls']/20
    return dict(schema='fps-actual-navigation-audit-v2',match_id=header['job_id'],source_commit=ref,control_source_commit=control_ref,
        rows=rows,reproduced_requested_actions=agree,rounds=rounds,
        note='Actual recorded motion only. Stationary means commanded navigation with all XY samples in a 2s window within 0.2m; excludes first 2s. Tail regions are descriptive, not proof every command was blocked. Control comparison keeps actual historical positions and visibility.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--log',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=run(a.log)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print('Rows and frozen action agreement:',r['rows'],r['reproduced_requested_actions'])
    for i,q in r['rounds'].items():print(int(i)+1,'detours',q['detour_calls'],'jump',q['jump_requests'],'changed_vs_control',q.get('changed_vs_v3',q.get('changed_vs_v2')),'stationary_s',q['stationary_xy_seconds_after_initial_2s'])
