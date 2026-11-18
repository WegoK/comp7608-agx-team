"""Check terminal oscillation on the logged Borgo path and obstacle recovery in the peer movement simulator."""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

FIRST, LAST = 100872, 102498


def player(path, tag):
    spec = importlib.util.spec_from_file_location(tag, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Player()


def position(me):
    return [me[k] for k in ('x', 'y', 'z')]


def motion(action):
    return math.hypot(action.get('move_right', 0), action.get('move_forward', 0))


def equal_action(a, b):
    if a.keys() != b.keys():
        return False
    return all(math.isclose(v, b[k], rel_tol=0, abs_tol=1e-8) if isinstance(v, (int,float)) else v == b[k] for k,v in a.items())


def simulate(model, first_me, observations, world, goal=None):
    from fpssim.movement import Mover, PlayerState, max_speed
    mover = Mover()
    s = PlayerState(**{k: v for k, v in first_me.items() if k in PlayerState.__dataclass_fields__})
    top_speed = max_speed(first_me['weapons'][first_me['active_slot']]['id'], first_me['zoom_level'])
    poses, actions = [], []
    goal_r = []
    for j, fixed in enumerate(observations):
        o = {**fixed, 'me': {**fixed['me'], **s.__dict__}}
        action = model.act(o) if goal is None else model.move(o, goal, {})
        poses.append(position(o['me']))
        goal_r.append(math.dist(poses[-1], goal or fixed['map_goal']))
        actions.append(motion(action))
        if j+1 == len(observations):
            break
        for i in range(1, 4):
            mover.step(s, action, top_speed, world, (), o['match_tick'] + i)
    result = dict(frames=len(poses), move_frames=sum(x > 1e-8 for x in actions),
                  cumulative_path_m=round(sum(math.dist(x,y) for x,y in zip(poses,poses[1:])),3),
                  final_distance_to_goal_m=round(goal_r[-1],3), reached_0_5_m=any(d <= .5 for d in goal_r),
                  arrival_s=next((round(i/20, 2) for i,d in enumerate(goal_r) if d <= .5), None))
    if goal is None:
        result['xy_rmse_vs_recording_m'] = round(math.sqrt(sum(math.dist(p[:2], position(o['me'])[:2])**2 for p,o in zip(poses,observations))/len(poses)),4)
    return result


def evaluate(args):
    sys.path.insert(0,str(args.peer_root))
    from fpssim.collision import BoxWorld
    paths = {'baseline':args.baseline,'candidate':args.candidate}
    policies = {k:player(p,k) for k,p in paths.items()}
    observed_dist, stored, single, matching, mobile = [], [], 0, 0, {'baseline':0,'candidate':0}
    for line in args.log.open():
        row = json.loads(line)
        o = row.get('observation')
        if o is None:
            continue
        if 'map' in o:
            static = o['map']
        t=o['match_tick']
        if t > LAST:
            break
        if t == FIRST:
            starts = {k:copy.deepcopy(p) for k,p in policies.items()}
        actions = {k:p.act(o) for k,p in policies.items()}
        if t < FIRST: continue
        matching += equal_action(actions['baseline'], row['action'])
        for k,a in actions.items():mobile[k] += motion(a)>1e-8
        base=policies['baseline']
        single+=base.path==[base.goal]
        o['map_goal'] = base.goal
        observed_dist.append(math.dist(position(o['me']), base.goal))
        stored.append(o)
    assert single == 543 and len(stored)==543
    assert all(not any(t['alive'] for t in o['teammates']) for o in stored)
    assert FIRST == stored[0]['match_tick'] and LAST == stored[-1]['match_tick']
    base=stored[0]
    pos=position(base['me'])
    boxes=static['boxes']
    near=min(math.hypot(max(0.,abs(pos[0]-b['x'])-b['w']/2),max(0.,abs(pos[1]-b['y'])-b['d']/2)) for b in boxes if b['z']<pos[2]+1.7 and b['z']+b['h']>pos[2]+.45)
    closed={k:simulate(p,base['me'],stored,BoxWorld(static)) for k,p in starts.items()}
    synthetic={}
    for width in (0, .6, 1, 2, 4):
        box=[dict(x=2.0,y=0.,z=0.,w=.6,d=width,h=2.0)] if width else []
        geom={'id':'movement-test','boxes':box,'ground':{'z':0}}
        obs={**base,'map_goal':[4.5,0.,0.],'me':{**base['me'],'x':0.,'y':0.,'z':-.00189,'vx':0.,'vy':0.,'vz':0.,'yaw':0.,'pitch':0.}}
        sequence=[{**obs,'match_tick':i*3} for i in range(401)]
        synthetic[str(width)]={}
        for k,path in paths.items():
            pol=player(path,k)
            pol.load_map({**geom,'sites':[],'nav':{'nodes':[],'edges':[]}})
            synthetic[str(width)][k]=simulate(pol,obs['me'],sequence,BoxWorld(geom),goal=obs['map_goal'])
    # Test the arrival tolerance at its boundary and immediately after a goal switch.
    p = player(args.candidate, 'boundary')
    p.load_map({'sites': [], 'nav': {'nodes': [], 'edges': []}})
    po = copy.deepcopy(base)
    po['me'].update(x=0.,y=0.,z=0.,yaw=0.,pitch=0.)
    boundary = {}
    for j, metres in enumerate((0.49,0.5,0.51,4.5,0.1,3.)):
        po['match_tick'] += 3
        magnitude = motion(p.move(po,[metres,0.,0.],{}))
        boundary[str(metres)] = magnitude
        assert (magnitude == 0.) == (metres <= 0.5)
    assert matching == len(stored)
    return dict(boundary_move_magnitude=boundary,source_hash={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()},
                logged=dict(ticks=[FIRST,LAST],n=len(stored),goal=base['map_goal'],final_only=single,
                            goal_variants=len({tuple(o['map_goal']) for o in stored}),
                            distance_min_m=min(observed_dist),distance_max_m=max(observed_dist),
                            nearest_box_xy_gap_m_at_first_tick=near,alive_teammates=0,
                            baseline_matches_recorded_within_1e_8=matching,move_frames=mobile),
                local_motion_only=closed,synthetic_box_width_m=synthetic)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('baseline','candidate','log','peer-root','out'):p.add_argument('--'+k,type=Path,required=True)
    args=p.parse_args();result=evaluate(args);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
