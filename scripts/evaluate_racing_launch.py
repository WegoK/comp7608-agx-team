"""Compare a bounded launch response with frozen Racing policies.

Recorded-input comparison is not a new race. The separate one-dimensional
following model has explicit hypothetical dynamics and no tyre/collision engine.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path

from evaluate_racing_recovery import CAPS, Policy, ROOT, blob, controlled_checks, equal, stub, lateral

BASELINE = 'd6268a7f50adcfec63b79ca706c627401ae2af2d'
SOURCES = {
    'bfaeb741839032f0bffc0671592ac48860374ed44213f6a89aff3cd7d5a2e4a9': BASELINE,
    '4a3466ba671bd53c4c2b46b95b192fbf913b3f692ef79e705929f5da26f0a621': '41367f1bf548f71e6826a0f31b6b59e80c584483',
}


def launch_stub():
    p = stub()
    p.c.update(lap=0, heading_error=0.)
    p.o.update(tick=0, preview=dict(rows=[[15., 0., 0., 7., 7.]] * 4))
    p.launch_tick = p.launch_distance = 0
    p.v, p.progress, p.s = 3., 3., 0.
    return p


def launch_checks(old, new):
    cases = []
    for mode in ('LINE', 'DRAFT'):
        p = launch_stub()
        p.mode, p.goal = mode, 3.  # Local grid goal overrides the tactical one.
        before = old.Driver.trajectory_acceleration(p, 6., 12., lambda s: 12., False, 0.)
        after = new.Driver.trajectory_acceleration(p, 6., 12., lambda s: 12., False, 0., launch_goal=0.)
        assert before == 6. and 6. < after <= 8.1
        assert p.diagnostic['launch_response']
        cases.append(mode + '_bounded_response_uses_actual_grid_goal')
    for label in ('expired_time', 'too_far', 'too_fast', 'turning', 'curved_preview', 'lane_transition',
                  'tracking_error', 'sliding_front', 'sliding_rear', 'pit', 'qualifying', 'PASS', 'DEFEND'):
        p = launch_stub()
        goal = 0.
        if label == 'expired_time': p.o['tick'] = 481
        elif label == 'too_far': p.s = 100.01
        elif label == 'too_fast': p.v = 20.
        elif label == 'turning': p.c['heading_error'] = .081
        elif label == 'curved_preview': p.o['preview']['rows'] = [[15., .0021, 0., 7., 7.]] * 4
        elif label == 'lane_transition': goal = .251
        elif label == 'tracking_error': p.tracking_error = lambda: .401
        elif label == 'sliding_front': p.c['front_sliding'] = True
        elif label == 'sliding_rear': p.c['rear_sliding'] = True
        elif label == 'pit': p.c['pit_state'] = 1
        elif label == 'qualifying': p.o['phase'] = 'qualifying'
        else: p.mode = label
        a = new.Driver.trajectory_acceleration(p, 6., 12., lambda s: 12., False, 0., launch_goal=goal)
        assert a == 6., (label, a)
        cases.append(label + '_unchanged')
    for label, yielding, guard in (('yielding', True, 0.), ('emergency_guard', False, 5.)):
        p = launch_stub()
        assert new.Driver.trajectory_acceleration(p, 6., 12., lambda s: 12., yielding, guard, launch_goal=0.) == 6.
        cases.append(label + '_unchanged')
    for label, original, target, future in (
            ('already_at_cap', 6., 3.4, 12.), ('baseline_rollout_not_feasible', 6., 5., 12.),
            ('upcoming_lower_cap', 6., 12., 4.), ('negative_request', -2., 12., 12.)):
        p = launch_stub()
        assert new.Driver.trajectory_acceleration(p, original, target, lambda s: future, False, 0., launch_goal=0.) == original
        cases.append(label + '_unchanged')
    return cases


def following_model(cls, candidate, gap0, brake_capacity, lag, stop_at, response):
    """A hypothetical flat-road scalar stress test, NOT a calibrated simulator."""
    dt, elapsed = 1/30, 0.
    p = launch_stub()
    own_s = own_v = own_a = lead_s = lead_v = 0.
    minimum_gap, maximum_cap_overshoot = gap0, 0.
    for tick in range(240):
        gap = gap0 + lead_s - own_s
        other_decel = 24.
        cap = max(0., math.sqrt((brake_capacity*response)**2 + 2*brake_capacity*(
            max(0., gap-7.2) + lead_v**2/(2*other_decel))) - brake_capacity*response)
        target = min(35., cap)
        guard = brake_capacity if own_v > cap+.5 else 0.
        original = max(-70., min(40., 1.6*(target-own_v)))
        p.o['tick'] = tick*2
        p.v, p.progress, p.s = own_v, max(own_v, 1.), own_s
        p.diagnostic = {}
        args = (p, original, target, lambda s: 35., False, guard)
        acceleration = cls.trajectory_acceleration(*args, launch_goal=0.) if candidate else cls.trajectory_acceleration(*args)
        if guard: acceleration = min(acceleration, -guard)
        request = max(-brake_capacity, min(8., acceleration))
        own_a += (request-own_a)*(1-math.exp(-dt/lag))
        next_own = max(0., own_v+own_a*dt)
        own_s += (own_v+next_own)*dt/2
        own_v = next_own
        lead_a = -18. if stop_at is not None and elapsed >= stop_at else (4. if lead_v < 20. else 0.)
        next_lead = max(0., lead_v+lead_a*dt)
        lead_s += (lead_v+next_lead)*dt/2
        lead_v = next_lead
        minimum_gap = min(minimum_gap, gap0+lead_s-own_s)
        maximum_cap_overshoot = max(maximum_cap_overshoot, own_v-cap)
        elapsed += dt
    return dict(own_distance_m=round(own_s, 6), min_centre_gap_m=round(minimum_gap, 6),
                max_cap_overshoot_mps=round(maximum_cap_overshoot, 6))


def scalar_stress(old, new):
    rows = []
    for gap in (12., 20.):
        for braking in (8., 12.):
            for lag in (.08, .2, .35):
                for stop in (None, 2.5, 5.):
                    for response in (.25, .45):
                        a = following_model(old.Driver, False, gap, braking, lag, stop, response)
                        b = following_model(new.Driver, True, gap, braking, lag, stop, response)
                        rows.append(dict(initial_gap_m=gap, own_brake_mps2=braking, lag_s=lag,
                                         leader_brakes_at_s=stop, response_s=response, baseline=a, candidate=b))
    inherited = [r for r in rows if r['baseline']['min_centre_gap_m'] <= 5.45]
    introduced = [r for r in rows if r['candidate']['min_centre_gap_m'] <= 5.45 < r['baseline']['min_centre_gap_m']]
    assert not introduced, 'Launch introduces overlap in a baseline-clear scalar case: do not package.'
    assert all(r['candidate']['min_centre_gap_m'] >= r['baseline']['min_centre_gap_m'] for r in inherited)
    return dict(cases=rows, count=len(rows), baseline_model_overlaps=len(inherited),
        candidate_model_overlaps=sum(r['candidate']['min_centre_gap_m'] <= 5.45 for r in rows),
        introduced_model_overlaps=len(introduced),
        criterion='No new overlap in baseline-clear model cases; inherited overlap cases must not have smaller minimum gaps. This is relative regression, not an absolute collision-free claim.',
        assumptions='8s scalar flat-road model; 30Hz; own acceleration cap 8m/s2; lead accelerates at 4m/s2 to 20m/s then brakes at 18m/s2 in stop cases; assumed centre-overlap threshold 5.45m. Parameters are hypothetical stress choices, not fitted AGX physics.',
        limitations='No tyres, lateral motion, turbo/gear model, rivals reacting, or official collision adjudication; no lap-time or finish-position inference.')


def logged_comparison(path):
    baseline, candidate = Policy('launch_baseline', BASELINE), Policy('launch_candidate')
    rows = raw = agreement = changed = 0
    digest, fields, phases, examples = hashlib.sha256(), Counter(), {}, []
    first_race = None
    with path.open('rb') as source:
        header = json.loads(next(source))
        assert len(header['participants']) == 1 and header['decision_encoding'] == 'original-jsonl-bytes'
        participant = header['participants'][0]
        actual_ref = SOURCES[participant['source_hash']]
        actual = baseline if actual_ref == BASELINE else Policy('launch_actual', actual_ref)
        footer = None
        for line in source:
            r = json.loads(line)
            if 'observation' not in r:
                if r.get('type') == 'export_complete': footer = r
                continue
            rows += 1
            raw += len(line)
            digest.update(line)
            o = r['observation']
            a, b = baseline.act(o), candidate.act(o)
            agreement += equal(a if actual is baseline else actual.act(o), r['action'])
            lateral.legal(b)
            before, after = baseline.main._driver, candidate.main._driver
            for key in CAPS: assert equal(before.diagnostic[key], after.diagnostic[key]), key
            is_changed = not equal(a, b)
            if o['phase'] == 'race' and first_race is None: first_race = o['tick']
            q = phases.setdefault(o['phase'], dict(rows=0, changed=0))
            q['rows'] += 1
            if is_changed:
                changed += 1
                q['changed'] += 1
                assert after.diagnostic.get('launch_response')
                assert o['phase'] == 'race' and o['self']['lap'] == 0 and 0 <= o['tick']-first_race <= 480
                assert before.diagnostic['guard_decel'] == 0 and not before.diagnostic['yielding']
                assert b['brake'] <= a['brake'] + 1e-8
                for key in a: fields[key] += not equal(a[key], b[key])
                if len(examples) < 5 and (not examples or o['time']-examples[-1]['global_time_s'] >= .8):
                    examples.append(dict(global_time_s=o['time'], elapsed_race_s=(o['tick']-first_race)/60,
                        speed_mps=o['self']['speed_mps'], target_mps=before.diagnostic['target'],
                        old_throttle=a['throttle'], new_throttle=b['throttle'], old_ers=a['ers_mode'], new_ers=b['ers_mode']))
            if rows % 5000 == 0: print(path.stem, rows, flush=True)
    assert footer and footer['complete'] and (rows, raw, digest.hexdigest()) == (footer['rows'], footer['raw_bytes'], footer['sha256'])
    assert footer['seat_calls'] == {str(participant['seat']): rows} and agreement == rows
    return dict(match_id=header['job_id'], actual_source_commit=actual_ref, actual_bundle_sha256=participant['source_hash'],
        complete=True, rows=rows, raw_decision_bytes=raw, decision_sha256=digest.hexdigest(),
        frozen_actual_action_agreement=agreement, changes_from_d6268a7f50ad=changed,
        unchanged_actions=rows-changed, all_model_speed_caps_identical=True,
        phases=phases, changed_fields=fields, examples=examples,
        scope='Same historical legal observations; new trajectories and race results are not simulated.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for name in ('preview_driver.py', 'design.json', 'weights/plan.npz', 'weights/mechanics.npz'):
        data = (ROOT / 'strategies/racing' / name).read_bytes()
        if not name.endswith('.npz'): data = data.replace(b'\r\n', b'\n')
        assert data == blob(BASELINE, name)
    old, new = Policy('launch_guard_old', BASELINE), Policy('launch_guard_new')
    guards = launch_checks(old.main, new.main)
    legacy = Policy('launch_legacy_guard_control', SOURCES['4a3466ba671bd53c4c2b46b95b192fbf913b3f692ef79e705929f5da26f0a621'])
    old_guards = controlled_checks(legacy.main, new.main)
    stress = scalar_stress(old.main, new.main)
    print('Controlled guards', len(guards), 'legacy guards', len(old_guards), 'scalar cases', stress['count'], flush=True)
    results = [logged_comparison(path) for path in args.log]
    result = dict(schema='racing-bounded-launch-v1', baseline_commit=BASELINE,
        candidate_agent_lf_sha256=hashlib.sha256((ROOT / 'strategies/racing/agent.py').read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        launch_guard_cases=guards, legacy_arrival_guard_cases=old_guards,
        scalar_stress=stress, logged_comparisons=results,
        platform_validation='not_run', competition_activation='not_run', candidate_real_matches=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps([dict(match_id=r['match_id'], rows=r['rows'], changed=r['changes_from_d6268a7f50ad']) for r in results], indent=2))


if __name__ == '__main__':
    main()
