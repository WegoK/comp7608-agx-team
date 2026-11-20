"""Rule-based driver: speed planning, traffic, tyres and energy."""
import math


def clip(value, low, high):
    return max(low, min(high, value))


def plan_speed(o, current_curve):
    car = o["self"]
    rows = o["preview"]["rows"]
    wet = o["weather"]["track_wetness"]
    wear = max(car["tyre_wear"])
    # Lower lateral acceleration when grip is likely to be worse.
    grip = (1.0 -0.45 * wet) * max(0.65, 1.0 -0.4 * wear)
    lateral_limit = 10.0 * grip
    braking = 7.0 * grip
    target = 45.0
    # The first preview point is 15 m ahead. Budget for actuator delay
    # and keep a local curvature limit until the car has crossed the apex.
    reaction_distance = car["speed_mps"] * 0.35 + 2.0
    target = min(target, math.sqrt(lateral_limit / max(current_curve, 0.001)))
    for row in rows:
        distance, curve = row[0], abs(row[1])
        remaining = max(0.0, distance - reaction_distance)
        corner_speed_sq = lateral_limit / max(curve, 0.001)
        target = min(target, math.sqrt(corner_speed_sq + 2 * braking * remaining))
    if abs(car["n"]) > 3 or abs(car["heading_error"]) > 0.3:
        target = min(target, 15.0)
    if car["fuel_kg"] < 5:
        target = min(target, 27.0)
    return target


def traffic_plan(o, target):
    car = o["self"]
    offset = 0.0
    nearby = [r for r in o["rivals"] if r["status"] == "racing"]
    leaders = [r for r in nearby if 0 < r["gap_m"] < 35]
    if not leaders:
        return target, offset
    front = min(leaders, key=lambda r: r["gap_m"])
    same_line = abs(front["dn"]) < 2.2
    if same_line and front["gap_m"] < 12:
        target = min(target, max(0.0, front["speed_mps"] -2.0))
    curves = [abs(r[1]) for r in o["preview"]["rows"][:6]]
    straight = max(curves) < 0.002
    # Try a modest offset only on a straight with enough road width.
    if same_line and straight and 12 <= front["gap_m"] < 30:
        road = o["preview"]["rows"][:6]
        width = min(min(r[3], r[4]) for r in road)
        if width > 4:
            for candidate in (-2.5, 2.5):
                clear = all(abs(r["gap_m"]) > 15 or
                            abs(car["n"] + r["dn"]
                                -candidate) > 2.5
                            for r in nearby)
                if clear:
                    offset = candidate
                    break
    return target, offset


def choose_pit(o):
    car = o["self"]
    if o["phase"] != "race" or car["pit_state"]:
        return False, "medium"
    # Schedule a soft-to-medium stop for the dry-compound rule.
    scheduled = car["pit_stops"] == 0 and car["lap"] >= 2
    worn = max(car["tyre_wear"]) > 0.75
    overheated = max(car["tyre_temp_c"]) > 130
    laps_left = o["laps_total"] -car["lap"]
    extra_stop = laps_left > 1 and (worn or overheated)
    compound = "hard" if car["compound"] == "medium" else "medium"
    return scheduled or extra_stop, compound


class Driver:
    def __init__(self):
        self.last_steer = 0.0
        self.last_phase = None
        self.recent_preview = []
        self.previous_station = None

    def act(self, o):
        car = o["self"]
        if self.last_phase != o["phase"]:
            self.last_steer = 0.0
            self.last_phase = o["phase"]
            self.recent_preview = []
            self.previous_station = None
        station = car["s"]
        if self.previous_station is not None and station < self.previous_station:
            self.recent_preview = []  # Lap seam or reset.
        self.previous_station = station
        distance, curvature = o["preview"]["rows"][0][:2]
        # Hold the tightest upcoming value over the blind 0..15 m interval.
        self.recent_preview = [(s, k) for s, k in self.recent_preview if s >= station]
        point = station + distance
        if not self.recent_preview or point != self.recent_preview[-1][0]:
            self.recent_preview.append((point, abs(curvature)))
        current_curve = max(k for _, k in self.recent_preview)
        target, offset = traffic_plan(o, plan_speed(o, current_curve))
        curve = o["preview"]["rows"][0][1]
        # Road curvature plus heading and position feedback.
        turn = 3.4 * curve -0.6 * car["heading_error"]
        correction = turn -0.04 * (car["n"] -offset)
        desired = clip(correction / 0.30, -1, 1)
        steer = clip(desired, self.last_steer -0.15,
                     self.last_steer + 0.15)
        self.last_steer = steer
        error = target -car["speed_mps"]
        throttle = clip(error * 0.25, 0.0, 1.0)
        brake = clip(-error * 0.32, 0.0, 1.0)
        # Reduce power while recovering from a large heading error.
        if abs(car["heading_error"]) > 0.6:
            throttle = min(throttle, 0.25)
        if car["fuel_kg"] < 5:
            throttle = min(throttle, 0.65)
        rows = o["preview"]["rows"][:6]
        straight = max(abs(row[1]) for row in rows) < 0.002
        boost = straight and error > 1 and car["ers_soc_mj"] > 1.5
        pit, compound = choose_pit(o)
        if car["pit_state"]:
            throttle, brake, steer = 0.0, 0.0, 0.0
        return {
            "throttle": throttle, "brake": brake, "steer": steer,
            "ers_mode": 2 if boost else 1,
            "aero_mode": 0,
            "pit_request": bool(pit), "pit_compound": compound
        }


_driver = Driver()


def init(context):
    global _driver
    _driver = Driver()


def act(observation):
    return _driver.act(observation)
