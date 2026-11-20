"""Local simulator entry point for the original R01 single-arm experiment."""
from planners import Experiment
from starter_player import Player


def init(context):
    global _player, _experiment
    _player = Player()
    _experiment = Experiment(_player, "straight")


def act(observation):
    _experiment.round = observation["round_index"]
    _experiment.side = observation["side"]
    return _player.act(observation)


init({})
