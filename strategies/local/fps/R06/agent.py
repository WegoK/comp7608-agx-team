"""Dust2 opening destinations on the starter's route planner."""
import math

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

    def load_map(self, row):
        if row["id"] != self.map_id:
            self.hold_task = None
        if row["id"] in NAVIGATION_MAPS:
            super().load_map(row)
        else:
            Player.load_map(self, row)
        self.map_id = row["id"]

    def move(self, observation, goal, action):
        if observation["side"] != "defend" or self.map_id not in NAVIGATION_MAPS:
            return Player.move(self, observation, goal, action)
        if self.map_id == DUST2 and observation["bomb"]["state"] != "planted":
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
        return super().move(observation, goal, action)


_player = Candidate()


def init(context):
    global _player
    _player = Candidate()


def act(observation):
    return _player.act(observation)
