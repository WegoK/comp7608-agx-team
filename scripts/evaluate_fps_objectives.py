"""Compare dropped-bomb decisions against the previous personal FPS candidate.

Synthetic observations and map fixtures test decisions, not collision or wins.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from evaluate_fps_navigation import (
    ROOT, check_action, load_agent, load_module, reference_file,
)

BASELINE = '4555a0a57521ffdc998992c93b6d7e567ebe3e74'


def make_map(data):
    start = data['spawn_zones'][0]['pads'][0]['position']
    dropped = [start[0] + 4, start[1] + 3, start[2]]
    sites = [dict(id=s['id'], center=list(s['center']), radius=s['radius_m'])
             for s in data['bombsites']]
    nodes = [dict(id=0, position=list(start))]
    nodes += [dict(id=i + 1, position=list(site['center'])) for i, site in enumerate(sites)]
    nodes.append(dict(id=len(nodes), position=dropped))
    edges = [{'from': 0, 'to': n['id'], 'kind': 'walk'} for n in nodes[1:]]
    return dict(id=data['map_id'], sites=sites, nav=dict(nodes=nodes, edges=edges)), start, dropped


def observation(row, start, dropped, side='attack', seat=0, round_index=0):
    return dict(map_id=row['id'], round_index=round_index, seat=seat, side=side,
                match_tick=0, round_tick=360, phase='live', map=row,
                teammates=[], visible=[], bomb=dict(state='world', position=list(dropped)),
                me=dict(x=start[0], y=start[1], z=start[2], yaw=0.0, pitch=0.0,
                        alive=True, grounded=True, crouch=False, blind_ticks=0,
                        cash=800, armor=0, has_kit=False, carrying_bomb=False,
                        weapons=[None, dict(ammo=12, reserve=24, reload_until=0), None]))


def run_pair(reference, candidate, obs):
    old, new = reference.Candidate(), candidate.Candidate()
    actions = [p.act(copy.deepcopy(obs)) for p in (old, new)]
    for action in actions:
        check_action(action)
    return old, new, actions


def unchanged(reference, candidate, obs):
    old, new, actions = run_pair(reference, candidate, obs)
    assert actions[0] == actions[1]
    assert old.goal == new.goal
    return actions[1]


def evaluate(reference, candidate, kit):
    maps = []
    attacking, changed, defending = 0, 0, 0
    for path in sorted((kit / 'data').glob('*.mapdata.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        row, start, dropped = make_map(data)
        local_attack, local_defend = 0, 0
        for side in ('attack', 'defend'):
            for seat in range(10):
                for round_index in (0, 1):
                    obs = observation(row, start, dropped, side, seat, round_index)
                    old, new, actions = run_pair(reference, candidate, obs)
                    if side == 'attack':
                        assert old.goal != dropped and new.goal == dropped
                        assert actions[1].get('use') not in ('plant', 'defuse')
                        attacking += 1
                        changed += int(old.goal != new.goal)
                        local_attack += 1
                    else:
                        assert old.goal == new.goal and actions[0] == actions[1]
                        defending += 1
                        local_defend += 1
        maps.append(dict(map_id=data['map_id'], synthetic_attack_cases=local_attack,
                         synthetic_defend_cases=local_defend,
                         public_geometry_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert len(maps) == 5

    data = json.loads((kit / 'data/fps-borgo.mapdata.json').read_text(encoding='utf-8'))
    row, start, dropped = make_map(data)
    base = observation(row, start, dropped)
    guards = []
    for label, bomb in [
        ('carried_bomb', dict(state='carried')),
        ('unknown_bomb', dict(state='unknown')),
        ('missing_position', dict(state='world')),
        ('null_position', dict(state='world', position=None)),
        ('empty_position', dict(state='world', position=[])),
        ('wrong_dimension', dict(state='world', position=[1, 2])),
        ('nonnumeric_position', dict(state='world', position=['x', 2, 3])),
        ('boolean_position', dict(state='world', position=[True, 2, 3])),
        ('nonfinite_position', dict(state='world', position=[float('nan'), 2, 3])),
        ('stale_position_on_unknown_state', dict(state='unknown', position=dropped)),
        ('planted_bomb', dict(state='planted', position=dropped)),
    ]:
        obs = copy.deepcopy(base)
        obs['bomb'] = bomb
        unchanged(reference, candidate, obs)
        guards.append(label)

    for phase in ('buy', 'post', 'finished'):
        obs = copy.deepcopy(base)
        obs['phase'] = phase
        unchanged(reference, candidate, obs)
        guards.append('phase_' + phase)
    obs = copy.deepcopy(base)
    obs['me']['alive'] = False
    assert unchanged(reference, candidate, obs) == {}
    guards.append('dead_player')

    obs = copy.deepcopy(base)
    obs['visible'] = [dict(x=start[0] + 10, y=start[1], z=start[2], crouch=False, parts=['chest'])]
    action = unchanged(reference, candidate, obs)
    assert 'fire' in action and not action.get('move_forward') and not action.get('move_right')
    guards.append('visible_enemy_keeps_combat_priority')

    # Feed an explicit pickup observation; this is not a simulated pickup event.
    player = candidate.Candidate()
    player.act(copy.deepcopy(base))
    assert player.goal == dropped
    obs = copy.deepcopy(base)
    obs.pop('map')
    obs.update(match_tick=3, bomb=dict(state='carried'))
    obs['me'].update(x=dropped[0], y=dropped[1], z=dropped[2], carrying_bomb=True)
    check_action(player.act(obs))
    site = row['sites'][0]['center']
    assert player.goal == site
    obs['me'].update(x=site[0], y=site[1], z=site[2])
    for tick in (6, 9, 12):
        obs['match_tick'] = tick
        action = player.act(obs)
        check_action(action)
        assert action['use'] == 'plant'
        assert not action.get('move_forward') and not action.get('fire')
    guards.append('pickup_observation_resumes_site_and_continuous_plant')

    # A known bomb must replace a stale recovery route immediately.
    player = candidate.Candidate()
    previous = copy.deepcopy(base)
    previous['bomb'] = dict(state='carried')
    player.act(previous)
    player.escape = (0, 0.0, 1, [0, 0, 0])
    obs = copy.deepcopy(base)
    obs.pop('map')
    obs['match_tick'] = 3
    player.act(obs)
    assert player.goal == dropped and player.motion_task[0] == tuple(dropped)
    guards.append('drop_switches_goal_and_clears_old_navigation_task')

    # A defender at a planted bomb still defuses immediately.
    obs = copy.deepcopy(base)
    obs.update(side='defend', bomb=dict(state='planted', position=list(start)))
    assert unchanged(reference, candidate, obs)['use'] == 'defuse'
    guards.append('defuse_priority_preserved')
    return dict(public_maps=maps, synthetic_attack_cases=attacking,
                attack_goal_changes=changed, unchanged_defend_cases=defending,
                guard_cases=guards, observed_pickup_transition_checked=True,
                scope='Constructed nav graphs and observations; not collisions, automatic pickup adjudication, full matches or win rate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit-root', type=Path, default=ROOT / 'agx-fps-kit')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    for name in ('starter_player.py', 'waypoint_nav.py'):
        assert (ROOT / 'strategies/fps' / name).read_bytes().replace(b'\r\n', b'\n') == reference_file(BASELINE, 'strategies/fps/' + name)
    nav = load_module('objectives_navigation', ROOT / 'strategies/fps/waypoint_nav.py')
    reference = load_agent('objectives_baseline', nav, BASELINE)
    candidate = load_agent('objectives_candidate', nav)
    result = dict(schema='fps-bomb-recovery-decisions-v1', baseline_commit=BASELINE,
                  candidate_agent_lf_sha256=hashlib.sha256((ROOT / 'strategies/fps/agent.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                  checks=evaluate(reference, candidate, args.kit_root.resolve()),
                  platform_validation='not_run', competition_activation='not_run', formal_matches=0)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
