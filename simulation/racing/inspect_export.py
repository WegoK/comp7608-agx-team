"""Evaluate action compatibility on recorded states. Does not simulate new outcomes."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from bench import driver, HERE


def summarize(path, folders):
    policies = {name: driver(folder) for name, folder in folders.items()}
    stats, samples = {}, {}
    phases, faults = Counter(), 0
    complete, classification, seat = None, None, None
    for line in path.open():
        row = json.loads(line)
        if row.get('type') == 'metadata':
            seat = row['participants'][0]['seat']
        if row.get('type') == 'outcome':
            classification = next(v for v in row['classification'] if v['seat']==seat)
        if row.get('type') == 'export_complete':
            complete = row
        if 'observation' not in row:
            continue
        assert row['seat'] == seat
        o, actual = row['observation'], row['action']
        phase, own = o['phase'], o['self']
        phases[phase] += 1
        faults += int(bool(row['fault']))
        point = {'time':o['time'], 'n':own['n'], 'heading_error':own['heading_error'],
                 'speed_mps':own['speed_mps'], 'wetness':o['weather']['track_wetness'],
                 'remaining_preview_half_width_m':min(o['preview']['rows'][0][3:5])-abs(own['n']),
                 'recorded_steer':actual['steer']}
        for label, policy in policies.items():
            proposed = policy.act(o)
            assert all(math.isfinite(proposed[k]) for k in ['throttle','brake','steer'])
            assert 0 <= proposed['brake'] <= 1 and 0 <= proposed['throttle'] <= 1 and -1 <= proposed['steer'] <= 1
            counts = stats.setdefault(phase, {}).setdefault(label, Counter())
            counts['calls'] += 1
            counts['ers_diff'] += proposed['ers_mode'] != actual['ers_mode']
            counts['steer_diff_gt_1e-5'] += abs(proposed['steer']-actual['steer']) > 1e-5
            point[label+'_steer'] = round(proposed['steer'],6)
        stats[phase].setdefault('recorded_ers_modes',Counter())[actual['ers_mode']]+=1
        sample=samples.setdefault(phase, {})
        sample.setdefault('first',point)
        sample['last']=point
    assert complete and complete['complete'] and complete['rows']==sum(phases.values())
    return dict(file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),seat=seat,rows=phases,
                faults=faults,stats=stats,first_last=samples,classification={k:classification[k] for k in ('position','status','laps','total_time_s','qualifying_position')})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path);p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--candidate',default=HERE/'preview_experiment',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=summarize(args.input, {'starter':args.baseline, 'candidate':args.candidate})
    args.output.write_text(json.dumps(result,indent=2)+'\n')
