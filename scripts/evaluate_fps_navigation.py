"""Compare the R08 detour optimisation and inspect a downloaded FPS kit.

Uses synthetic graphs/observations and public geometry, never match outcomes.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '6b549f690f47db3d8317e9bed67a226a6b13fc7a'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference_file(ref, name):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{ref}:{name}'])


def load_agent(name, navigation, policy_ref=None):
    saved = {key: sys.modules.get(key) for key in ('waypoint_nav', 'starter_player')}
    try:
        sys.modules['waypoint_nav'] = navigation
        if policy_ref is None:
            sys.modules['starter_player'] = load_module(name + '_starter', ROOT / 'strategies/fps/starter_player.py')
            return load_module(name, ROOT / 'strategies/fps/agent.py')
        starter = types.ModuleType(name + '_starter')
        exec(compile(reference_file(policy_ref, 'strategies/fps/starter_player.py'), '<frozen-starter>', 'exec'), starter.__dict__)
        sys.modules['starter_player'] = starter
        agent = types.ModuleType(name)
        exec(compile(reference_file(policy_ref, 'strategies/fps/agent.py'), '<frozen-control-policy>', 'exec'), agent.__dict__)
        return agent
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def probe(module, nodes, edges, long_avoid=False):
    player = module.NavigationMixin()
    player.nodes, player.edges = nodes, edges
    player._node_at = {tuple(point): node for node, point in nodes.items()}
    player.long_avoid = long_avoid
    return player


def same_detour(reference, candidate, nodes, edges, pos, goal, target,
                tick=100, anchors=None, failed_edges=None, long_avoid=False):
    players = [probe(m, nodes, edges, long_avoid) for m in (reference, candidate)]
    routes = []
    for player in players:
        player._failed_anchors = dict(anchors or {})
        player._failed_edges = dict(failed_edges or {})
        routes.append(player._detour(pos, goal, target, tick))
    assert routes[0] == routes[1], (pos, goal, target, routes)
    assert players[0]._failed_anchors == players[1]._failed_anchors
    assert players[0]._failed_edges == players[1]._failed_edges
    return routes[0]


def regression_checks(reference, candidate):
    # Equal distances must retain insertion order, not numerical node ID order.
    nodes = {9: [-1, 0, 0], 2: [1, 0, 0], 5: [0, 4, 0]}
    edges = {9: [5], 2: [5], 5: []}
    route = same_detour(reference, candidate, nodes, edges, [0, 0, 0], nodes[5], [20, 20, 0])
    assert route[0] == nodes[9]
    cases = ['stable_equal_distance_tie']
    for label, nodes, pos, target, anchors, expected in [
        ('radius_inclusive', {0: [5, 0, 0], 1: [12, 0, 0]}, [0, 0, 0], [99, 0, 0], {}, 0),
        ('radius_exclusive', {0: [5.00001, 0, 0]}, [0, 0, 0], [99, 0, 0], {}, None),
        ('height_inclusive', {0: [0, 0, 1.25]}, [0, 0, 0], [99, 0, 0], {}, 0),
        ('height_exclusive', {0: [0, 0, 1.25001]}, [0, 0, 0], [99, 0, 0], {}, None),
        ('blocked_nearest', {0: [0, 0, 0], 1: [1, 0, 0]}, [0, 0, 0], [0, 0, 0], {}, 1),
        ('expired_blacklist', {0: [0, 0, 0], 1: [1, 0, 0]}, [0, 0, 0], [99, 0, 0], {0: 100}, 0),
        ('active_blacklist', {0: [0, 0, 0], 1: [1, 0, 0]}, [0, 0, 0], [99, 0, 0], {0: 101}, 1),
    ]:
        goal = nodes[expected] if expected is not None else list(nodes.values())[-1]
        route = same_detour(reference, candidate, nodes, {k: [] for k in nodes}, pos,
                            goal, target, anchors=anchors)
        assert (route[0] == nodes[expected]) if expected is not None else not route
        cases.append(label)
    for module in (reference, candidate):
        assert probe(module, {}, {})._detour([0, 0, 0], [1, 0, 0], [2, 0, 0], 100) == []
    cases.append('empty_graph')
    rng = random.Random(7608)
    random_cases = 500
    for i in range(random_cases):
        order = list(range(30))
        rng.shuffle(order)
        nodes = {key: [rng.randrange(-5, 6), rng.randrange(-5, 6), rng.choice([0, 1.25, 2.5])]
                 for key in order}
        edges = {key: rng.sample(order, 4) for key in order}
        pos = [rng.uniform(-2, 2), rng.uniform(-2, 2), rng.choice([0, 1.25])]
        same_detour(reference, candidate, nodes, edges, pos, nodes[order[-1]],
                    nodes[order[0]] if i % 2 else [99, 99, 0],
                    anchors={order[2]: 99, order[3]: 101},
                    failed_edges={(order[4], order[5]): 101}, long_avoid=bool(i % 2))
    return dict(boundary_cases=cases, randomized_graph_cases=random_cases,
                seed=7608, exact_route_and_cache_equivalence=True)


def check_action(action):
    for key in ('move_forward', 'move_right', 'yaw_rate', 'pitch_rate'):
        if key in action:
            value = action[key]
            assert type(value) in (int, float) and math.isfinite(value) and -1 <= value <= 1
    json.dumps(action, allow_nan=False)


def action_checks(reference_agent, candidate_agent):
    count = 0
    for map_id in ('dust2-cs2-v54', 'mirage-cs2-v54', 'borgo-r3-v54'):
        for side in ('attack', 'defend'):
            for seat in (0, 5):
                players = [m.Candidate() for m in (reference_agent, candidate_agent)]
                row = dict(id=map_id, sites=[dict(id='B', center=[0, 10, 0], radius=4)],
                           nav=dict(nodes=[dict(id=0, position=[0, 0, 0]),
                                           dict(id=1, position=[1, 0, 0]),
                                           dict(id=2, position=[0, 10, 0])],
                                    edges=[dict(**{'from': 0, 'to': 2, 'kind': 'walk'}),
                                           dict(**{'from': 1, 'to': 2, 'kind': 'walk'})]))
                for i in range(200):
                    phase = 'buy' if i < 2 else 'live'
                    obs = dict(map_id=map_id, round_index=0, seat=seat, side=side,
                               match_tick=i * 3, round_tick=360 + i * 3, phase=phase,
                               teammates=[], visible=[], bomb=dict(state='carried'),
                               me=dict(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0,
                                       alive=True, grounded=True, crouch=False,
                                       blind_ticks=0, cash=800, armor=0, has_kit=False,
                                       carrying_bomb=False,
                                       weapons=[None, dict(ammo=12, reserve=24, reload_until=0), None]))
                    if i == 0:
                        obs['map'] = row
                    if i in (80, 81):
                        obs['visible'] = [dict(x=10, y=0, z=0, crouch=False, parts=['chest'])]
                    if i in (120, 121):
                        obs.update(side='defend', bomb=dict(state='planted', position=[0, 0, 0]))
                    if i == 150:
                        obs['round_index'] = 1
                    actions = [p.act(copy.deepcopy(obs)) for p in players]
                    assert actions[0] == actions[1], (map_id, side, seat, i, actions)
                    check_action(actions[1])
                    count += 1
    return dict(synthetic_callbacks=count, exact_action_equivalence=True,
                scope='Small synthetic maps, stationary recovery, combat/defuse interruptions and round changes.')


def audit_and_benchmark(kit, reference, candidate):
    import numpy as np

    reader = load_module('fps_kit_reader', kit / 'tools/read_map.py')
    search = load_module('fps_kit_search', kit / 'sim/optimize.py')
    archive = kit.with_suffix('.zip')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name in z.namelist():
            assert z.read(name) == (kit / name).read_bytes(), name
    maps = []
    for path in sorted((kit / 'data').glob('*.mapdata.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert reader.load(path.with_suffix('.npz')) == data
        with np.load(path.with_suffix('.npz'), allow_pickle=False) as arrays:
            assert all(arrays[key].dtype.kind in 'biuf' for key in arrays.files)
        grid = data['walkable_surface_candidates']
        routes = []
        for zone in data['spawn_zones']:
            for site in data['bombsites']:
                route = search.astar(grid['cells'], grid['origin_xy'], grid['cell_size_m'],
                                     zone['pads'][0]['position'], site['center'])
                routes.append(dict(side=zone['side'], site=site['id'],
                                   found_in_xy_approximation=bool(route), grid_points=len(route)))
        # Preserve every floor candidate, including stacked XY coordinates.
        nodes = {i: [grid['origin_xy'][0] + (x + .5) * grid['cell_size_m'],
                     grid['origin_xy'][1] + (y + .5) * grid['cell_size_m'], z]
                 for i, (x, y, z) in enumerate(grid['cells'])}
        # Empty adjacency deliberately isolates anchor scanning from path search.
        # These are NOT the actual observation's navigation nodes or edges.
        edges = {key: [] for key in nodes}
        pos = data['spawn_zones'][0]['pads'][0]['position']
        goal = data['bombsites'][0]['center']
        target = nodes[min(nodes, key=lambda key: math.dist(pos, nodes[key]))]
        same_detour(reference, candidate, nodes, edges, pos, goal, target)
        timings = {'reference': [], 'candidate': []}
        batch = 20
        repeats = 7
        for repeat in range(repeats):
            order = [('reference', reference), ('candidate', candidate)]
            if repeat % 2:
                order.reverse()
            for label, module in order:
                player = probe(module, nodes, edges)
                started = time.perf_counter_ns()
                for _ in range(batch):
                    player._failed_edges, player._failed_anchors = {}, {}
                    player._detour(pos, goal, target, 100)
                timings[label].append((time.perf_counter_ns() - started) / batch / 1e6)
        medians = {key: statistics.median(value) for key, value in timings.items()}
        maps.append(dict(map_id=data['map_id'], json_npz_equal=True,
                         floor_candidate_count=len(grid['cells']),
                         duplicate_xy_count=len(grid['cells']) - len({(x, y) for x, y, z in grid['cells']}),
                         routes=routes, anchor_scan_wall_ms=medians,
                         anchor_scan_samples_wall_ms=timings,
                         candidate_change_pct=(medians['candidate'] / medians['reference'] - 1) * 100))
    assert len(maps) == 5
    return dict(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                maps=maps, benchmark_batch=20, benchmark_repeats=7,
                benchmark_scope='Public floor points with EMPTY synthetic adjacency; wall time of anchor scanning, not real routes or matches.',
                calibrated_simulation='not_run_no_own_training_log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit-root', type=Path, default=ROOT / 'agx-fps-kit')
    parser.add_argument('--baseline-ref', default=BASELINE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    reference_source = reference_file(args.baseline_ref, 'strategies/fps/waypoint_nav.py')
    reference = types.ModuleType('fps_reference_navigation')
    exec(compile(reference_source, '<git-baseline-waypoint_nav>', 'exec'), reference.__dict__)
    candidate_path = ROOT / 'strategies/fps/waypoint_nav.py'
    candidate = load_module('fps_candidate_navigation', candidate_path)
    # Freeze the R08 controller on both sides to isolate the navigation helper.
    # Later objective changes have a separate evaluator and experiment record.
    result = dict(schema='fps-r08-anchor-scan-checks-v1', baseline_commit=args.baseline_ref,
                  candidate_navigation_lf_sha256=hashlib.sha256(candidate_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                  evidence_kind='Synthetic regression checks and public geometry audit; no match simulation.',
                  regression=regression_checks(reference, candidate),
                  actions=action_checks(load_agent('fps_reference_agent', reference, args.baseline_ref),
                                        load_agent('fps_candidate_agent', candidate, args.baseline_ref)),
                  control_policy_scope='Frozen R08 controller on both sides; does not assess later objective changes.',
                  kit=audit_and_benchmark(args.kit_root.resolve(), reference, candidate),
                  platform_validation='not_run', competition_activation='not_run', formal_matches=0)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
