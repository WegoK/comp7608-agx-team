"""Re-evaluate actual race actions and the track guard; requires NumPy."""
import argparse
from collections import Counter
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent


def policy(tag):
    repo = HERE.parents[1]
    p = repo / 'strategies/local/racing/R01' if tag == 'candidate' else repo.parent / 'AGX_Local_Lab/racing_v1'
    if tag=='candidate':
        sys.path.insert(0,str(p))
    spec=importlib.util.spec_from_file_location('racing_'+tag,p/'agent.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.init({'weights_dir':str(p)})
    return mod


def evaluate(path, output, max_per_phase=None):
    base, candidate=policy('baseline'),policy('candidate')
    by_phase, lengths, upstream= {},set(), None
    digest=sha256(); end=None
    for line in path.open('rb'):
        digest.update(line)
        row=json.loads(line)
        if row.get('type')=='metadata':
            upstream=row['participants'][0]['source_hash']
        if row.get('type')=='export_complete':
            end=row
        if 'observation' not in row:
            continue
        o=row['observation'];phase=o['phase'];counts=by_phase.setdefault(phase,Counter());counts['total_rows']+=1
        if max_per_phase and counts['total_rows']>max_per_phase:
            continue
        action=base.act(o)
        new=candidate.act(o)
        for k,v in new.items():
            if isinstance(v,(int,float)) and not math.isfinite(v):
                raise ValueError('non-finite action')
        if not (0<=new['throttle']<=1 and 0<=new['brake']<=1 and -1<=new['steer']<=1):raise ValueError('controls out of range')
        # Missing optional actions have the neutral default False.
        same=lambda a,b: all(abs(float(a.get(k,False))-float(b.get(k,False))) <= 1e-7 if isinstance(v,(int,float)) else a.get(k,None)==b.get(k,None) for k,v in a.items())
        counts['baseline_matches_recorded']+=same(action,row['action'])
        counts['candidate_matches_baseline']+=same(action,new)
        counts['evaluated']+=1
        lengths.add(o['track_length_m'])
    assert end and end['complete'] and sum(c['total_rows'] for c in by_phase.values())==end['rows']
    result=dict(export_file_sha256=digest.hexdigest(),source_hash=upstream,public_track_lengths=sorted(lengths),
                plan_length=base._driver.length,counts=by_phase,first_n_per_phase=max_per_phase,comparison_tolerance=1e-7)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('export',type=Path);p.add_argument('out',type=Path);p.add_argument('--first',type=int)
    a=p.parse_args();evaluate(a.export,a.out,a.first)
