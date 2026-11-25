"""Attribute recorded Parkland launch gates and last-lap stopping caps.

Frozen legal observations only; this is diagnosis, not a new trajectory.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from evaluate_racing_recovery import Policy, equal

REF = 'ee12c93d76f6ecefc9569776b925139adbd7c860'
PACKAGE = '0ce291ac5e307cb97bb8f04666adbcb286d0abcdaa18c0a20acef31a2a941ee4'


def audit(path, ref=REF, package=PACKAGE, laps=(3,)):
    policy = Policy('constraint_audit', ref)
    original_guard = policy.main.Driver.longitudinal_guard
    original_arrival = policy.main.Driver.trajectory_acceleration
    gates, launch_calls, reductions = Counter(), [], []

    def arrival(self, original, target, speed_at, yielding, braking, launch_goal=None):
        if self.o['phase'] == 'race' and 0 <= self.o['tick']-self.launch_tick <= 480:
            rejected = dict(mode=self.mode not in ('LINE', 'DRAFT'),
                no_positive_request=original <= 0, near_target=target <= self.v+.5,
                speed=self.v >= 20, lane_transition=abs(launch_goal-self.offset) > .25,
                tracking=self.tracking_error() > .4, turning=abs(self.c['heading_error']) > .08,
                slide=self.c['front_sliding'] or self.c['rear_sliding'], yielding=yielding,
                emergency_brake=braking > 0,
                curvature=max(abs(r[1]) for r in self.o['preview']['rows'][:4]) >= .002)
            failures = [name for name, failed in rejected.items() if failed]
            gates.update(failures)
            launch_calls.append(failures)
        return original_arrival(self, original, target, speed_at, yielding, braking, launch_goal=launch_goal)

    def guard(self, goal):
        cap, braking = original_guard(self, goal)
        if self.o['phase'] != 'race' or self.c['lap'] not in laps or cap >= self.diagnostic['follow_cap']-3:
            return cap, braking
        saved = dict(self.diagnostic)
        response = .45 if self.queue_context() else .25
        decel = min(20., .65*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
        if self.gg_active:
            decel = min(decel, max(.5, .70*self.brake_capacity()))
        other_decel = 30*(1-.45*self.wet)
        reasons = []
        try:
            for rival in self.rivals:
                if not .1 < rival['gap_m'] < 400 or not self.stopping_conflict(rival, goal, other_decel, response):
                    continue
                safe = max(0., math.sqrt((decel*response)**2 + 2*decel*(
                    max(0., rival['gap_m']-7.2) + rival['speed_mps']**2/(2*other_decel))) - decel*response)
                if abs(safe-cap) < 1e-6:
                    reasons.append(dict(observed_rival_seat=rival['seat'], gap_m=rival['gap_m'],
                                        lateral_separation_m=rival['dn'], speed_mps=rival['speed_mps']))
            reductions.append(dict(global_time_s=self.o['time'], lap_zero_based=self.c['lap'], station_m=self.c['s'],
                own_speed_mps=self.v, model_own_deceleration_mps2=decel,
                follow_cap_mps=saved['follow_cap'], stopping_cap_mps=cap, limiting_visible_rivals=reasons))
        finally:
            # Extra diagnostic calls must not change the controller's state.
            self.diagnostic = saved
        return cap, braking

    policy.main.Driver.trajectory_acceleration = arrival
    policy.main.Driver.longitudinal_guard = guard
    count = byte_count = agreement = 0
    digest = hashlib.sha256()
    with path.open('rb') as source:
        header = json.loads(next(source))
        assert len(header['participants']) == 1 and header['participants'][0]['source_hash'] == package
        footer = None
        for line in source:
            row = json.loads(line)
            if 'observation' not in row:
                if row.get('type') == 'export_complete': footer = row
                continue
            digest.update(line)
            byte_count += len(line)
            count += 1
            agreement += equal(policy.act(row['observation']), row['action'])
    assert footer and footer['complete'] and (count, byte_count, digest.hexdigest()) == (footer['rows'], footer['raw_bytes'], footer['sha256'])
    assert agreement == count
    by_lap={}
    for lap in laps:
        entries=[r for r in reductions if r['lap_zero_based']==lap]
        rivals=Counter(r['observed_rival_seat'] for e in entries for r in e['limiting_visible_rivals'])
        by_lap[str(lap)]=dict(incremental_stopping_reductions_gt_3mps=len(entries),limiting_visible_rival_seat_counts=rivals,
            samples=entries[::max(1,len(entries)//4)][:4])
    return dict(schema='racing-constraint-audit-v2', match_id=header['job_id'], source_commit=ref,
        recorded_action_agreement=agreement, decision_sha256=digest.hexdigest(),
        launch_window=dict(calls=len(launch_calls), overlapping_rejection_counts=gates,
                           only_mode_rejected=sum(v == ['mode'] for v in launch_calls), all_listed_gates_clear=sum(not v for v in launch_calls)),
        tracked_laps_zero_based=list(laps),by_lap=by_lap,
        fourth_lap=by_lap.get('3'),
        note='Own recorded legal rival observations only. A triggered guard is not proof of unnecessary braking; no policy change or new race simulated.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-ref', default=REF)
    p.add_argument('--bundle-sha256', default=PACKAGE)
    p.add_argument('--laps', default='3', help='Zero-based lap indices, separated by commas')
    args = p.parse_args()
    result = audit(args.log, args.source_ref, args.bundle_sha256, tuple(int(v) for v in args.laps.split(',')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('recorded_action_agreement', 'launch_window')}))


if __name__ == '__main__':
    main()
