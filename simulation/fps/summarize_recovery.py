"""Summarize the fixed five simulations; keep seed/map/seat pairings intact."""
import argparse
import csv
import json
from pathlib import Path

from matched_recovery import CASES

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--arms',nargs='+',default=['baseline','stop_only','candidate'])
    args=p.parse_args()
    rows=[]
    for name,seed,seat in CASES:
        reference=None
        for arm in args.arms:
            d=json.loads((args.data/arm/f'{name}-{seed}.json').read_text())
            assert (d['map_id'],d['seed'],d['seat'])==(name+'-r3-v54',seed,seat) and d['done']
            fixture=(d['opponent_sha256'],d['static_sha256'],d['peer_release_sha256'])
            if reference is None: reference=fixture
            assert reference==fixture
            ours,theirs=d['seat_team_score']
            rows.append(dict(map=name,seed=seed,seat=seat,version=arm,ours=ours,theirs=theirs,
                             outcome='win' if ours>theirs else 'loss' if ours<theirs else 'draw',
                             faults=sum(d['faulty_actions']),steps=d['steps'],**d['motion']))
    with (args.data/'fixtures.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,rows[0].keys(),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    summary={}
    for arm in args.arms:
        batch=[r for r in rows if r['version']==arm]
        total_s=sum(r['moving_s'] for r in batch)
        stuck_s=sum(r['stuck_s'] for r in batch)
        summary[arm]=dict(matches=len(batch),wins=sum(r['outcome']=='win' for r in batch),
                          losses=sum(r['outcome']=='loss' for r in batch),draws=sum(r['outcome']=='draw' for r in batch),
                          rounds_for=sum(r['ours'] for r in batch),rounds_against=sum(r['theirs'] for r in batch),
                          moving_s=round(total_s,2),stuck_s=round(stuck_s,2),
                          stuck_share_pct=round(100*stuck_s/total_s,3) if total_s else None,
                          longest_stuck_s=max(r['longest_stuck_s'] for r in batch),
                          faults=sum(r['faults'] for r in batch))
    (args.data/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
