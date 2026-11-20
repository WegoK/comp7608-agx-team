"""R08 game navigation. Positions and distances use the public map frame."""
import math
from collections import deque

from starter_player import Player, angle, clip
from waypoint_nav import NavigationMixin


DUST2 = "dust2-cs2-v54"
NAVIGATION_MAPS = {DUST2, "mirage-cs2-v54"}
# Public map coordinates, in platform metres. A uses the ground beside the car.
CAR_GROUND = (151.8827, 145.0278, 20.1524)
VIEWS = {
    "A": ((140.0, 139.0, 21.6), (153.0, 108.0, 24.0)),
    "B": ((153.0, 58.0, 21.62),),
}


def hold_view(me, views, crouch, action):
    eye_z = me["z"] + (0.95 if me.get("crouch") else 1.62)
    headings = [math.atan2(me["y"] - y, x - me["x"]) for x, y, z in views]
    # Average unit vectors so two bearings either side of +/-pi stay together.
    yaw = math.atan2(sum(math.sin(h) for h in headings),
                     sum(math.cos(h) for h in headings))
    pitch = sum(math.atan2(z - eye_z, math.hypot(x - me["x"], y - me["y"]))
                for x, y, z in views) / len(views)
    action.update(move_forward=0.0, move_right=0.0, crouch=crouch,
                  yaw_rate=clip(angle(yaw - me["yaw"]) / 0.15),
                  pitch_rate=clip((pitch - me["pitch"]) / 0.1))
    return action


class Candidate(NavigationMixin, Player):
    def __init__(self):
        super().__init__()
        self.map_id = None
        self.hold_task = None
        self.live_stage = self.first_live = None
        self.reset_motion()

    def load_map(self, row):
        if row["id"] != self.map_id:
            self.hold_task = None
            self.path, self.goal = [], None
            self.reset_motion()
        super().load_map(row)
        self.map_id = row["id"]

    def reset_motion(self):
        self.motion_task = None
        self.last_move_tick = None
        self.samples = deque()
        self.escape = None
        self.opening_s = None
        self.opening_bearing = 0.0
        self.escape_count = 0
        self.last_escape_end = -1000

    def act(self, obs):
        tick = obs["match_tick"]
        stage = (obs.get("map_id"), obs["round_index"], obs["side"])
        if self.live_stage != stage:
            self.live_stage = stage
            self.first_live = None
            self.reset_motion()
        if obs["phase"] == "live" and self.first_live is None:
            self.first_live = tick
        action = super().act(obs)
        # Objective use, stationary holds and other non-navigation decisions
        # preempt locomotion. Never accumulate a stall across those pauses.
        if self.last_move_tick != tick and self.motion_task is not None:
            self.reset_motion()
        if obs["side"] == "attack" and "walk" in action:
            action["walk"] = False
        return action

    def move(self, observation, goal, action):
        if observation["side"] == "defend" and self.map_id not in NAVIGATION_MAPS:
            return Player.move(self, observation, goal, action)
        if (observation["side"] == "defend" and self.map_id == DUST2
                and observation["bomb"]["state"] != "planted"):
            index = (observation["round_index"] + observation["seat"] % 2) % len(self.sites)
            site = self.sites[index]["id"]
            if site in VIEWS:
                if site == "A":
                    goal = list(CAR_GROUND)
                me = observation["me"]
                task = (observation["round_index"], site)
                distance = math.dist((me["x"], me["y"], me["z"]), goal)
                # Tolerate small shifts by other avatars after arriving.
                if distance <= (1.0 if self.hold_task == task else 0.50):
                    self.hold_task = task
                    # At the A car the lower camera can be hidden by the bodywork.
                    return hold_view(me, VIEWS[site], site == "B", action)
        self.hold_task = None
        return self.navigate(observation, goal, action)

    def navigate(self, obs, goal, action):
        me = obs["me"]
        pos = (me["x"], me["y"], me["z"])
        tick = obs["match_tick"]
        attack = obs["side"] == "attack"
        task = (tuple(goal), obs["bomb"]["state"] == "planted")
        if self.motion_task != task or self.last_move_tick != tick - 3:
            self.reset_motion()
            self.motion_task = task
        self.last_move_tick = tick

        if self.escape is not None:
            start, bearing, sign, blocked = self.escape
            elapsed = tick - start
            if elapsed < 42:
                return self.escape_step(action, me, elapsed, bearing, sign)
            route = self._detour(pos, goal, blocked, tick)
            if route:
                self.path, self.goal = route, list(goal)
            self.escape = None
            self.last_escape_end = tick
            self.samples.clear()
            self._window_point = None

        result = super().move(obs, goal, action)
        point = self.path[0]
        if not result.get("move_forward") and not result.get("move_right"):
            self.reset_motion()
            return result

        # Sample actual positions on a rolling physical-time window. Keep the
        # same samples if the planner changes intermediate nodes while jammed.
        window = 72 if attack else 129
        self.samples.append((tick, pos))
        while self.samples[0][0] < tick - window:
            self.samples.popleft()
        full_window = tick - self.samples[0][0] >= window
        bearing = math.atan2(pos[1] - point[1], point[0] - pos[0])
        if (full_window and tick - self.last_escape_end >= window
                and math.dist(pos, goal) > 1.0
                and max(math.dist(p, pos) for _, p in self.samples) < 0.65):
            sign = 1 if (obs["seat"] + self.escape_count) % 2 else -1
            self.escape_count += 1
            self.escape = (tick, bearing, sign, point)
            return self.escape_step(action, me, 0, bearing, sign)
        if not attack:
            return result

        # The camera can follow the upcoming public path segment while the
        # movement vector remains expressed in the avatar's current frame.
        delta = angle(bearing - me["yaw"])
        result.update(walk=False, move_forward=math.cos(delta), move_right=-math.sin(delta))
        if len(self.path) > 1 and math.dist(pos, point) < 4.0:
            ahead = self.path[1]
            ax, ay = ahead[0] - pos[0], pos[1] - ahead[1]
            if math.hypot(ax, ay) > 0.75:
                turn = angle(math.atan2(ay, ax) - bearing)
                look = bearing + max(-math.pi/3, min(math.pi/3, turn))
                result["yaw_rate"] = clip(angle(look - me["yaw"]) / 0.15)

        # Each team starts in a cluster. Exact teammate positions are absent
        # from the public view, so pick a lane by seat before first contact.
        # Fix the initial heading briefly; aiming back at a nearby waypoint
        # immediately would cut the path back across the same blocker.
        if tick == self.first_live and any(t.get("alive") for t in obs.get("teammates", ())):
            self.opening_s = tick
            self.opening_bearing = bearing
        if self.opening_s is not None:
            age = (tick - self.opening_s) / 60.0
            if 0.0 <= age < 1.25:
                sign = 1 if obs["seat"] % 2 else -1
                if age < 0.4:
                    offset = 1.15
                elif age < 0.75:
                    offset = 1.15 * (0.75 - age) / 0.35
                else:
                    offset = -0.28 * math.sin(math.pi * (age - 0.75) / 0.50)
                delta = angle(self.opening_bearing + sign * offset - me["yaw"])
                result.update(move_forward=math.cos(delta), move_right=-math.sin(delta))
        return result

    @staticmethod
    def escape_step(action, me, elapsed, bearing, sign):
        # Back off for 0.25s, step sideways for 0.45s, then let the planner
        # choose an available nearby graph attachment from the new position.
        direction = bearing + (math.pi if elapsed < 15 else sign * math.pi / 2)
        delta = angle(direction - me["yaw"])
        action.update(move_forward=math.cos(delta), move_right=-math.sin(delta),
                      walk=False, jump=False, yaw_rate=0.0)
        return action


_player = Candidate()


def init(context):
    global _player
    _player = Candidate()


def act(observation):
    return _player.act(observation)
