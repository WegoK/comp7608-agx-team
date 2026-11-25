"""R08 objectives and navigation with bounded last-seen aim retention."""
import math
from collections import deque

from starter_player import Player, angle, clip
from waypoint_nav import NavigationMixin


DUST2 = "dust2-cs2-v54"
NAVIGATION_MAPS = {DUST2, "mirage-cs2-v54"}
AIM_HOLD_TICKS = 18  # 0.3 seconds; last-seen position is never a firing permit.
# Public map coordinates, in platform metres. A uses the ground beside the car.
CAR_GROUND = (151.8827, 145.0278, 20.1524)
VIEWS = {
    "A": ((140.0, 139.0, 21.6), (153.0, 108.0, 24.0)),
    "B": ((153.0, 58.0, 21.62),),
}


def dropped_bomb_goal(observation):
    """Recover a known dropped bomb; hidden/invalid locations have no target."""
    if observation["side"] != "attack" or observation["me"]["carrying_bomb"]:
        return None
    bomb = observation["bomb"]
    if bomb["state"] != "world":
        return None
    position = bomb.get("position")
    if (not isinstance(position, (list, tuple)) or len(position) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)):
        return None
    return list(position)


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
        self.reset_retake()
        self.reset_aim()

    def buy(self, obs):
        me = obs['me']
        if obs['phase'] != 'buy' or not me['alive']:
            return {}
        context = (obs.get('map_id'), obs['round_index'], obs['side'])
        if getattr(self, 'buy_context', None) != context:
            self.buy_context = context
            self.buy_rejected = set()
            self.buy_pending = None
        if self.buy_pending is not None:
            if any(r.get('action') == 'buy' and r.get('ok') is False
                   for r in obs.get('receipts', ())):
                self.buy_rejected.add(self.buy_pending)
            self.buy_pending = None
        rifle, price = ('ak47', 2700) if obs['side'] == 'attack' else ('m4a1', 2900)
        # Keep the main weapon affordable, then protect the player before kit.
        # Only request the observed 350 helmet upgrade with full body armour.
        choices = ((rifle, price, me['weapons'][0] is None),
                   ('armor', 650, me['armor'] < 50),
                   ('helmet', 350, me['armor'] >= 100 and not me.get('helmet', False)),
                   ('defusekit', 400, obs['side'] == 'defend' and not me['has_kit']))
        for kind, cost, needed in choices:
            if needed and me['cash'] >= cost and kind not in self.buy_rejected:
                self.buy_pending = kind
                return {'buy': kind}
        return {}

    def reset_aim(self):
        self.aim_target = None
        self.aim_seen_tick = None

    def combat(self, obs, action, gun):
        if obs['me']['grounded'] and not obs['me']['blind_ticks']:
            me = obs['me']
            enemy = min(obs['visible'], key=lambda e:
                        (e['x']-me['x'])**2 + (e['y']-me['y'])**2)
            self.aim_target = {k: enemy[k] for k in ('x', 'y', 'z', 'crouch')}
            self.aim_target['parts'] = list(enemy['parts'])
            self.aim_seen_tick = obs['match_tick']
        return super().combat(obs, action, gun)

    def load_map(self, row):
        if row["id"] != self.map_id:
            self.hold_task = None
            self.path, self.goal = [], None
            self.reset_motion()
            self.reset_retake()
            self.reset_aim()
        super().load_map(row)
        self.map_id = row["id"]

    def reset_motion(self):
        self.motion_task = None
        self.last_move_tick = None
        self.samples = deque()
        self.escape = None
        self.opening_s = None
        self.opening_bearing = 0.0
        self.step_attempt = {}
        self.escape_count = 0
        self.last_escape_end = -1000

    def reset_retake(self):
        self.retake_key = None
        self.retake_site = None
        self.retake_searched = set()
        self.reset_scan()

    def reset_scan(self):
        self.scan_start = self.scan_tick = None

    def search_planted_bomb(self, obs, action):
        """Inspect public sites while the planted bomb position is unknown.

        A completed look is only a search attempt, not evidence of absence.
        Cycle sites if necessary; no replay-only location enters the policy.
        Player.act retains combat and known-bomb defuse precedence.
        """
        bomb = obs["bomb"]
        if obs["side"] != "defend" or bomb["state"] != "planted" or bomb.get("position"):
            return None, None
        key = (self.map_id, obs["round_index"], bomb.get("fuse_end_tick"))
        if self.retake_key != key:
            self.reset_retake()
            self.retake_key = key
        me, tick = obs["me"], obs["match_tick"]
        pos = (me["x"], me["y"], me["z"])
        if self.retake_site is None:
            self.retake_site = min(range(len(self.sites)),
                                   key=lambda i: math.dist(pos, self.sites[i]["center"]))
        goal = list(self.sites[self.retake_site]["center"])
        if math.dist(pos, goal) > 1.0:
            self.reset_scan()
            return goal, None

        # At 20 decisions/s, 150 ticks give 2.5s to look around the floor.
        # Blindness or a combat interruption must not count as inspection.
        if me["blind_ticks"] or not me["grounded"]:
            self.reset_scan()
        else:
            if self.scan_tick != tick - 3:
                self.scan_start = tick
            self.scan_tick = tick
            if tick - self.scan_start >= 150:
                self.retake_searched.add(self.retake_site)
                remaining = set(range(len(self.sites))) - self.retake_searched
                if not remaining:
                    self.retake_searched.clear()
                    remaining = set(range(len(self.sites))) - {self.retake_site}
                self.retake_site = min(remaining or {self.retake_site},
                                       key=lambda i: (math.dist(pos, self.sites[i]["center"]), i))
                self.reset_scan()
                self.path, self.goal = [], None
                self.last_position, self.stuck = None, 0
                return list(self.sites[self.retake_site]["center"]), None

        self.path, self.goal = [], goal
        self.last_position, self.stuck = None, 0
        self.reset_motion()
        action.update(move_forward=0.0, move_right=0.0, yaw_rate=1.0,
                      pitch_rate=clip((-0.65 - me["pitch"]) / 0.1), walk=False)
        return goal, action

    def act(self, obs):
        tick = obs["match_tick"]
        stage = (obs.get("map_id"), obs["round_index"], obs["side"])
        if self.live_stage != stage:
            self.live_stage = stage
            self.first_live = None
            self.reset_motion()
            self.reset_retake()
            self.reset_aim()
        if (obs['phase'] != 'live' or not obs['me']['alive']
                or obs['me']['blind_ticks'] or not obs['me']['grounded']
                or (self.aim_seen_tick is not None
                    and not 0 <= tick-self.aim_seen_tick <= AIM_HOLD_TICKS)):
            self.reset_aim()
        if (obs["phase"] != "live" or not obs["me"]["alive"]
                or obs["side"] != "defend" or obs["bomb"]["state"] != "planted"
                or obs["bomb"].get("position")):
            self.reset_retake()
        if obs["phase"] == "live" and self.first_live is None:
            self.first_live = tick
        action = super().act(obs)
        if action.get('use'):
            self.reset_aim()
        elif (self.aim_target is not None and not obs['visible']
              and obs['phase'] == 'live' and obs['me']['alive']
              and not obs['me']['blind_ticks'] and obs['me']['grounded']):
            # Aim at a short-lived memory, not a newly observed enemy. Reuse
            # the same recoil compensation but categorically forbid gunfire.
            remembered = dict(obs, visible=[self.aim_target])
            slot = action['weapon_slot']
            held = {k: action[k] for k in ('weapon_slot', 'reload') if k in action}
            action = Player.combat(self, remembered, held, obs['me']['weapons'][slot])
            action['fire'] = False
            self.reset_scan()
            self.reset_motion()
        # A higher-priority stationary decision interrupted any ongoing scan.
        if self.scan_tick is not None and self.scan_tick != tick:
            self.reset_scan()
        # Objective use, stationary holds and other non-navigation decisions
        # preempt locomotion. Never accumulate a stall across those pauses.
        if self.last_move_tick != tick and self.motion_task is not None:
            self.reset_motion()
        if "walk" in action and (obs["side"] == "attack" or (
                obs["side"] == "defend" and obs["bomb"]["state"] == "planted"
                and obs["phase"] == "live" and obs["me"]["alive"])):
            action["walk"] = False
        return action

    def move(self, observation, goal, action):
        search_goal, scan_action = self.search_planted_bomb(observation, action)
        if scan_action is not None:
            return scan_action
        if search_goal is not None:
            goal = search_goal
        recovery_goal = dropped_bomb_goal(observation)
        if recovery_goal is not None:
            # The arena picks up a nearby visible bomb automatically. Keep
            # combat/plant/defuse precedence in Player.act; only redirect travel.
            goal = recovery_goal
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
        self.horizontal_recovery = self.map_id == 'mirage-cs2-v54'
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
            if self.horizontal_recovery and not me['grounded']:
                return self.escape_step(action, me, elapsed, bearing, sign)
            route = self._detour(pos, goal, blocked, tick)
            if route:
                self.path, self.goal = route, list(goal)
            self.escape = None
            self.last_escape_end = tick
            self.samples.clear()
            self._window_point = None

        self.long_avoid = attack and self.map_id == DUST2
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
                and (not self.horizontal_recovery or me['grounded'])
                and max(math.dist(p[:2], pos[:2]) if self.horizontal_recovery else math.dist(p, pos)
                        for _, p in self.samples) < 0.65):
            if (attack and self.map_id == DUST2 and me["grounded"]
                    and 0.25 < point[2] - pos[2] < 1.9
                    and self.step_attempt.get(tuple(point), 0) <= tick):
                self.step_attempt[tuple(point)] = tick + 3600
                self.samples.clear()
                result.update(jump=True, walk=False, move_forward=math.cos(bearing - me["yaw"]),
                              move_right=-math.sin(bearing - me["yaw"]))
                return result
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
