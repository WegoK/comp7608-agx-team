"""Review a frozen Parkland Racing match without changing its strategy.

Only aggregate own-seat evidence is saved. The public event stream is read
after policy replay and never supplied to a controller.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

from evaluate_racing_recovery import CAPS, Policy, ROOT, blob, equal, lateral


def verify_package(manifest, archive, control_ref):
    ref = manifest['git_sha']
    assert manifest['game'] == 'racing'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['zip_sha256']
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None and set(z.namelist()) == set(manifest['files'])
        for name, digest in manifest['files'].items():
            source = z.read(name)
            assert source == blob(ref, name)
            assert hashlib.sha256(source).hexdigest() == digest
            if name != 'agent.py':
                current = (ROOT / 'strategies/racing' / name).read_bytes()
                if not name.endswith('.npz'):
                    current = current.replace(b'\r\n', b'\n')
                assert current == source
                if control_ref:
                    assert blob(control_ref, name) == source


def group():
    return dict(rows=0, modes=Counter(), weather_conditions=Counter(),
                wetness_min=1., wetness_max=0., wetness_sum=0., min_tyre_temp_c=999.,
                max_tyre_wear=0., min_fuel_kg=999., speed_sum=0., cap_sums=Counter(),
                incremental_cap_reductions_gt_3mps=Counter(), active_guard_braking_calls=0,
                wet_recovery_model_calls=0, changed_vs_control_calls=0)


def update(q, obs, player, changed):
    c, d = obs['self'], player.diagnostic
    q['rows'] += 1
    q['modes'][player.mode] += 1
    q['weather_conditions'][obs['weather']['condition']] += 1
    wet = obs['weather']['track_wetness']
    q['wetness_min'] = min(q['wetness_min'], wet)
    q['wetness_max'] = max(q['wetness_max'], wet)
    q['wetness_sum'] += wet
    q['min_tyre_temp_c'] = min(q['min_tyre_temp_c'], min(c['tyre_temp_c']))
    q['max_tyre_wear'] = max(q['max_tyre_wear'], max(c['tyre_wear']))
    q['min_fuel_kg'] = min(q['min_fuel_kg'], c['fuel_kg'])
    q['speed_sum'] += c['speed_mps']
    q['wet_recovery_model_calls'] += bool(d.get('wet_reference_recovery'))
    q['changed_vs_control_calls'] += changed
    for name in CAPS:
        q['cap_sums'][name] += d[name]
    for label, earlier, later in (('traffic', 'free', 'traffic_cap'),
                                  ('following', 'traffic_cap', 'follow_cap'),
                                  ('stopping_guard', 'follow_cap', 'stopping_cap'),
                                  ('pit_merge', 'stopping_cap', 'merge_cap'),
                                  ('pit_entry', 'merge_cap', 'pit_entry_cap')):
        q['incremental_cap_reductions_gt_3mps'][label] += d[later] < d[earlier] - 3.
    q['active_guard_braking_calls'] += d.get('guard_decel', 0.) > 0.


def finish_group(q):
    n = q['rows']
    q['mean_wetness'] = round(q.pop('wetness_sum') / n, 6)
    q['mean_speed_mps'] = round(q.pop('speed_sum') / n, 6)
    q['mean_model_caps_mps'] = {key: round(value / n, 6) for key, value in q.pop('cap_sums').items()}


def review(path, manifest, control_ref):
    policy = Policy('review_actual', manifest['git_sha'])
    control = Policy('review_previous_control', control_ref) if control_ref else None
    count = byte_count = agreement = faults = changes = 0
    digest, phases, laps = hashlib.sha256(), {}, {}
    fields = Counter()
    samples = []
    previous = None
    boundaries = {}
    race_start = None
    footer = outcome = None
    launch = []
    with path.open('rb') as source:
        header = json.loads(next(source))
        assert header['game_id'] == 'racing' and header['decision_encoding'] == 'original-jsonl-bytes'
        assert header['observation_semantics'] == 'recorded_policy_input' and len(header['participants']) == 1
        own = header['participants'][0]
        assert own['source_hash'] == manifest['zip_sha256']
        seat = own['seat']
        for line in source:
            row = json.loads(line)
            if 'observation' not in row:
                if row.get('type') == 'export_complete': footer = row
                elif row.get('type') == 'outcome': outcome = row
                continue
            count += 1
            byte_count += len(line)
            digest.update(line)
            obs, recorded = row['observation'], row['action']
            c, phase = obs['self'], obs['phase']
            assert row['seat'] == obs['seat'] == seat
            assert abs(obs['track_length_m'] - policy.main._driver.length) <= .5, 'This review supports the frozen Parkland controller only.'
            proposed = policy.act(obs)
            lateral.legal(proposed)
            agreement += equal(proposed, recorded)
            faults += bool(row['fault'])
            player = policy.main._driver
            changed = False
            if control:
                before = control.act(obs)
                changed = not equal(proposed, before)
                changes += changed
                for name in CAPS:
                    assert equal(player.diagnostic[name], control.main._driver.diagnostic[name]), name
                if changed:
                    assert player.diagnostic.get('wet_reference_recovery')
                    for key in proposed.keys() | before.keys():
                        fields[key] += not equal(proposed.get(key), before.get(key))
                    if len(samples) < 5 and (not samples or obs['time'] - samples[-1]['global_time_s'] > 5):
                        samples.append(dict(global_time_s=obs['time'], lap_zero_based=c['lap'],
                            station_m=c['s'], speed_mps=c['speed_mps'], mode=player.mode,
                            target_mps=player.diagnostic['target'], previous_brake=before['brake'],
                            actual_version_brake=proposed['brake']))
            update(phases.setdefault(phase, group()), obs, player, changed)
            if phase == 'race':
                race_start = obs['time'] if race_start is None else race_start
                lap = laps.setdefault(c['lap'], group())
                update(lap, obs, player, changed)
                if obs['time'] - race_start <= 10. and (not launch or obs['time'] - launch[-1]['global_time_s'] >= .99):
                    launch.append(dict(global_time_s=obs['time'], elapsed_s=round(obs['time']-race_start, 4),
                        live_position=obs['position'], distance_m=c['distance'], speed_mps=c['speed_mps'],
                        throttle=proposed['throttle'], brake=proposed['brake'], mode=player.mode,
                        target_mps=player.diagnostic['target'], free_mps=player.diagnostic['free'],
                        stopping_cap_mps=player.diagnostic['stopping_cap']))
                if previous is not None:
                    distance, tick_time = previous
                    length = obs['track_length_m']
                    for number in range(1, obs['laps_total']):
                        boundary = number * length
                        if distance < boundary <= c['distance']:
                            fraction = (boundary - distance) / (c['distance'] - distance)
                            boundaries[number] = tick_time + fraction * (obs['time']-tick_time)
                previous = c['distance'], obs['time']
            if count % 5000 == 0:
                print('Reviewed callbacks:', count, flush=True)
    assert footer and footer['complete'] and outcome
    assert (count, byte_count, digest.hexdigest()) == (footer['rows'], footer['raw_bytes'], footer['sha256'])
    assert footer['seat_calls'] == {str(seat): count} and agreement == count
    result = next(c for c in outcome['classification'] if c['seat'] == seat)
    for q in phases.values(): finish_group(q)
    for number, q in laps.items():
        finish_group(q)
        start = race_start if number == 0 else boundaries.get(number)
        end = boundaries.get(number+1)
        if number+1 == result['laps'] and result['status'] == 'finished':
            end = race_start + result['total_time_s']
        if start is not None and end is not None:
            q['estimated_lap_time_s'] = round(end-start, 4)
    return dict(schema='racing-frozen-match-review-v1', match_id=header['job_id'],
        rules_version=header['rules_version'], own_seat_zero_based=seat,
        platform_version_id=own['version_id'], bundle_sha256=own['source_hash'],
        source_commit=manifest['git_sha'], track='parkland', race_start_global_s=race_start,
        integrity=dict(complete=True, decision_rows=count, raw_decision_bytes=byte_count,
            decision_bytes_sha256=digest.hexdigest(), seat_calls=footer['seat_calls']),
        observed=dict(faults=faults, final_classification=result, match_end_reason=outcome['reason']),
        frozen_policy_replay=dict(action_agreement=agreement, tolerance_absolute=1e-8),
        same_observation_previous_control=dict(source_commit=control_ref, changed_actions=changes,
            changed_fields=fields, all_model_caps_identical=True if control else None, examples=samples,
            scope='Decision comparison on this version\'s historical legal observations; NOT a paired full race or simulated outcome.'),
        phases=phases, race_laps_zero_based=laps, launch_samples=launch,
        lap_time_note='Estimated from linear interpolation of recorded distance crossings, with observed finish total for the last boundary; not all official lap timing. Lap 1 includes the grid start.',
        strategy_changes=False, assistant_uploaded_or_activated=False)


def add_events(path, result):
    own, start = result['own_seat_zero_based'], result['race_start_global_s']
    counts, selected = Counter(), []
    with path.open(encoding='utf-8') as source:
        for line in source:
            e = json.loads(line)
            if own not in e['seats']: continue
            counts[e['phase'] + ':' + e['kind']] += 1
            if e['kind'] in ('incident', 'overtake', 'fastest_lap', 'qualifying_lap', 'finished'):
                payload = e['payload']
                entry = dict(kind=e['kind'], global_time_s=e['time'], phase=e['phase'])
                if e['phase'] == 'race': entry['elapsed_s'] = round(e['time']-start, 4)
                if e['kind'] == 'incident': entry.update(reason=payload['reason'], station_m=payload['s'])
                elif e['kind'] == 'overtake': entry.update(own_passed_other=payload['passer']==own, event_position=payload['position'])
                else: entry['payload'] = payload
                selected.append(entry)
    result['public_event_review'] = dict(own_event_counts=counts, selected_events=selected,
        classification_note='Final outcome classification is authoritative. A finished event position may differ from final ranking by completed laps.',
        policy_input=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--control-ref')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    verify_package(manifest, args.archive, args.control_ref)
    result = review(args.log, manifest, args.control_ref)
    add_events(args.events, result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(integrity=result['integrity'], observed=result['observed'],
                         action_agreement=result['frozen_policy_replay']['action_agreement'],
                         changed_vs_previous_control=result['same_observation_previous_control']['changed_actions']), indent=2))


if __name__ == '__main__':
    main()
