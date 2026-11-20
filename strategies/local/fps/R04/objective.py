"""Track the virtual objective with the player's own observation and public map sites."""
import math

from v3_base import angle, clip

class ObjectiveMixin:
    objective_enabled = True

    def __init__(self):
        super().__init__()
        self._objective_round = None
        self._clear_objective()

    def _clear_objective(self):
        self.objective_mode = "opening"
        self.inspection_site = None
        self._reported_site = None
        self._known_point = None
        self._inside_since = None

    def _leave_opening_route(self):
        self.goal = None
        self.path = []
        self.last_position = None
        self.stuck = 0
        if self.nav_enabled:
            self.reset_navigation()

    def _observe_objective(self, observation):
        key = (observation.get("map_id"), observation["round_index"], observation["side"])
        if self._objective_round != key:
            self._clear_objective()
            self._objective_round = key
        if (observation["phase"] != "live" or not observation["me"]["alive"]
                or observation["side"] != "defend"):
            return
        bomb = observation["bomb"]
        if bomb["state"] != "planted":
            return
        if self.objective_mode == "opening":
            self._leave_opening_route()
            self.objective_mode = "inspect"

        position = bomb.get("position")
        if position is not None:
            # This game object stays fixed once placed. The cache lasts one round.
            self._known_point = tuple(position)
        sites = observation.get("map", {}).get("sites", self.sites)
        site_id = bomb.get("site")
        if site_id is not None and any(s["id"] == site_id for s in sites):
            self._reported_site = site_id
        if self._known_point is not None:
            self.objective_mode = "located"
        elif self._reported_site is not None:
            self.objective_mode = "site"

    def act(self, observation):
        if not self.objective_enabled:
            return super().act(observation)
        self._observe_objective(observation)
        if (self.objective_mode == "located" and observation["bomb"]["state"] == "planted"
                and observation["bomb"].get("position") is None):
            # Restore only coordinates that this same seat has already received.
            bomb = dict(observation["bomb"], position=self._known_point)
            observation = dict(observation, bomb=bomb)
        return super().act(observation)

    def move(self, observation, goal, action):
        if not self.objective_enabled or self.objective_mode in ("opening", "located"):
            return super().move(observation, goal, action)
        pos = tuple(observation["me"][axis] for axis in "xyz")
        if self.objective_mode == "site":
            selected = self._reported_site
        else:
            if self.inspection_site is None:
                nearest = min(self.sites, key=lambda s: (math.dist(pos, s["center"]), s["id"]))
                self.inspection_site = nearest["id"]
            selected = self.inspection_site
        site = next(s for s in self.sites if s["id"] == selected)
        if math.dist(pos, site["center"]) > 2.0:
            self._inside_since = None
            return super().move(observation, site["center"], action)

        # A hidden objective can be underneath the avatar and outside its view.
        # Observe the *public marker* before continuing to another unconfirmed site.
        tick = observation["match_tick"]
        if self._inside_since is None:
            self._inside_since = tick
        me = observation["me"]
        dx, dy, dz = (b - a for a, b in zip(pos, site["center"]))
        horizontal = math.hypot(dx, dy)
        yaw = angle(math.atan2(-dy, dx) - me["yaw"]) if horizontal > 0.25 else 0.0
        eye_height = 0.95 if me.get("crouch") else 1.62
        pitch = math.atan2(dz - eye_height, max(0.01, horizontal)) - me["pitch"]
        elapsed = tick - self._inside_since
        facing_marker = abs(yaw) <= 0.2 and abs(pitch) <= 0.2
        inspected = elapsed >= 30 and facing_marker
        if (self.objective_mode == "site" or len(self.sites) < 2
                or (elapsed < 120 and not inspected)):
            return dict(action, yaw_rate=clip(yaw / 0.15), pitch_rate=clip(pitch / 0.1))
        others = [s for s in self.sites if s["id"] != selected]
        next_site = min(others, key=lambda s: (math.dist(pos, s["center"]), s["id"]))
        self.inspection_site = next_site["id"]
        self._inside_since = None
        return super().move(observation, next_site["center"], action)
