"""Test waypoint recovery only for the defending seat on the two mesh maps."""
from v3_base import RoutePlayer
from waypoint_nav import NavigationMixin

NAVIGATION_MAPS = frozenset({"dust2-cs2-v54", "mirage-cs2-v54"})


class Candidate(NavigationMixin, RoutePlayer):
    def load_map(self, row):
        if row["id"] in NAVIGATION_MAPS:
            # Map data can arrive on attack and be omitted after the side switch.
            super().load_map(row)
        else:
            RoutePlayer.load_map(self, row)

    def move(self, observation, goal, action):
        if self.map_id in NAVIGATION_MAPS and observation["side"] == "defend":
            return super().move(observation, goal, action)
        return RoutePlayer.move(self, observation, goal, action)


_player = Candidate()


def init(context):
    global _player
    _player = Candidate()


def act(observation):
    return _player.act(observation)
