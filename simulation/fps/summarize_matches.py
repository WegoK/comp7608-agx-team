"""Summarize completed paired native matches from the tested seat's perspective."""
import argparse
import csv
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.directory.glob('*.json')):
        data = json.loads(path.read_text())
        a, b = data['results']
        if not a['done'] or not b['done']:
            continue
        team = int(data['tested_seat'] >= 5)
        own, opp = team, 1 - team
        def outcome(score):
            return 'win' if score[own] > score[opp] else 'loss' if score[own] < score[opp] else 'draw'
        rows.append(dict(map=data['map'], seed=data['seed'], seat=data['tested_seat'],
                         baseline_own=a['score'][own], baseline_other=a['score'][opp],
                         candidate_own=b['score'][own], candidate_other=b['score'][opp],
                         baseline_result=outcome(a['score']), candidate_result=outcome(b['score']),
                         total_faults=sum(a['faults'])+sum(b['faults'])))
    if not rows:
        raise SystemExit('No complete paired results yet.')
    with (args.directory/'paired_scores.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)


if __name__ == '__main__':
    main()
