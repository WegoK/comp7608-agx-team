"""Measure and compare bounded, loop-free paths on the simulator's map graph."""

import heapq
import itertools
import math


def metrics(points):
    """Return 3-D length and count of horizontal direction changes ≥45°."""
    segments = []
    length = 0.0
    for start, end in zip(points, points[1:]):
        length += math.dist(start, end)
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy)
        if norm > 1e-7:
            segments.append((dx / norm, dy / norm))

    threshold = math.cos(math.radians(45)) + 1e-12
    turns = sum(
        x * u + y * v <= threshold
        for (x, y), (u, v) in zip(segments, segments[1:])
    )
    return length, turns


def options(nodes, edges, first, last):
    """Return (turns, length, node IDs) candidates and whether work remained."""
    reverse = {node: [] for node in nodes}
    for node, neighbors in edges.items():
        for neighbor in neighbors:
            reverse[neighbor].append((node, math.dist(nodes[neighbor], nodes[node])))

    # Dijkstra on reversed edges gives the exact remaining distance on the graph.
    remaining = {last: 0.0}
    queue = [(0.0, last)]
    while queue:
        distance, node = heapq.heappop(queue)
        if remaining[node] != distance:
            continue
        for previous, weight in reverse[node]:
            cost = distance + weight
            if cost < remaining.get(previous, math.inf):
                remaining[previous] = cost
                heapq.heappush(queue, (cost, previous))

    if first not in remaining:
        return [], False
    cap = remaining[first] * 1.2 + 1e-7
    # Equal lower bounds are ordered by insertion; retain map neighbor order.
    sequence = itertools.count()
    frontier = [(remaining[first], next(sequence), 0.0, (first,))]
    paths = []
    expansions = 0
    while frontier and len(paths) < 48 and expansions < 4096:
        _, _, cost, path = heapq.heappop(frontier)
        node = path[-1]
        expansions += 1
        if node == last:
            points = [nodes[n] for n in path]
            paths.append((metrics(points)[1], cost, path))
            continue
        for neighbor in dict.fromkeys(edges[node]):
            if neighbor in path:
                continue
            next_cost = cost + math.dist(nodes[node], nodes[neighbor])
            lower_bound = next_cost + remaining.get(neighbor, math.inf)
            if lower_bound <= cap:
                heapq.heappush(
                    frontier,
                    (lower_bound, next(sequence), next_cost, path + (neighbor,)),
                )
    return paths, bool(frontier)


def select_routes(nodes, edges, first, last):
    # Cache only the two selected paths, regardless of the number enumerated.
    choices, _ = options(nodes, edges, first, last)
    if not choices:
        return None
    low = min(turns for turns, _, _ in choices)
    high = min(low + 2, max(turns for turns, _, _ in choices))
    return tuple(
        min(choices, key=lambda path: (abs(path[0] - target), path[1], path[2]))[2]
        for target in (low, high)
    )
