"""Compare the route() methods on matched waypoint pairs in AGX FPS maps."""
import argparse
from collections import deque
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import statistics
import time


def load_policy(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Player()


def grid_graph(data):
    # Collapse the candidate layers to one height per XY. The graph is an
    # explicit test fixture, not the platform's navigation graph.
    grid = data['walkable_surface_candidates']
    spacing = grid['cell_size_m']
    ox, oy = grid['origin_xy']
    heights = {}
    for i, j, z in grid['cells']:
        heights[i, j] = min(z, heights.get((i, j), z))
    nodes = {f'{i},{j}': [ox + (i + .5) * spacing, oy + (j + .5) * spacing, z]
             for (i, j), z in sorted(heights.items())}
    edges = []

    def near(a, b):
        return b in heights and abs(heights[a] - heights[b]) <= 0.5

    for i, j in sorted(heights):
        for di, dj in ((1, 0), (0, 1), (1, -1), (1, 1)):
            a, b = (i, j), (i + di, j + dj)
            if not near(a, b):
                continue
            # Diagonals may not cut across a missing corner.
            if di and dj:
                corners = ((i + di, j), (i, j + dj))
                if not all(near(a, c) and near(b, c) for c in corners):
                    continue
            edges.append({'from': f'{i},{j}', 'to': f'{b[0]},{b[1]}', 'kind': 'walk'})
    return {'nodes': [{'id': k, 'position': v} for k, v in nodes.items()], 'edges': edges}


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reachable_pairs(reference, n, rng):
    keys = sorted(reference.nodes)
    sent = set()
    while len(sent) < n:
        origin = rng.choice(keys)
        todo = deque([origin])
        reachable = {origin}
        while todo:
            node = todo.popleft()
            for nxt in reference.edges[node]:
                if nxt not in reachable:
                    todo.append(nxt)
                    reachable.add(nxt)
        alternatives = sorted(k for k in reachable if k != origin and
                              math.dist(reference.nodes[origin], reference.nodes[k]) >= 15)
        if alternatives:
            dest = rng.choice(alternatives)
            if (origin, dest) not in sent:
                sent.add((origin, dest))
                yield origin, dest


def measure(policy, origin, dest, lookup):
    a, b = policy.nodes[origin], policy.nodes[dest]
    t = time.perf_counter_ns()
    route = policy.route(a, b)
    ms = (time.perf_counter_ns() - t) / 1e6
    seq = [a] + route
    if not route or route[-1] != b:
        raise ValueError('route misses goal')
    meters = 0.
    for p, q in zip(seq, seq[1:]):
        if p == q:
            continue
        left, right = lookup[tuple(p)], lookup[tuple(q)]
        if right not in policy.edges[left]:
            raise ValueError('route crosses a non-edge')
        meters += math.dist(p, q)
    return meters, ms


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--maps', required=True, type=Path)
    p.add_argument('--replay-static', action='store_true', help='Use original nav nodes and edges from recording static maps')
    p.add_argument('--baseline', required=True, type=Path)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--pairs', type=int, default=24)
    p.add_argument('--seed', type=int, default=1811)
    args = p.parse_args()
    rows, groups = [], []
    a = load_policy(args.baseline, 'baseline')
    b = load_policy(args.candidate, 'candidate')
    pattern = 'static_*.json' if args.replay_static else 'fps-*.mapdata.json'
    for file in sorted(args.maps.glob(pattern)):
        data = json.loads(file.read_text())
        if args.replay_static:
            mapobs = data['map']
            nav = mapobs['nav']
            map_id = mapobs['id']
        else:
            nav = grid_graph(data)
            mapobs = {'nav': nav, 'sites': data['bombsites']}
            map_id = data['map_id']
        a.load_map(mapobs)
        b.load_map(mapobs)
        lookup = {tuple(v): k for k, v in a.nodes.items()}
        rng = random.Random(f'{args.seed}-{map_id}')
        pairs = reachable_pairs(a, args.pairs, rng)
        group = []
        for origin, dest in pairs:
            am, at = measure(a, origin, dest, lookup)
            bm, bt = measure(b, origin, dest, lookup)
            group.append(dict(map=map_id, origin=origin, goal=dest,
                              baseline_m=am, candidate_m=bm,
                              baseline_ms=at, candidate_ms=bt))
        rows.extend(group)
        ba = sum(r['baseline_m'] for r in group)
        ca = sum(r['candidate_m'] for r in group)
        groups.append(dict(map=map_id, map_sha256=checksum(file), pairs=len(group),
                           nodes=len(nav['nodes']), edges=len(nav['edges']),
                           baseline_total_m=ba, candidate_total_m=ca,
                           pct_shorter=100*(ba-ca)/ba,
                           shorter=sum(r['candidate_m'] < r['baseline_m']-1e-7 for r in group),
                           longer=sum(r['candidate_m'] > r['baseline_m']+1e-7 for r in group)))
    if not rows:
        raise SystemExit('No input maps matched')
    args.out.mkdir(exist_ok=True, parents=True)
    with (args.out/'route_pairs.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
    baseline_dist = sum(r['baseline_m'] for r in rows)
    candidate_dist = sum(r['candidate_m'] for r in rows)
    summary = dict(kind='public_replay_nav_graph' if args.replay_static else 'approximate_grid_route_microbenchmark', seed=args.seed,
                   baseline_sha256=checksum(args.baseline),candidate_sha256=checksum(args.candidate),
                   pairs=len(rows), baseline_total_m=baseline_dist, candidate_total_m=candidate_dist,
                   pct_shorter=100*(baseline_dist-candidate_dist)/baseline_dist,
                   baseline_median_ms=statistics.median(r['baseline_ms'] for r in rows),
                   candidate_median_ms=statistics.median(r['candidate_ms'] for r in rows),
                   by_map=groups)
    (args.out/'route_summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
