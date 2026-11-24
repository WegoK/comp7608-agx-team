"""Verify a private FPS export and compare decisions without simulating a match.

Only aggregate results are written. Public events are read AFTER policy replay;
omniscient bomb locations never enter either policy's input or state.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import types

from evaluate_fps_navigation import ROOT, check_action, load_agent, reference_file

BASELINE = '4f5747cbde4296a235b042e60b7486a224f3dfd4'
BASELINE_BUNDLE_SHA256 = '2fb651073041be55b178e189c43631c96fc1ac0aae794ca0107a4959a60d6ba1'


def policies():
    for name in ('starter_player.py', 'waypoint_nav.py'):
        assert (ROOT / 'strategies/fps' / name).read_bytes().replace(b'\r\n', b'\n') == reference_file(BASELINE, 'strategies/fps/' + name)
    nav = types.ModuleType('retake_frozen_nav')
    exec(compile(reference_file(BASELINE, 'strategies/fps/waypoint_nav.py'), '<frozen-nav>', 'exec'), nav.__dict__)
    return load_agent('retake_reference', nav, BASELINE), load_agent('retake_candidate', nav)


def fixture():
    row = dict(id='nevaio-r3-v54', sites=[dict(id='A', center=[0, 0, 0], radius=4),
                                       dict(id='B', center=[20, 0, 0], radius=4)],
               nav=dict(nodes=[dict(id=0, position=[0, 0, 0]), dict(id=1, position=[20, 0, 0])],
                        edges=[{'from': 0, 'to': 1, 'kind': 'walk'}]))
    return dict(map_id=row['id'], map=row, round_index=0, seat=8, side='defend',
                match_tick=0, round_tick=360, phase='live', teammates=[], visible=[],
                bomb=dict(state='planted', fuse_end_tick=2400),
                me=dict(x=0, y=0, z=0, yaw=0, pitch=0, alive=True, grounded=True,
                        crouch=False, blind_ticks=0, cash=800, armor=0, has_kit=True,
                        carrying_bomb=False, weapons=[None, dict(ammo=12, reserve=24, reload_until=0), None]))


def synthetic_checks(old, new, combat_hold_ticks=0):
    cases = []

    def call(player, obs, tick):
        obs['match_tick'] = tick
        action = player.act(copy.deepcopy(obs))
        check_action(action)
        return action

    obs, player = fixture(), new.Candidate()
    for t in range(0, 150, 3):
        a = call(player, obs, t)
        assert a['move_forward'] == a['move_right'] == 0 and a['yaw_rate'] == 1
        assert player.retake_site == 0
    a = call(player, obs, 150)
    assert player.goal == [20, 0, 0] and not a['walk']
    cases.append('scan_then_rotate_after_150_ticks')
    # Explicitly inject arrival, rather than claiming a simulated trajectory.
    obs['me']['x'] = 20
    for t in range(153, 303, 3):
        call(player, obs, t)
        assert player.retake_site == 1
    call(player, obs, 303)
    assert player.retake_site == 0 and player.goal == [0, 0, 0]
    cases.append('injected_arrival_and_repeated_site_search')

    for interruption in ('combat', 'blind', 'airborne', 'left_site', 'callback_gap'):
        obs, player = fixture(), new.Candidate()
        for t in range(0, 75, 3):
            call(player, obs, t)
        paused = copy.deepcopy(obs)
        if interruption == 'combat':
            paused['visible'] = [dict(x=10, y=0, z=0, crouch=False, parts=['chest'])]
        elif interruption == 'blind':
            paused['me']['blind_ticks'] = 20
        elif interruption == 'airborne':
            paused['me']['grounded'] = False
        elif interruption == 'left_site':
            paused['me']['x'] = 2
        if interruption != 'callback_gap':
            call(player, paused, 75)
        resume = 78 + (combat_hold_ticks if interruption == 'combat' else 0)
        for t in range(78, resume + 150, 3):
            call(player, obs, t)
            assert player.retake_site == 0
        call(player, obs, resume + 150)
        assert player.retake_site == 1
        cases.append('continuous_scan_resets_on_' + interruption)

    for change in ('known_position', 'round_change', 'map_change', 'fuse_change', 'not_planted', 'buy', 'dead', 'side_change'):
        obs, player = fixture(), new.Candidate()
        call(player, obs, 0)
        if change == 'known_position':
            obs['bomb']['position'] = [0, 0, 0]
        elif change == 'round_change':
            obs['round_index'] = 1
        elif change == 'map_change':
            obs['map_id'] = obs['map']['id'] = 'borgo-r3-v54'
        elif change == 'fuse_change':
            obs['bomb']['fuse_end_tick'] = 2403
        elif change == 'not_planted':
            obs['bomb'] = dict(state='carried')
        elif change == 'buy':
            obs['phase'] = 'buy'
        elif change == 'dead':
            obs['me']['alive'] = False
        elif change == 'side_change':
            obs['side'] = 'attack'
        a = call(player, obs, 3)
        assert player.scan_start in (None, 3)
        if change == 'known_position':
            assert a['use'] == 'defuse' and player.retake_key is None
        cases.append('search_state_resets_on_' + change)

    for label in ('attack', 'ordinary_defend', 'combat', 'defuse', 'plant'):
        obs = fixture()
        if label == 'attack':
            obs['side'] = 'attack'
        elif label == 'ordinary_defend':
            obs['bomb'] = dict(state='carried')
        elif label == 'combat':
            obs['visible'] = [dict(x=10, y=0, z=0, crouch=False, parts=['chest'])]
        elif label == 'defuse':
            obs['bomb']['position'] = [0, 0, 0]
        elif label == 'plant':
            obs.update(side='attack', bomb=dict(state='carried'))
            obs['me']['carrying_bomb'] = True
        assert call(old.Candidate(), obs, 0) == call(new.Candidate(), obs, 0)
        cases.append(label + '_priority_or_action_preserved')

    obs = fixture()
    obs['bomb']['position'] = [20, 0, 0]
    assert not call(new.Candidate(), obs, 0)['walk']
    cases.append('known_bomb_travel_runs')
    obs = fixture()
    obs['map']['sites'] = obs['map']['sites'][:1]
    player = new.Candidate()
    for t in range(0, 306, 3):
        call(player, obs, t)
    assert player.retake_site == 0
    cases.append('single_site_safe_cycle')
    for map_id in ('borgo-r3-v54', 'dust2-cs2-v54', 'mirage-cs2-v54', 'molo-r3-v54', 'nevaio-r3-v54'):
        obs, player = fixture(), new.Candidate()
        obs['map_id'] = obs['map']['id'] = map_id
        for t in range(0, 153, 3):
            a = call(player, obs, t)
        assert player.goal == [20, 0, 0] and not a['walk']
        cases.append('search_routes_on_' + map_id)
    return cases


def equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
    return a == b


def replay(log, seat, old, new):
    reference, candidate = old.Candidate(), new.Candidate()
    digest, rows, raw_bytes = hashlib.sha256(), 0, 0
    faults, agreement, changes, guarded, combat = 0, 0, 0, 0, 0
    all_combat, map_id = 0, None
    rounds, footer, outcome = {}, None, None
    with log.open('rb') as source:
        header = json.loads(next(source))
        assert header['game_id'] == 'fps' and header['decision_encoding'] == 'original-jsonl-bytes'
        assert header['observation_semantics'] == 'recorded_policy_input'
        own = next(p for p in header['participants'] if p['seat'] == seat)
        assert own['source_hash'] == BASELINE_BUNDLE_SHA256
        for line in source:
            row = json.loads(line)
            if 'observation' not in row:
                if row.get('type') == 'export_complete':
                    footer = row
                elif row.get('type') == 'outcome':
                    outcome = row
                continue
            rows += 1
            raw_bytes += len(line)
            digest.update(line)
            assert row['seat'] == seat, 'This evaluator expects a single-seat export.'
            obs = row['observation']
            assert obs['seat'] == seat
            map_id = obs['map_id']
            a, b = reference.act(obs), candidate.act(obs)
            check_action(b)
            agreement += equal(a, row['action'])
            faults += bool(row['fault'])
            changed = not equal(a, b)
            changes += changed
            active = obs['phase'] == 'live' and obs['me']['alive']
            if active and obs['visible'] and not obs['me']['blind_ticks']:
                assert not changed, 'Combat priority changed'
                all_combat += 1
            retake = active and obs['side'] == 'defend' and obs['bomb']['state'] == 'planted'
            if not retake:
                assert not changed, (obs['round_index'], obs['match_tick'], 'change outside retake')
                guarded += 1
            elif obs['visible'] and not obs['me']['blind_ticks']:
                assert not changed, 'Combat priority changed'
                combat += 1
            r = rounds.setdefault(obs['round_index'], dict(side=obs['side'], alive_live_calls=0,
                    planted_unknown_alive_calls=0, requested_uses=Counter(),
                    candidate_changes=0, candidate_scan_calls=0, candidate_target_switches=[]))
            r['candidate_changes'] += changed
            if not active:
                continue
            r['alive_live_calls'] += 1
            if 'use' in a:
                r['requested_uses'][a['use']] += 1
            if retake and not obs['bomb'].get('position'):
                r['planted_unknown_alive_calls'] += 1
                tick = obs['match_tick']
                r.setdefault('unknown_first_tick', tick)
                r['unknown_last_tick'] = tick
                r['reference_assigned_site'] = reference.sites[(obs['round_index'] + seat % 2) % len(reference.sites)]['id']
                r['reference_last_goal_distance_m'] = round(math.dist(
                    [obs['me'][k] for k in ('x', 'y', 'z')], reference.goal), 4)
                site = candidate.retake_site
                if site is not None:
                    site_id = candidate.sites[site]['id']
                    previous = r['candidate_target_switches']
                    if not previous or previous[-1]['site'] != site_id:
                        previous.append(dict(tick=tick, site=site_id))
                r['candidate_scan_calls'] += candidate.scan_tick == tick
    assert footer and footer['complete'] and outcome
    assert (rows, raw_bytes, digest.hexdigest()) == (footer['rows'], footer['raw_bytes'], footer['sha256'])
    assert footer['seat_calls'] == {str(seat): rows}
    assert agreement == rows, 'Frozen source does not reproduce the recorded requested actions.'
    return dict(match_id=header['job_id'], map_id=map_id, rules_version=header['rules_version'],
                own_seat_zero_based=seat, own_team_zero_based=own['team'],
                platform_version_id=own['version_id'], bundle_sha256=own['source_hash'],
                integrity=dict(complete=True, decision_rows=rows, raw_decision_bytes=raw_bytes,
                               decision_bytes_sha256=digest.hexdigest(), seat_calls=footer['seat_calls']),
                observed=dict(faults=faults, round_wins_team_order=outcome['round_wins'],
                              own_team_round_score=f"{outcome['round_wins'][own['team']]}:{outcome['round_wins'][1-own['team']]}",
                              own_requested_plant_or_defuse=sum(sum(r['requested_uses'].values()) for r in rounds.values())),
                same_observation_comparison=dict(reference_action_agreement=agreement,
                    candidate_changed_actions=changes, unchanged_outside_retake=guarded,
                    unchanged_retake_combat_calls=combat, unchanged_total_combat_calls=all_combat,
                    scope='Recorded legal observations only; new trajectories, visibility, collisions and match outcomes are NOT simulated.'),
                rounds=rounds)


def public_diagnosis(path, result):
    seat = result['own_seat_zero_based']
    kills, deaths, damage = 0, 0, 0
    with path.open(encoding='utf-8') as source:
        for line in source:
            e = json.loads(line)
            r = result['rounds'].get(e.get('round_index'))
            if e['type'] == 'kill':
                kills += e.get('killer') == seat
                deaths += e.get('target') == seat
            elif e['type'] == 'hit' and e.get('player') == seat:
                damage += e.get('hp_damage', 0)
            elif r is not None and e['type'] == 'plant_complete_detail':
                r['public_replay_planted_site'] = e['site']
            elif r is not None and e['type'] == 'round_end':
                r['winner_team_zero_based'] = e['round_winner']
                r['end_reason'] = e['reason']
                r['end_tick'] = e['match_tick']
    misses = []
    for i, r in result['rounds'].items():
        if (r.get('planted_unknown_alive_calls') == 800 and r.get('end_reason') == 'bomb_explode'
                and r.get('unknown_last_tick') == r.get('end_tick', 0) - 3
                and r.get('reference_assigned_site') != r.get('public_replay_planted_site')):
            misses.append(i)
    result['observed'].update(kills=kills, deaths=deaths, hp_damage=round(damage, 3),
                            wrong_site_full_fuse_alive_round_indices=misses)
    result['public_diagnosis_note'] = 'Omniscient events explain actual planted sites after policy comparison; neither policy receives them.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--seat', type=int, default=8)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old, new = policies()
    checks = synthetic_checks(old, new)
    result = replay(args.log, args.seat, old, new)
    public_diagnosis(args.events, result)
    result.update(schema='fps-retake-log-analysis-v1', reference_commit=BASELINE,
                  candidate_agent_lf_sha256=hashlib.sha256((ROOT / 'strategies/fps/agent.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                  synthetic_guard_cases=checks, candidate_platform_validation='not_run',
                  candidate_activation='not_run', candidate_real_matches=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('integrity', 'observed', 'same_observation_comparison')}, indent=2))
    print('Synthetic guards:', len(checks))


if __name__ == '__main__':
    main()
