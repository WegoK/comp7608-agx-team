"""Local game-policy candidate. Two independent switches support paired comparisons."""
from v3_base import RoutePlayer
from waypoint_nav import NavigationMixin
from objective import ObjectiveMixin

class Candidate(ObjectiveMixin, NavigationMixin, RoutePlayer):
    nav_enabled = True
    objective_enabled = True

_player = Candidate()

def init(context):
    global _player
    _player = Candidate()

def act(observation):
    return _player.act(observation)
