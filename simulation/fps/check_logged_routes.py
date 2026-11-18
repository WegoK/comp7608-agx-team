"""Read one private FPS export; emit only aggregate navigation measurements."""
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from check_routes import load_player


def equivalent(action, recorded):
    if action.keys() != recorded.keys():
        return False
    return all(math.isclose(value, recorded[k], abs_tol=1e-8) if isinstance(value, float)
               else value == recorded[k] for k, value in action.items())


def analyse(export, baseline, candidate):
    raw_hash = hashlib.sha256()
    raw_bytes = 0
    seats = Counter()
    header = footer = map_id = None
    node_count = edge_count = 0
    baseline_events = []
    agree = 0
    intervals = []
    stalled = None
    previous = None
    old_route = baseline.route

    def tracked_route(start, goal):
        path = old_route(start, goal)
        nearest_start = min(baseline.nodes, key=lambda n: math.dist(start, baseline.nodes[n]))
        nearest_goal = min(baseline.nodes, key=lambda n: math.dist(goal, baseline.nodes[n]))
        paired = candidate.route(start, goal)
        cost = lambda r: sum(map(math.dist, [start] + r, r))
        baseline_events.append((nearest_start, nearest_goal, cost(path), cost(paired)))
        return path
    baseline.route = tracked_route

    def finish_stall():
        nonlocal stalled
        if stalled and stalled['last_tick'] - stalled['first_tick'] >= 60:
            intervals.append(stalled)
        stalled = None

    with export.open('rb') as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('type') == 'metadata':
                header = row
            if row.get('type') == 'export_complete':
                footer = row
            if 'observation' not in row:
                continue
            raw_hash.update(line)
            raw_bytes += len(line)
            o = row['observation']
            seat = o['seat']
            assert seat == row['seat']
            seats[seat] += 1
            if 'map' in o:
                candidate.load_map(o['map'])
                map_id = o['map_id']
                nav = o['map']['nav']
                node_count, edge_count = len(nav['nodes']), len(nav['edges'])
            # Replay v1 on the recorded observation then provide identical (start,goal)
            # inputs to both planners at each route request made by v1.
            expected = baseline.act(o)
            agree += equivalent(expected, row['action'])
            tick, rnd = o['match_tick'], o['round_index']
            me = o['me']
            pos = (me['x'], me['y'])
            if previous:
                prev_tick, prev_round, prev_live, prev_move, prev_pos = previous
                # The action at tick t explains displacement to tick t + 3.
                eligible = (rnd == prev_round and tick == prev_tick + 3 and prev_live
                            and me['alive'] and o['phase'] == 'live' and prev_move)
                if eligible:
                    if stalled is None:
                        stalled = {'anchor': prev_pos, 'round_index': rnd, 'first_tick': prev_tick,
                                   'last_tick': prev_tick, 'max_offset_m': 0.0}
                    offset = math.dist(pos, stalled['anchor'])
                    if offset <= 0.15:
                        stalled['last_tick'] = tick
                        stalled['max_offset_m'] = max(offset, stalled['max_offset_m'])
                    else:
                        finish_stall()
                else:
                    finish_stall()
            a = row['applied_action']
            moving = math.hypot(a.get('move_forward', 0), a.get('move_right', 0)) >= 0.1
            previous = (tick, rnd, me['alive'] and o['phase'] == 'live', moving, pos)
    finish_stall()
    assert header['completion_record'] == 'export_complete' and footer['complete']
    assert footer['rows'] == sum(seats.values()) and footer['raw_bytes'] == raw_bytes
    assert footer['sha256'] == raw_hash.hexdigest()
    assert {str(k): v for k, v in seats.items()} == footer['seat_calls']
    assert len(seats) == 1
    unique = {(a, b) for a, b, _, _ in baseline_events}
    reachable = [e for e in baseline_events if e[0] == e[1] or
                 len(candidate.route(candidate.nodes[e[0]], candidate.nodes[e[1]])) > 1]
    shorter = sum(e[3] < e[2] - 1e-7 for e in reachable)
    longer = sum(e[3] > e[2] + 1e-7 for e in reachable)
    longest = max(intervals, key=lambda e:e['last_tick'] - e['first_tick']) if intervals else None
    excerpt = None if longest is None else {k:v for k, v in longest.items() if k != 'anchor'}
    return {
        'scope': 'offline replay of the original policy on the recorded seat observations',
        'export_complete_and_decision_byte_hash_verified': True,
        'seat_zero_based': next(iter(seats)), 'decision_rows': sum(seats.values()),
        'baseline_actions_match_within_1e-8': agree,
        'map_id': map_id,'public_nav_nodes': node_count,'public_nav_edges': edge_count,
        'paired_baseline_route_requests': len(baseline_events), 'reachable_requests': len(reachable),
        'distinct_snapped_endpoint_pairs': len(unique),'shorter': shorter,
        'equal': len(reachable) - shorter - longer,'longer': longer,
        'baseline_requested_path_total_m': round(sum(e[2] for e in reachable), 3),
        'candidate_requested_path_total_m': round(sum(e[3] for e in reachable), 3),
        'stall_definition': 'alive/live, continuous applied movement magnitude >= 0.1, consecutive 3-tick samples all within 0.15 m XY of anchor for at least 60 ticks',
        'observed_stall_intervals_at_least_1s': len(intervals),
        'longest_observed_interval': excerpt,
        'tick_hz': 60,
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('export', type=Path)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    result = json.dumps(analyse(a.export, load_player(a.baseline), load_player(a.candidate)), indent=2)
    if a.out:
        a.out.write_text(result+'\n')
    print(result)
