"""Exercise each planner through its public Player.load_map and Player.route."""
import argparse
import importlib.util
import json
import math
import random
from pathlib import Path


def load_player(source):
    spec = importlib.util.spec_from_file_location("strategy", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Player()


def graph(points, edges):
    return {"sites": [], "nav": {
        "nodes": [{"id": idx, "position": p} for idx, p in enumerate(points)],
        "edges": [{"from": u, "to": v, "kind": k} for u, v, k in edges],
    }}


def length(path):
    return sum(map(math.dist, path, path[1:]))


def measured_path(player, start, goal):
    route = player.route(start, goal)
    return route, length([start] + route)


def shortest_distances(points, edges):
    # Floyd-Warshall is independent of both production implementations.
    size = len(points)
    dist = [[0 if a == b else math.inf for b in range(size)] for a in range(size)]
    for u, v, k in edges:
        if k == "ladder":
            continue
        dist[u][v] = math.dist(points[u], points[v])
        if k == "walk":
            dist[v][u] = dist[u][v]
    for via in range(size):
        for u in range(size):
            for v in range(size):
                dist[u][v] = min(dist[u][v], dist[u][via] + dist[via][v])
    return dist


def evaluate(baseline, candidate, reference=None):
    planners = [baseline, candidate] + ([reference] if reference else [])
    points = [[0, 0, 0], [5, 12, 0], [3, 0, 0], [6, 0, 0], [10, 0, 0]]
    edges = [(0, 1, "walk"), (1, 4, "walk"), (0, 2, "walk"),
             (2, 3, "walk"), (3, 4, "walk")]
    for p in planners:
        p.load_map(graph(points, edges))
    fork = [measured_path(p, points[0], points[4])[1] for p in planners]
    assert fork[:2] == [26.0, 10.0]
    if reference:
        assert fork[2] == 26.0
    for p in planners:
        p.load_map(graph([], []))
        assert p.route(points[0], points[4]) == [points[4]]

    counts = dict(reachable=0, unreachable=0, shorter=0, equal=0, longer=0)
    for seed in range(12):
        rng = random.Random(seed)
        size = 10
        points = [[i * 2, rng.randrange(-12, 13), rng.randrange(0, 5)] for i in range(size)]
        edges = []
        for u in range(size):
            for v in range(u + 1, size):
                if rng.random() < 0.28:
                    edges.append((u, v, rng.choice(("walk", "walk", "drop", "ladder"))))
        matrix = shortest_distances(points, edges)
        nodes = {tuple(point): idx for idx, point in enumerate(points)}
        legal = {(u, v) for u, v, kind in edges if kind != "ladder"}
        legal.update((v, u) for u, v, kind in edges if kind == "walk")
        for p in planners:
            p.load_map(graph(points, edges))
        for u, source in enumerate(points):
            for v, dest in enumerate(points):
                if u == v:
                    assert candidate.route(source, dest)[-1] == dest
                    continue
                paths = [measured_path(p, source, dest) for p in planners]
                if math.isinf(matrix[u][v]):
                    assert all(route == [dest] for route, _ in paths)
                    counts["unreachable"] += 1
                    continue
                counts["reachable"] += 1
                for route, _ in paths:
                    assert route[0] == source and route[-1] == dest
                    # route[-1] repeats the exact requested endpoint.
                    hops = [nodes[tuple(point)] for point in route[:-1]]
                    assert all(edge in legal for edge in zip(hops, hops[1:]))
                old_cost, new_cost = paths[0][1], paths[1][1]
                assert math.isclose(new_cost, matrix[u][v], abs_tol=1e-9)
                if reference:
                    assert math.isclose(old_cost, paths[2][1], abs_tol=1e-9)
                if math.isclose(new_cost, old_cost, abs_tol=1e-9):
                    counts["equal"] += 1
                elif new_cost < old_cost:
                    counts["shorter"] += 1
                else:
                    counts["longer"] += 1
    return {"scope": "synthetic graphs, route length only",
            "fork_length": dict(zip(["course_v1", "candidate", "official_kit"], fork)),
            "random_graph_seeds": list(range(12)), "nodes_per_graph": 10,
            "directed_pairs_excluding_self": sum(counts[k] for k in ("reachable", "unreachable")),
            "results": counts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--official-kit", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    data = evaluate(load_player(args.baseline), load_player(args.candidate),
                    load_player(args.official_kit) if args.official_kit else None)
    result = json.dumps(data, indent=2) + "\n"
    if args.out:
        args.out.write_text(result)
    print(result, end="")
