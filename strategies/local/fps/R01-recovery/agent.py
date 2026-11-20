"""Rule-based player: public map, combat and bomb objectives."""
import math
from collections import deque


def clip(value):
    return max(-1.0, min(1.0, value))


def distance(a, b):
    return math.sqrt(sum((x -y)**2 for x, y in zip(a, b)))


def angle(value):
    return (value + math.pi) % (2 * math.pi) -math.pi


class Player:
    def __init__(self):
        self.nodes, self.edges, self.sites = {}, {}, []
        self.path, self.goal = [], None
        self.round = None
        self.reset_movement()

    def load_map(self, row):
        self.boxes = row.get("boxes", ())
        self.sites = row["sites"]
        self.nodes = {n["id"]: n["position"]
                      for n in row["nav"]["nodes"]}
        self.edges = {key: [] for key in self.nodes}
        for edge in row["nav"]["edges"]:
            if edge["kind"] != "ladder":
                self.edges[edge["from"]].append(edge["to"])
                if edge["kind"] == "walk":
                    self.edges[edge["to"]].append(edge["from"])

    def route(self, start, goal):
        if not self.nodes:
            return [goal]
        first = min(self.nodes,
                    key=lambda k: distance(start, self.nodes[k]))
        last = min(self.nodes,
                   key=lambda k: distance(goal, self.nodes[k]))
        queue, parent = deque([first]), {first: None}
        while queue and last not in parent:
            here = queue.popleft()
            for there in self.edges[here]:
                if there not in parent:
                    parent[there] = here
                    queue.append(there)
        if last not in parent:
            return [goal]
        path, cursor = [goal], last
        while cursor is not None:
            path.append(self.nodes[cursor])
            cursor = parent[cursor]
        return list(reversed(path))

    def buy(self, o):
        m = o["me"]
        if (o["side"] == "defend" and not m["has_kit"]
                and m["cash"] >= 400):
            return {"buy": "defusekit"}
        rifle = "ak47" if o["side"] == "attack" else "m4a1"
        price = 2700 if o["side"] == "attack" else 2900
        if m["weapons"][0] is None and m["cash"] >= price:
            return {"buy": rifle}
        if m["armor"] < 50 and m["cash"] >= 650:
            return {"buy": "armor"}
        return {}

    def combat(self, o, action, gun):
        m = o["me"]
        enemy = min(o["visible"], key=lambda e:
                    (e["x"]-m["x"])**2 + (e["y"]-m["y"])**2)
        dx, dy = enemy["x"]-m["x"], enemy["y"]-m["y"]
        heights = {"head": 1.64, "chest": 1.18, "abdomen": 0.88}
        part = next((p for p in ("chest", "head", "abdomen")
                     if p in enemy["parts"]), "chest")
        height = heights[part] * (0.6 if enemy["crouch"] else 1.0)
        eye = 0.95 if m["crouch"] else 1.62
        dz = enemy["z"] + height -m["z"] -eye
        punch = gun.get("aim_punch", {}) if gun else {}
        yaw = angle(math.atan2(-dy, dx) -m["yaw"]
                    -2 * punch.get("yaw", 0.0))
        pitch = math.atan2(dz, max(0.1, math.hypot(dx, dy)))
        pitch -= m["pitch"] + 2 * punch.get("pitch", 0.0)
        ready = bool(gun and gun["ammo"]
                     and not gun["reload_until"])
        # Release the trigger between pistol shots.
        trigger = (action["weapon_slot"] == 0
                   or (o["match_tick"] // 3) % 4 == 0)
        action.update(yaw_rate=clip(yaw / 0.15),
                      pitch_rate=clip(pitch / 0.1), crouch=True,
                      fire=ready and trigger and abs(yaw) < 0.04
                      and abs(pitch) < 0.04)
        return action

    def reset_movement(self):
        self.progress_anchor = None
        self.waypoint = None
        self.recovery_start = None
        self.recovery_count = 0
        self.last_move_tick = None

    def clear_space(self, pos, direction, reach=1.2):
        """Approximate available distance using the map's public solid boxes."""
        limit = reach
        for box in getattr(self, "boxes", ()):
            if box["z"] + box["h"] <= pos[2] + 0.45 or box["z"] > pos[2] + 1.7:
                continue
            # Intersect the short movement segment with an expanded rectangle.
            near, far = 0.0, reach
            for center, width, p, d in zip(
                    (box["x"], box["y"]), (box["w"], box["d"]), pos, direction):
                low, high = center - width / 2 - 0.28, center + width / 2 + 0.28
                if abs(d) < 1e-8:
                    if not low <= p <= high:
                        break
                else:
                    left, right = (low-p)/d, (high-p)/d
                    near, far = max(near, min(left, right)), min(far, max(left, right))
                if far < near:
                    break
            else:
                limit = min(limit, near)
        return limit

    def move(self, o, goal, action):
        me = o["me"]
        pos = [me["x"], me["y"], me["z"]]
        tick = o["match_tick"]
        # The final node has no successor. Stop here until the goal moves.
        if distance(pos, goal) <= 0.5:
            self.reset_movement()
            self.path, self.goal = [], None
            return action
        if self.goal != goal or not self.path:
            self.path, self.goal = self.route(pos, goal), list(goal)
        while len(self.path) > 1 and distance(pos, self.path[0]) < 1.0:
            self.path.pop(0)
        point = self.path[0]
        if (self.waypoint != point or self.last_move_tick is None
                or tick != self.last_move_tick + 3):
            self.reset_movement()
            self.waypoint = point
            self.progress_anchor, self.anchor_tick = pos, tick
        self.last_move_tick = tick
        dx, dy = point[0]-pos[0], point[1]-pos[1]
        norm = math.hypot(dx, dy)
        fx, fy = (dx/norm, dy/norm) if norm > 1e-6 else (math.cos(me["yaw"]), -math.sin(me["yaw"]))
        heading = angle(math.atan2(-fy, fx) - me["yaw"])
        action.update(yaw_rate=clip(heading / 0.15),
                      pitch_rate=clip(-me["pitch"] / 0.1), walk=True)

        if self.recovery_start is None:
            if distance(pos, self.progress_anchor) >= 0.25:
                self.progress_anchor, self.anchor_tick = pos, tick
            elif tick - self.anchor_tick >= 45:
                wall = self.clear_space(pos, (fx, fy)) < 1.0
                # Teammate observations give a zone, not a body position.
                # In open space wait briefly for possible traffic to clear.
                waiting = not wall and any(t["alive"] for t in o["teammates"])
                if waiting and tick - self.anchor_tick < 69:
                    return action
                self.recovery_start = tick
                self.recovery_forward = fx, fy
                right = (-fy, fx)
                left_space = self.clear_space(pos, (-right[0], -right[1]))
                right_space = self.clear_space(pos, right)
                choose_right = right_space > left_space if abs(left_space-right_space) > 0.1 else self.recovery_count % 2 == 0
                self.recovery_side = 1 if choose_right else -1
                self.recovery_count += 1

        if self.recovery_start is not None:
            elapsed = tick - self.recovery_start
            fx, fy = self.recovery_forward
            if elapsed < 21:
                fx, fy = -fx, -fy
            elif elapsed < 63:
                fx, fy = -fy*self.recovery_side, fx*self.recovery_side
            else:
                self.recovery_start = None
                self.progress_anchor, self.anchor_tick = pos, tick
                if self.recovery_count % 2 == 0:
                    self.path = []
        # Express a fixed world direction in the agent's local coordinates.
        yaw = me["yaw"]
        action["move_forward"] = 0.75 * (fx*math.cos(yaw) - fy*math.sin(yaw))
        action["move_right"] = 0.75 * (fx*math.sin(yaw) + fy*math.cos(yaw))
        return action

    def act(self, o):
        if "map" in o:
            self.load_map(o["map"])
        if self.round != o["round_index"]:
            self.round = o["round_index"]
            self.path, self.goal = [], None
            self.reset_movement()
        m = o["me"]
        if not m["alive"] or o["phase"] not in ("buy", "live"):
            return {}
        if o["phase"] == "buy":
            return self.buy(o)
        slot = 0 if m["weapons"][0] is not None else 1
        gun = m["weapons"][slot]
        action = {"weapon_slot": slot}
        if gun and gun["ammo"] == 0 and gun["reserve"]:
            action["reload"] = True
        pos = [m["x"], m["y"], m["z"]]
        bomb = o["bomb"]
        index = (o["round_index"] + o["seat"] % 2) % len(self.sites)
        site = self.sites[index]
        goal = list(site["center"])
        planted = bomb["state"] == "planted"
        if planted and bomb.get("position"):
            goal = list(bomb["position"])
        if (o["side"] == "defend" and planted
                and bomb.get("position")
                and distance(pos, goal) < 2.0):
            return dict(action, use="defuse")
        radius = site["radius"] * 0.5
        at_site = distance(pos, site["center"]) < radius
        if m["carrying_bomb"] and at_site:
            return dict(action, use="plant")
        if o["visible"] and not m["blind_ticks"]:
            return self.combat(o, action, gun)
        if (o["side"] == "attack" and planted
                and distance(pos, goal) < 8):
            return dict(action, yaw_rate=0.15, walk=True)
        return self.move(o, goal, action)


_player = Player()


def init(context):
    global _player
    _player = Player()


def act(observation):
    return _player.act(observation)
