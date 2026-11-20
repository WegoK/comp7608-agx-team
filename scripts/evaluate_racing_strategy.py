"""Check R07 lateral cut-in decisions using synthetic legal observations.

No collision model, race outcome, rating, or platform admission is simulated.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '2b3c1c4a419325133c5b56fa79349ec47f51fa80'
TRACKS = {'parkland': 5795.930, 'highland': 6999.478, 'crossover': 5803.256}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_bytes(name):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{BASELINE}:{name}'])


def observation(track='crossover', phase='race', time=0.0, station=200.0):
    car = dict(s=station, distance=station, speed_mps=30.0, n=0.0,
               heading_error=0.0, fuel_kg=12.0, tyre_wear=[.05] * 4,
               tyre_temp_c=[85.0] * 4, ers_soc_mj=3.0, pit_state=0,
               lap=0, compound='soft', pit_stops=0, vx=30.0, vy=0.0,
               x=0.0, y=0.0, yaw=0.0, yaw_rate=0.0, steer=0.0,
               effective_mass_kg=812.0, drive_force_n=[2500.0, 3000.0, 4000.0, 4500.0],
               brake_force_max_n=20000.0, drag_n=400.0, rolling_resistance_n=100.0,
               front_sliding=False, rear_sliding=False, g_lat=0.0,
               pu_temp_c=90.0, aid_available=False, aid_zone=None, override_available=False,
               status='racing')
    return dict(self=car, phase=phase, time=time, tick=round(time * 60),
                track_length_m=TRACKS[track], laps_total=4, rivals=[],
                weather=dict(track_wetness=0.0, rain_intensity=0.0, condition='dry'),
                preview=dict(rows=[[float(d), 0.0, 0.0, 7.0, 7.0] for d in range(15, 361, 15)]))


def rival(seat, gap, n, speed=30.0, rate=0.0):
    return dict(seat=seat, gap_m=gap, dn=n, speed_mps=speed,
                status='racing', lateral_speed_mps=rate)


def legal(action):
    assert isinstance(action, dict)
    assert set(action) <= {'throttle', 'brake', 'steer', 'ers_mode', 'aero_mode',
                           'pit_request', 'pit_compound', 'override', 'aid'}
    for key in ('throttle', 'brake', 'steer'):
        assert type(action[key]) in (int, float) and math.isfinite(action[key])
        assert (-1 if key == 'steer' else 0) <= action[key] <= 1
    assert type(action['ers_mode']) is int and action['ers_mode'] in (0, 1, 2, 3)
    assert type(action['aero_mode']) is int and action['aero_mode'] in (0, 1)
    assert type(action['pit_request']) is bool
    # The inherited Parkland controller emits null when it requests no stop.
    # Check that neutral form without claiming to reproduce platform admission.
    assert (action['pit_compound'] in ('soft', 'medium', 'hard', 'intermediate', 'wet')
            or (action['pit_compound'] is None and not action['pit_request']))
    json.dumps(action, allow_nan=False)


def check_corridors(old, new):
    car = observation()['self']
    entrant = rival(2, 5.0, 8.0, rate=-2.0)
    assert old.Driver.corridor_free(car, [entrant], 3.8, 1)
    assert not new.Driver.corridor_free(car, [entrant], 3.8, 1)
    rng = random.Random(7608)
    stationary = conservative = 0
    for _ in range(1000):
        car = dict(n=rng.uniform(-4, 4), speed_mps=rng.uniform(5, 60))
        lane = rng.uniform(-4, 4)
        cars = [rival(i, rng.uniform(-70, 100), rng.uniform(-12, 12), rng.uniform(5, 60))
                for i in range(1, 5)]
        before = old.Driver.corridor_free(car, cars, lane, 1)
        assert before == new.Driver.corridor_free(car, cars, lane, 1)
        stationary += 1
        for row in cars:
            row['lateral_speed_mps'] = rng.uniform(-3, 3)
        after = new.Driver.corridor_free(car, cars, lane, 1)
        assert before or not after  # Adding motion must not waive an existing veto.
        conservative += 1
    return dict(outside_lane_cut_in_veto=True, unchanged_stationary_cases=stationary,
                existing_veto_preserved_cases=conservative, seed=7608)


def check_estimator(new):
    driver = new.Driver()
    first = observation(time=0.0)
    first['rivals'] = [rival(2, 5, 8.4)]
    original = copy.deepcopy(first)
    assert driver.observe_lateral_motion(first)['rivals'][0]['lateral_speed_mps'] == 0
    assert first == original
    second = observation(time=.1)
    second['rivals'] = [rival(2, 5, 8.2)]
    assert math.isclose(driver.observe_lateral_motion(second)['rivals'][0]['lateral_speed_mps'], -2)
    third = observation(time=.2)
    third['self']['n'] = 1.0
    third['rivals'] = [rival(2, 5, 7.2)]
    assert abs(driver.observe_lateral_motion(third)['rivals'][0]['lateral_speed_mps']) < 1e-9
    stale = observation(time=1.0)
    stale['rivals'] = [rival(2, 5, -5)]
    assert driver.observe_lateral_motion(stale)['rivals'][0]['lateral_speed_mps'] == 0
    driver.observe_lateral_motion(observation(time=1.1))
    assert driver.observe_lateral_motion(stale)['rivals'][0]['lateral_speed_mps'] == 0
    return ['first_view_zero', 'two_view_estimate', 'own_lane_change_compensated',
            'stale_history_reset', 'reappearing_rival_reset', 'caller_observation_unchanged']


def check_passing(old, new):
    # Warm up with legal public views, then compare the same passing opportunity.
    before = observation(time=1.9)
    before['rivals'] = [rival(1, 20, 0, 15), rival(2, 5, 8.4), rival(3, 0, -4)]
    now = observation(time=2.0, station=203.0)
    now['rivals'] = [rival(1, 20, 0, 15), rival(2, 5, 8.2), rival(3, 0, -4)]
    drivers = [m.Driver() for m in (old, new)]
    actions = []
    for driver in drivers:
        driver.act(copy.deepcopy(before))
        driver.start_distance, driver.waiting_for, driver.wait_since = 0.0, 1, 0.0
        action = driver.act(copy.deepcopy(now))
        legal(action)
        actions.append(action)
    assert drivers[0].pass_seat == 1 and drivers[0].pass_offset == 3.8
    assert drivers[1].pass_seat is None
    assert actions[0]['steer'] > actions[1]['steer']
    # An observed leader drifting into the intended lane also vetoes it early.
    obs = observation(time=3.0)
    obs['rivals'] = [rival(1, 20, 0, 15, .4)]
    lanes = []
    for module in (old, new):
        driver = module.Driver()
        driver.retry_at, driver.waiting_for, driver.wait_since = 0.0, 1, 0.0
        lanes.append(driver.passing_line(obs, 75.0))
    assert lanes == [3.8, -3.8]
    return dict(two_observation_cut_in=dict(baseline_action=actions[0], candidate_action=actions[1],
                                           baseline_commits=True, candidate_commits=False),
                drifting_leader_lane_choice=lanes)


def check_regression(old, new):
    count = 0
    for track in TRACKS:
        for phase in ('qualifying', 'race'):
            for wet in (0.0, .7):
                drivers = [m.Driver() for m in (old, new)]
                for i in range(200):
                    obs = observation(track, phase, i / 30, station=200 + i)
                    obs['self'].update(distance=200 + i, lap=2 if i >= 100 else 0)
                    obs['weather']['track_wetness'] = wet
                    obs['rivals'] = [rival(1, 20, 0, 15), rival(2, -30, 6)]
                    actions = [d.act(copy.deepcopy(obs)) for d in drivers]
                    assert actions[0] == actions[1]
                    legal(actions[1])
                    count += 1
    driver = new.Driver()
    driver.act(observation(phase='qualifying'))
    obs = observation(phase='race', time=.1)
    obs['rivals'] = [rival(2, 5, 8)]
    driver.act(obs)
    assert driver.rival_time == .1
    assert driver.rival_positions == {2: 8.0}
    return dict(unchanged_static_traffic_callbacks=count, tracks=list(TRACKS),
                phases=['qualifying', 'race'], wetness=[0.0, .7], phase_reset=True)


def check_bundle_sources():
    import numpy as np

    files = ('agent.py', 'design.json', 'weights/plan.npz', 'weights/mechanics.npz')
    for name in files:
        current = (ROOT / 'strategies/racing' / name).read_bytes()
        if not name.endswith('.npz'):
            current = current.replace(b'\r\n', b'\n')
        assert current == git_bytes('strategies/racing/' + name)
    for name in ('plan.npz', 'mechanics.npz'):
        with np.load(ROOT / 'strategies/racing/weights' / name, allow_pickle=False) as arrays:
            assert all(arrays[key].dtype.kind in 'biuf' for key in arrays.files)
    main = load_module('racing_main', ROOT / 'strategies/racing/agent.py')
    preview = load_module('racing_preview_probe', ROOT / 'strategies/racing/preview_driver.py')
    saved = sys.modules.get('preview_driver')
    sys.modules['preview_driver'] = preview
    try:
        main.init({'weights_dir': str(ROOT / 'strategies/racing')})
        results = []
        for track in TRACKS:
            for phase in ('qualifying', 'race'):
                main.init({'weights_dir': str(ROOT / 'strategies/racing')})
                obs = observation(track, phase)
                if track == 'parkland':
                    idx = int(obs['self']['s'] / main._driver.ds) % main._driver.count
                    c = obs['self']
                    c.update(x=float(main._driver.x[idx]), y=float(main._driver.y[idx]))
                    c['yaw'] = math.atan2(float(main._driver.y[(idx+1) % main._driver.count] - c['y']),
                                         float(main._driver.x[(idx+1) % main._driver.count] - c['x']))
                action = main.act(obs)
                legal(action)
                results.append(dict(track=track, phase=phase, action=action))
    finally:
        if saved is None:
            sys.modules.pop('preview_driver', None)
        else:
            sys.modules['preview_driver'] = saved
    return dict(unchanged_files=list(files), numeric_npz_readable=True,
                full_entry_init_and_dispatch=results,
                scope='Synthetic telemetry and straight preview, not a real run or platform resource-budget check.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    baseline = types.ModuleType('racing_reference')
    exec(compile(git_bytes('strategies/racing/preview_driver.py'), '<R07-preview>', 'exec'), baseline.__dict__)
    path = ROOT / 'strategies/racing/preview_driver.py'
    candidate = load_module('racing_candidate', path)
    result = dict(schema='racing-lateral-cut-in-checks-v1', baseline_commit=BASELINE,
                  candidate_preview_lf_sha256=hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                  corridor=check_corridors(baseline, candidate),
                  estimator=check_estimator(candidate), passing=check_passing(baseline, candidate),
                  regression=check_regression(baseline, candidate), sources=check_bundle_sources(),
                  platform_validation='not_run', competition_activation='not_run', formal_matches=0)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
