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


class Experiment:
    """Wrap one simulated player's route function and record each planning request."""

    def __init__(self, player, arm):
        self.player = player
        self.arm = arm
        self.original = player.route
        self.cache = {}
        self.round = None
        self.requests = []
        player.route = self.route

    def route(self, start, goal):
        player = self.player
        chosen = None
        choices = []
        truncated = False
        mode = self.arm
        if mode == 'mixed':
            mode = 'straight' if self.side == 'attack' else 'bends'

        if player.nodes:
            first = min(player.nodes, key=lambda k: math.dist(start, player.nodes[k]))
            last = min(player.nodes, key=lambda k: math.dist(goal, player.nodes[k]))
            key = first, last
            if key not in self.cache:
                self.cache[key] = options(player.nodes, player.edges, first, last)
            choices, truncated = self.cache[key]
            if choices and mode != 'v2':
                low = min(turns for turns, _, _ in choices)
                high = min(low + 2, max(turns for turns, _, _ in choices))
                target = low if mode == 'straight' else high
                _, _, node_ids = min(
                    choices, key=lambda c: (abs(c[0] - target), c[1], c[2])
                )
                chosen = [player.nodes[n] for n in node_ids] + [goal]

        if chosen is None:
            chosen = self.original(start, goal)
        length, turns = metrics([start, *chosen])
        self.requests.append(dict(
            round=self.round,
            side=self.side,
            plan_length=length,
            plan_turns=turns,
            choices=len(choices),
            bend_choice=len({choice[0] for choice in choices}) > 1,
            truncated=truncated,
        ))
        return chosen
