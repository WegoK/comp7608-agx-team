"""Audit a private Racing export and compare wet recovery on recorded inputs.

No new trajectory, collision, tyre evolution or race result is simulated.
Public replay events are diagnostic evidence, never policy inputs.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import types

import evaluate_racing_strategy as lateral

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '41367f1bf548f71e6826a0f31b6b59e80c584483'
BUNDLE = '4a3466ba671bd53c4c2b46b95b192fbf913b3f692ef79e705929f5da26f0a621'
CAPS = ('target', 'free', 'traffic_cap', 'follow_cap', 'stopping_cap', 'merge_cap', 'pit_entry_cap')


def blob(ref, name):
    return subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{ref}:strategies/racing/{name}'])


def module(name, source):
    result = types.ModuleType(name)
    exec(compile(source, '<' + name + '>', 'exec'), result.__dict__)
    return result


class Policy:
    def __init__(self, label, ref=None):
        folder = ROOT / 'strategies/racing'
        read = lambda name: blob(ref, name) if ref else (folder / name).read_bytes()
        self.preview = module(label + '_preview', read('preview_driver.py'))
        self.main = module(label + '_main', read('agent.py'))
        self.bind(lambda: self.main.init({'weights_dir': str(folder)}))

    def bind(self, callback):
        saved = sys.modules.get('preview_driver')
        sys.modules['preview_driver'] = self.preview
        try:
            return callback()
        finally:
            if saved is None:
                sys.modules.pop('preview_driver', None)
            else:
                sys.modules['preview_driver'] = saved

    def act(self, observation):
        return self.bind(lambda: self.main.act(observation))


def equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
    return a == b


def stub():
    return types.SimpleNamespace(c=dict(lap=1, pit_state=0, front_sliding=False, rear_sliding=False),
        o=dict(phase='race'), gg_active=True, wet=.8, mode='LINE', goal=0., offset=0.,
        v=20., progress=20., s=0., chasing=False, tracking_error=lambda: 0.,
        ctrl=dict(latency=.08), diagnostic={})


def controlled_checks(old, new):
    checks = []

    def compare(obj, original=-5., target=24., speed_at=lambda s: 24.):
        instances = [copy.deepcopy(obj), copy.deepcopy(obj)]
        values = [cls.trajectory_acceleration(p, original, target, speed_at, False, 0.)
                  for cls, p in zip((old.Driver, new.Driver), instances)]
        return values, instances

    for mode in ('LINE', 'DRAFT'):
        p = stub()
        p.mode = mode
        values, instances = compare(p)
        assert values[0] == -5 and 0 < values[1] <= 10
        assert instances[1].diagnostic['wet_reference_recovery']
        for t in (.1, .2, .3, .4):
            assert p.v + values[1] * t <= 24. + 1e-8
        checks.append('wet_' + mode + '_recovers_under_flat_cap')

    values, _ = compare(stub(), speed_at=lambda s: 18. if s > 3. else 24.)
    assert values[1] <= -20  # A nearby lower cap still requires braking.
    checks.append('future_lower_cap_keeps_necessary_braking')

    for label in ('dry', 'first_lap', 'qualifying', 'no_capability', 'pit', 'front_slide',
                  'rear_slide', 'lane_transition', 'tracking_error', 'defend', 'yield_mode',
                  'existing_pass', 'existing_chase'):
        p = stub()
        if label == 'dry': p.wet = .49
        elif label == 'first_lap': p.c['lap'] = 0
        elif label == 'qualifying': p.o['phase'] = 'qualifying'
        elif label == 'no_capability': p.gg_active = False
        elif label == 'pit': p.c['pit_state'] = 1
        elif label == 'front_slide': p.c['front_sliding'] = True
        elif label == 'rear_slide': p.c['rear_sliding'] = True
        elif label == 'lane_transition': p.goal = .26
        elif label == 'tracking_error': p.tracking_error = lambda: .41
        elif label == 'defend': p.mode = 'DEFEND'
        elif label == 'yield_mode': p.mode = 'YIELD'
        elif label == 'existing_pass': p.mode = 'PASS'
        elif label == 'existing_chase': p.chasing = True
        values, _ = compare(p)
        assert values[0] == values[1], (label, values)
        checks.append(label + '_unchanged')
    for label, original, target in (('positive_acceleration', 1., 24.), ('at_target', -5., 20.5)):
        values, _ = compare(stub(), original=original, target=target)
        assert values[0] == values[1]
        checks.append(label + '_unchanged')
    for label, yielding, braking in (('yielding', True, 0.), ('collision_braking', False, 4.)):
        p = stub()
        values = [cls.trajectory_acceleration(p, -5., 24., lambda s: 24., yielding, braking)
                  for cls in (old.Driver, new.Driver)]
        assert values == [-5., -5.]
        checks.append(label + '_unchanged')
    return checks


def replay(path, old, new):
    digest, count, byte_count = hashlib.sha256(), 0, 0
    agreement = faults = changed = eligible = below_target_braking = 0
    per_phase, interventions, fields = {}, [], Counter()
    old_mode_counts, changed_modes = Counter(), Counter()
    stronger_brake = lower_brake = unchanged_outside = 0
    footer = outcome = None
    with path.open('rb') as source:
        header = json.loads(next(source))
        assert header['game_id'] == 'racing' and header['decision_encoding'] == 'original-jsonl-bytes'
        assert header['observation_semantics'] == 'recorded_policy_input'
        assert len(header['participants']) == 1
        own = header['participants'][0]
        seat = own['seat']
        assert own['source_hash'] == BUNDLE
        for line in source:
            row = json.loads(line)
            if 'observation' not in row:
                if row.get('type') == 'export_complete': footer = row
                elif row.get('type') == 'outcome': outcome = row
                continue
            count += 1
            byte_count += len(line)
            digest.update(line)
            o, recorded = row['observation'], row['action']
            c = o['self']
            assert row['seat'] == o['seat'] == seat
            a, b = old.act(o), new.act(o)
            lateral.legal(b)
            agreement += equal(a, recorded)
            faults += bool(row['fault'])
            before, after = old.main._driver, new.main._driver
            da, db = before.diagnostic, after.diagnostic
            for name in CAPS:
                assert equal(da[name], db[name]), (name, o['time'])
            assert a['steer'] == b['steer'] and a['pit_request'] == b['pit_request'] and a['pit_compound'] == b['pit_compound']
            old_mode_counts[before.mode] += 1
            is_changed = not equal(a, b)
            is_eligible = bool(db.get('wet_reference_recovery'))
            eligible += is_eligible
            if not is_eligible:
                assert not is_changed, ('unexpected change', o['time'], before.mode)
                unchanged_outside += 1
            if o['phase'] == 'race' and c['lap'] >= 1 and a['brake'] > .005 and da['target'] > before.v + .5 and not da.get('arrival_control'):
                below_target_braking += 1
            phase = per_phase.setdefault(o['phase'], dict(rows=0, changed_actions=0, wetness_min=1., wetness_max=0.,
                minimum_tyre_temp_c=999., maximum_tyre_wear=0., laps={}, target_sums=Counter()))
            phase['rows'] += 1
            phase['wetness_min'] = min(phase['wetness_min'], o['weather']['track_wetness'])
            phase['wetness_max'] = max(phase['wetness_max'], o['weather']['track_wetness'])
            phase['minimum_tyre_temp_c'] = min(phase['minimum_tyre_temp_c'], min(c['tyre_temp_c']))
            phase['maximum_tyre_wear'] = max(phase['maximum_tyre_wear'], max(c['tyre_wear']))
            phase['laps'].setdefault(c['lap'], dict(first_time_s=o['time']))['last_time_s'] = o['time']
            for key in CAPS: phase['target_sums'][key] += da[key]
            if is_changed:
                assert o['phase'] == 'race' and c['lap'] >= 1 and before.wet >= .5
                assert before.mode in ('LINE', 'DRAFT') and not c['pit_state']
                assert not c['front_sliding'] and not c['rear_sliding']
                assert not da['yielding'] and da['guard_decel'] == 0
                changed += 1
                phase['changed_actions'] += 1
                changed_modes[before.mode] += 1
                lower_brake += b['brake'] < a['brake'] - 1e-8
                stronger_brake += b['brake'] > a['brake'] + 1e-8
                for key in a.keys() | b.keys(): fields[key] += not equal(a.get(key), b.get(key))
                if len(interventions) < 8 and (not interventions or o['time'] - interventions[-1]['time_s'] > 5):
                    interventions.append(dict(time_s=o['time'], lap_zero_based=c['lap'], station_m=c['s'],
                        speed_mps=c['speed_mps'], mode=before.mode, final_target_mps=da['target'],
                        old_brake=a['brake'], new_brake=b['brake'], old_throttle=a['throttle'], new_throttle=b['throttle'],
                        new_model_acceleration_mps2=db.get('arrival_acceleration')))
            if count % 5000 == 0: print('Compared callbacks:', count, flush=True)
    assert footer and footer['complete'] and outcome
    assert (count, byte_count, digest.hexdigest()) == (footer['rows'], footer['raw_bytes'], footer['sha256'])
    assert footer['seat_calls'] == {str(seat): count}
    assert agreement == count
    own_result = next(v for v in outcome['classification'] if v['seat'] == seat)
    for phase in per_phase.values():
        phase['mean_original_caps_mps'] = {k: round(v / phase['rows'], 6) for k, v in phase.pop('target_sums').items()}
    return dict(match_id=header['job_id'], rules_version=header['rules_version'], own_seat_zero_based=seat,
        platform_version_id=own['version_id'], bundle_sha256=own['source_hash'],
        track='parkland', track_length_m=old.main._driver.length,
        integrity=dict(complete=True, decision_rows=count, raw_decision_bytes=byte_count,
                       decision_bytes_sha256=digest.hexdigest(), seat_calls=footer['seat_calls']),
        observed=dict(faults=faults, classification=own_result,
            under_target_braking_calls_before_guard_filter=below_target_braking,
            under_target_note='These calls can include necessary corner braking; they are not all classified as mistakes.'),
        comparison=dict(reference_action_agreement=agreement, candidate_changed_actions=changed,
            new_recovery_model_calls=eligible, unchanged_outside_new_recovery=unchanged_outside,
            changed_modes=changed_modes, changed_fields=fields, lower_brake_calls=lower_brake,
            stronger_brake_calls=stronger_brake, all_recorded_speed_caps_identical=True,
            all_steering_and_pit_decisions_identical=True, examples=interventions,
            scope='Same recorded legal observations; no new trajectories, tyres, visibility, collisions, lap times or race results simulated.'),
        phases=per_phase)


def public_events(path, result):
    kinds = Counter()
    incidents, qualifying = [], None
    with path.open(encoding='utf-8') as source:
        for line in source:
            e = json.loads(line)
            if result['own_seat_zero_based'] not in e['seats']: continue
            kinds[e['phase'] + ':' + e['kind']] += 1
            if e['kind'] == 'incident':
                incidents.append(dict(time_s=e['time'], phase=e['phase'], reason=e['payload']['reason'], station_m=e['payload']['s']))
            elif e['kind'] == 'qualifying_lap':
                qualifying = e['payload']
    result['observed'].update(public_replay_own_event_counts=kinds, public_replay_incidents=incidents, qualifying_lap=qualifying)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for name in ('preview_driver.py', 'design.json', 'weights/plan.npz', 'weights/mechanics.npz'):
        current = (ROOT / 'strategies/racing' / name).read_bytes()
        if not name.endswith('.npz'): current = current.replace(b'\r\n', b'\n')
        assert current == blob(BASELINE, name)
    old, new = Policy('recovery_old', BASELINE), Policy('recovery_new')
    checks = controlled_checks(old.main, new.main)
    result = replay(args.log, old, new)
    public_events(args.events, result)
    preview_old = module('original_r07_preview', lateral.git_bytes('strategies/racing/preview_driver.py'))
    preview_new = new.preview
    preview_checks = dict(corridors=lateral.check_corridors(preview_old, preview_new),
        estimator=lateral.check_estimator(preview_new), passing=lateral.check_passing(preview_old, preview_new),
        callbacks=lateral.check_regression(preview_old, preview_new))
    result.update(schema='racing-wet-recovery-analysis-v1', baseline_commit=BASELINE,
        candidate_agent_lf_sha256=hashlib.sha256((ROOT / 'strategies/racing/agent.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        controlled_guard_cases=checks, preview_regression=preview_checks,
        candidate_platform_validation='not_run', candidate_activation='not_run', candidate_real_matches=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('integrity', 'observed', 'comparison')}, indent=2))
    print('Controlled guards:', len(checks))


if __name__ == '__main__':
    main()
