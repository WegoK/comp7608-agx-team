"""Recover stalled movement on the game's public navigation graph."""
import heapq
import math


class NavigationMixin:
    nav_enabled = True

    def __init__(self):
        super().__init__()
        self.reset_navigation()

    def reset_navigation(self):
        self._nav_target = None
        self._window_point = None
        self._previous_tick = None
        self._failed_edges = {}
        self._failed_anchors = {}
        self._jump_tried = False
        self.recovery_count = 0

    def load_map(self, row):
        rebuild = self.map_id != row["id"] or not hasattr(self, "_node_at")
        super().load_map(row)
        if rebuild and self.nav_enabled:
            self._node_at = {tuple(p): node for node, p in self.nodes.items()}
            self.reset_navigation()

    def _reset_window(self, tick, point, pos):
        self._window_point = tuple(point)
        self._window_tick = tick
        self._best_distance = math.dist(pos, point)

    def _detour(self, pos, goal, target, tick):
        """Avoid the failed incoming link. Reattach on the same elevation when possible."""
        if not self.nodes:
            return []
        self._failed_edges = {k: end for k, end in self._failed_edges.items() if end > tick}
        self._failed_anchors = {k: end for k, end in self._failed_anchors.items() if end > tick}
        blocked = self._node_at.get(tuple(target))
        # We only need the nearest node and nearest eligible attachment, not a
        # sorted copy of the whole graph. Strict comparisons preserve the old
        # stable-sort tie order (the map's node insertion order).
        nearest = start = None
        nearest_distance = start_distance = math.inf
        for node, point in self.nodes.items():
            distance = math.dist(pos, point)
            if distance < nearest_distance:
                nearest, nearest_distance = node, distance
            # The current blocked target is blacklisted immediately below.
            if (node != blocked and node not in self._failed_anchors
                    and abs(pos[2] - point[2]) <= 1.25
                    and distance <= 5.0 and distance < start_distance):
                start, start_distance = node, distance
        if blocked is not None:
            if nearest != blocked:
                self._failed_edges[nearest, blocked] = tick + (7200 if getattr(self, "long_avoid", False) else 900)
            self._failed_anchors[blocked] = tick + (7200 if getattr(self, "long_avoid", False) else 900)
        # For an unreachable candidate segment, do not jump to another floor or
        # project ourselves across the map to satisfy the waypoint tolerance.
        if start is None:
            return []
        end = min(self.nodes, key=lambda k: math.dist(self.nodes[k], goal))
        cost = {start: 0.0}
        parent = {start: None}
        queue = [(math.dist(self.nodes[start], self.nodes[end]), start)]
        visited = set()
        while queue:
            _, node = heapq.heappop(queue)
            if node in visited:
                continue
            if node == end:
                break
            visited.add(node)
            for nxt in self.edges[node]:
                if (getattr(self, "long_avoid", False) and nxt in self._failed_anchors
                        and nxt != end):
                    continue
                if (node, nxt) in self._failed_edges or nxt in visited:
                    continue
                if nxt == blocked and node == start and start != end:
                    continue
                score = cost[node] + math.dist(self.nodes[node], self.nodes[nxt])
                if score < cost.get(nxt, math.inf):
                    cost[nxt] = score
                    parent[nxt] = node
                    heapq.heappush(queue, (score + math.dist(self.nodes[nxt], self.nodes[end]), nxt))
        if end not in parent:
            return []
        nodes = []
        cursor = end
        while cursor is not None:
            nodes.append(self.nodes[cursor])
            cursor = parent[cursor]
        return list(reversed(nodes)) + [list(goal)]

    def move(self, observation, goal, action):
        if not self.nav_enabled:
            return super().move(observation, goal, action)
        me = observation["me"]
        pos = (me["x"], me["y"], me["z"])
        tick = observation["match_tick"]
        task = (self.map_id, observation["round_index"], tuple(goal))
        if self._nav_target != task or self.goal is None:
            self.reset_navigation()
            self._nav_target = task
        # Keep normal starter steering; use progress toward its actual waypoint for recovery.
        self.stuck = 0
        result = super().move(observation, goal, action)
        point = self.path[0]
        interrupted = self._previous_tick is None or tick != self._previous_tick + 3
        self._previous_tick = tick
        if self._window_point != tuple(point) or interrupted:
            self._reset_window(tick, point, pos)
            self._jump_tried = False
        distance = math.dist(pos, point)
        if len(self.path) == 1 and distance <= 0.45:
            result.update(move_forward=0.0, move_right=0.0)
            self._reset_window(tick, point, pos)
            return result
        if self._best_distance - distance >= 0.3:
            self._reset_window(tick, point, pos)
            self._jump_tried = False
        if tick - self._window_tick < 90:
            return result
        self._reset_window(tick, point, pos)
        if not self._jump_tried and me["grounded"] and 0.25 < point[2] - pos[2] <= 1.8:
            self._jump_tried = True
            result["jump"] = True
            return result
        route = self._detour(pos, goal, point, tick)
        self.recovery_count += 1
        if route:
            self.path = route
            self._window_point = None
        return result
