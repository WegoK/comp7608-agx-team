"""Paired single-car speed-control replay on the downloaded public centerlines.

Usage: python3 bench.py --maps ../racing_maps --baseline <official driver dir>
"""
import argparse
import bisect
import csv
import importlib.util
import json
import math
import statistics
from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = [# label, wetness, tyre wear, forward accel, full braking deceleration, response lag
    ('dry', 0.0, 0.05, 5.5, 10., 0.1),
    ('damp', 0.3, 0.2, 4.7, 8.0, 0.2),
    ('wet', 0.7, 0.3, 3.8, 6.8, 0.3),
]
# Prespecified stress tests after selecting the candidate. No code retuning.
STRESS = [
    ('dry_weak_brake', 0.0, 0.05, 5.5, 7.0, 0.35),
    ('wet_slow_response', 0.7, 0.35, 3.2, 5.3, 0.45),
]
DT = 0.1


def driver(folder):
    spec = importlib.util.spec_from_file_location('driver', folder / 'agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.init({})
    return module


class Track:
    def __init__(self, path):
        data = json.loads(path.read_text())
        self.name = data['track_id']
        self.length = data['length_m']
        self.rows = data['centerline']
        self.stations = [r['s_m'] for r in self.rows]

    def row(self, station):
        s = station % self.length
        i = bisect.bisect_right(self.stations, s) - 1
        j = (i + 1) % len(self.stations)
        a, b = self.rows[i], self.rows[j]
        ds = (self.stations[j] if j else self.length) - self.stations[i]
        f = (s-self.stations[i]) / ds
        fields = ('curvature_1pm', 'grade_dz_ds', 'half_width_left_m', 'half_width_right_m')
        return [a[k] + f * (b[k] - a[k]) for k in fields]


def run(track, folder, scenario):
    label, wet, wear, accel, brake, lag = scenario
    policy = driver(folder)
    car = dict(s=0., speed_mps=0., n=0., heading_error=0., fuel_kg=30.,
               tyre_wear=[wear]*4, tyre_temp_c=[85]*4, ers_soc_mj=0.,
               pit_state=0, lap=1, compound='soft', pit_stops=0)
    cols = ['distance_m', 'curvature_1pm', 'grade', 'half_width_left_m', 'half_width_right_m']
    o = dict(self=car, preview={'rows': [], 'columns': cols}, weather={'track_wetness':wet},
             phase='qualifying', rivals=[], laps_total=1)
    t = s = speed = a = excess = 0.
    demand = []
    limit = 10 * (1-.45*wet) * max(.65, 1-.4*wear)
    while s < track.length:
        car.update(s=s, speed_mps=speed)
        o['preview']['rows'] = [[d, *track.row(s+d)] for d in range(15, 361, 15)]
        cmd = policy.act(o)
        kappa, grade, _, _ = track.row(s)
        lat = speed ** 2 * abs(kappa)
        demand.append(lat)
        excess += (lat > limit) * DT
        requested = accel*cmd['throttle'] - brake*cmd['brake'] - .00045*speed**2 - 9.81*grade
        a += (requested-a) * (1 - math.exp(-DT/lag))
        next_v = max(0., speed+DT*a)
        ds = (speed+next_v)*DT/2
        if s+ds >= track.length:
            t += DT * (track.length-s)/ds
            break
        t += DT
        s += ds
        speed = next_v
        if t > 1200:
            raise ValueError('Failed to finish within 1200 s')
    return dict(seconds=round(t,3), over_budget_s=round(excess,2), p95_lateral=round(statistics.quantiles(demand,n=100)[94],3), peak_lateral=round(max(demand),3))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--maps', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, default=HERE/'preview_experiment')
    p.add_argument('--out', type=Path, default=HERE/'results')
    p.add_argument('--stress', action='store_true')
    args = p.parse_args()
    scenarios = STRESS if args.stress else SCENARIOS
    rows = []
    hashes = {}
    for path in sorted(args.maps.glob('racing-*.mapdata.json')):
        hashes[path.name]=sha256(path.read_bytes()).hexdigest()
        track = Track(path)
        for scenario in scenarios:
            for tag, folder in [('baseline',args.baseline),('candidate',args.candidate)]:
                data=run(track,folder,scenario)
                rows.append(dict(track=track.name,scenario=scenario[0],driver=tag,**data))
    if len(rows) != 3 * len(scenarios) * 2:
        raise ValueError('Expected complete paired runs on three tracks')
    args.out.mkdir(exist_ok=True,parents=True)
    with (args.out/'racing.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
    for tag, folder in [('baseline',args.baseline),('candidate',args.candidate)]:
        hashes[tag+'.agent.py']=sha256((folder/'agent.py').read_bytes()).hexdigest()
    (args.out/'inputs.json').write_text(json.dumps(dict(sha256=hashes,scenarios=scenarios,dt=DT),indent=2)+'\n')
    for r in rows: print(r)

if __name__=='__main__':
    main()
