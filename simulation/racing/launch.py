"""Fixed-progress lateral launch test from the Crossover wet-race telemetry."""
import argparse
import copy
import json
import math
from pathlib import Path
from verify import policy


def run(rows, label, gain):
    m = policy(label)
    first = rows[0]['observation']['self']
    n, heading, rate, steer = first['n'], first['heading_error'], first['yaw_rate'], first['steer']
    offsets = [n]
    for row, following in zip(rows, rows[1:]):
        o = copy.deepcopy(row['observation'])
        observed = row['observation']['self']
        c = o['self']
        nxt = following['observation']['self']
        dt = following['observation']['time']-o['time']
        road_heading = c['yaw']-c['heading_error']
        next_road_heading = nxt['yaw']-nxt['heading_error']
        dh = math.remainder(next_road_heading-road_heading, math.tau)
        delta = n - observed['n']
        c.update(x=c['x']-math.sin(road_heading)*delta, y=c['y']+math.cos(road_heading)*delta,
                 n=n, yaw=road_heading+heading, heading_error=heading, yaw_rate=rate, steer=steer)
        for rival in o['rivals']:
            rival['dn']-=delta
        action = m.act(o)
        # Racing exports store steering angle (rad) in self.steer; the normalized
        # action is applied before the next telemetry snapshot.
        steer = m._driver.p['steer_max']*action['steer']
        rate = gain * action['steer'] * c['speed_mps']
        h_next = heading + dt * rate - dh
        n += dt*(c['speed_mps']+nxt['speed_mps'])/2 * math.sin((heading+h_next)/2)
        heading = h_next
        offsets.append(n)
    error = [(sim-r['observation']['self']['n']) for sim,r in zip(offsets,rows)]
    return dict(final_n_m=round(n,3),max_abs_n_m=round(max(map(abs,offsets)),3),
                max_departure_from_start_m=round(max(abs(v-offsets[0]) for v in offsets),3),
                second_half_vs_recorded_rmse_m=round(math.sqrt(sum(e*e for e in error[len(error)//2:])/len(error[len(error)//2:])),3))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('export',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    rows=[]
    for line in args.export.open():
        r=json.loads(line)
        if r.get('observation',{}).get('phase')=='race':rows.append(r)
    assert 160 <= len(rows)<=170 and abs(rows[0]['observation']['track_length_m']-5803.167)<.001
    # Fit response on the first half only: yaw_rate(t+1) ~ gain * steer(t) * speed(t).
    pairs=[(r['action']['steer']*r['observation']['self']['speed_mps'],s['observation']['self']['yaw_rate'])
           for r,s in zip(rows[:len(rows)//2-1],rows[1:len(rows)//2])]
    fit=sum(x*y for x,y in pairs)/sum(x*x for x,y in pairs)
    summary=dict(fit_gain=fit,fit_pairs=len(pairs),race_rows=len(rows),seconds=rows[-1]['observation']['time']-rows[0]['observation']['time'],
                 recorded_final_n_m=rows[-1]['observation']['self']['n'],variants=[])
    for factor in (.75,1.,1.25):
        summary['variants'].append(dict(gain_scale=factor,baseline=run(rows,'baseline',fit*factor),
                                         candidate=run(rows,'candidate',fit*factor)))
    args.output.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))

if __name__=='__main__':
    main()
