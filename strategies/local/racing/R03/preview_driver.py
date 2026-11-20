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
    grip = (1.0 - 0.40 * wet) * max(0.65, 1.0 -0.4 * wear)
    lateral_limit = 17.0 * grip
    braking = 13.0 * grip
    target = 75.0
    # The first preview point is 15 m ahead. Budget for actuator delay
    # and keep a local curvature limit until the car has crossed the apex.
    reaction_distance = car["speed_mps"] * 0.35 + 2.0
    target = min(target, math.sqrt(lateral_limit / max(current_curve, 0.001)))
    for row in rows:
        distance, curve = row[0], abs(row[1])
        remaining = max(0.0, distance - reaction_distance)
        corner_speed_sq = lateral_limit / max(curve, 0.001)
        target = min(target, math.sqrt(corner_speed_sq + 2 * braking * remaining))
    if abs(car["heading_error"]) > 0.3:
        target = min(target, 15.0)
    if car["fuel_kg"] < 5:
        target = min(target, 27.0)
    return target


def traffic_plan(o, target, offset):
    car = o["self"]
    n, speed = car["n"], car["speed_mps"]
    nearby = [r for r in o["rivals"] if r["status"] == "racing"]
    # Before moving toward the centre, allow a car alongside (or about to
    # draw alongside) to clear the lane. All distances below are in metres.
    for other in nearby:
        other_n = n + other["dn"]
        if abs(other_n - offset) >= abs(other["dn"]):
            continue
        if not min(n, offset) - 2.5 < other_n < max(n, offset) + 2.5:
            continue
        gap = other["gap_m"]
        next_gap = gap + 0.8 * (other["speed_mps"] - speed)
        if min(gap, next_gap) < 7.0 and max(gap, next_gap) > -7.0:
            offset = n
            break
    # A nearer car in the other column must not hide a slower leader.
    low, high = min(n, offset) - 2.5, max(n, offset) + 2.5
    braking = 4.0 * (1.0 - 0.4 * o["weather"]["track_wetness"])
    for other in nearby:
        gap, lead_speed = other["gap_m"], other["speed_mps"]
        if gap <= 0 or not low < n + other["dn"] < high:
            continue
        # Leave one car length and allow control response when closing.
        space = gap - 6.0 - 0.5 * max(0.0, speed - lead_speed)
        if space >= 0:
            cap = math.sqrt(lead_speed * lead_speed + 2.0 * braking * space)
        else:
            cap = max(0.0, lead_speed + space)
        target = min(target, cap)
    return target, offset


def choose_pit(o, wet_waiver):
    car = o["self"]
    if o["phase"] != "race" or car["pit_state"]:
        return False, "medium"
    # Schedule a soft-to-medium stop for the dry-compound rule.
    scheduled = not wet_waiver and car["pit_stops"] == 0 and car["lap"] >= 2
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
            self.start_distance = car.get("distance", car["s"])
            self.grid_lane = car["n"]
            self.wet_waiver = False
        if o["phase"] == "race" and o["weather"]["track_wetness"] >= 0.5:
            self.wet_waiver = True
        station = car["s"]
        if self.previous_station is not None and station < self.previous_station:
            self.recent_preview = []  # Lap seam or reset.
        self.previous_station = station
        distance, curvature = o["preview"]["rows"][0][:2]
        # Store one curvature maximum per 1 m bin. This bounds storage even
        # when the car is stopped or creeping towards a corner.
        bucket = math.floor(station + distance)
        self.recent_preview = [(i, k) for i, k in self.recent_preview if i + 1 >= station]
        if self.recent_preview and self.recent_preview[-1][0] == bucket:
            self.recent_preview[-1] = (bucket, max(self.recent_preview[-1][1], abs(curvature)))
        else:
            self.recent_preview.append((bucket, abs(curvature)))
        current_curve = max(k for _, k in self.recent_preview)
        target = plan_speed(o, current_curve)
        offset = 0.0
        # Stay in the starting lane in a crowded grid. Ease to the normal
        # traffic planner after the first 70 m, completing the merge by 150 m.
        lateral_error = abs(car["n"])
        if o["phase"] == "race":
            travelled = car.get("distance", station) - self.start_distance
            blend = clip((travelled - 70.0) / 80.0, 0.0, 1.0)
            fraction = blend * blend * (3.0 - 2.0 * blend)
            offset = (1.0 - fraction) * self.grid_lane + fraction * offset
        target, offset = traffic_plan(o, target, offset)
        if o["phase"] == "race" and travelled < 150.0:
            lateral_error = abs(car["n"] - offset)
        # Project the near-term lateral drift in road coordinates. The
        # nearest cross-section available in the observation is 15 m ahead.
        left, right = o["preview"]["rows"][0][3:5]
        heading = car["heading_error"]
        lateral_speed = (car.get("vx", car["speed_mps"]) * math.sin(heading)
                         + car.get("vy", 0.0) * math.cos(heading))
        projected_n = car["n"] + 0.6 * lateral_speed
        boundary_margin = min(left - max(car["n"], projected_n),
                              right + min(car["n"], projected_n))
        if lateral_error > 3.0 or boundary_margin <= 2.0:
            target = min(target, 15.0)
        curve = o["preview"]["rows"][0][1]
        # Road curvature plus heading and position feedback.
        turn = 3.4 * curve -0.6 * car["heading_error"]
        correction = turn -0.04 * (car["n"] -offset)
        desired = clip(correction / 0.30, -1, 1)
        steer = clip(desired, self.last_steer -0.15,
                     self.last_steer + 0.15)
        self.last_steer = steer
        error = target -car["speed_mps"]
        throttle = clip(error * 0.45, 0.0, 1.0)
        brake = clip(-error * 0.32, 0.0, 1.0)
        # Reduce power while recovering from a large heading error.
        if abs(car["heading_error"]) > 0.6:
            throttle = min(throttle, 0.25)
        if car["fuel_kg"] < 5:
            throttle = min(throttle, 0.65)
        rows = o["preview"]["rows"][:6]
        straight = max(abs(row[1]) for row in rows) < 0.002
        boost = straight and error > 1 and car["ers_soc_mj"] > 1.5
        pit, compound = choose_pit(o, self.wet_waiver)
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
