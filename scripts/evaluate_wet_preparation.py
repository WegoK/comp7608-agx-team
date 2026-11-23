"""Test one bounded wet passing-preparation proposal on legal recorded inputs.

No alternative trajectory or official collision/race result is simulated.
"""
import argparse
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import types

from evaluate_racing_recovery import Policy, ROOT, blob, equal, lateral

BASE = 'ee12c93d76f6ecefc9569776b925139adbd7c860'
ACTUAL = {'0ce291ac5e307cb97bb8f04666adbcb286d0abcdaa18c0a20acef31a2a941ee4': BASE,
          'bfaeb741839032f0bffc0671592ac48860374ed44213f6a89aff3cd7d5a2e4a9': 'd6268a7f50adcfec63b79ca706c627401ae2af2d'}


def guards(old, new):
    p = types.SimpleNamespace(gg_active=True, wet=.9, defender=None, v=50., s=0., goal=0., offset=0.,
        c=dict(lap=3, pit_state=0, status='racing', front_sliding=False, rear_sliding=False, heading_error=0.),
        o=dict(phase='race'), diagnostic={}, pace=lambda s: 20., tracking_error=lambda: .1,
        pursuit_zone=lambda f: None, catch_window=lambda f: None)
    front = dict(seat=2, gap_m=140., speed_mps=20., merging=False)
    def run(cls, player, rival): return cls.approach_conflict(copy.deepcopy(player), copy.deepcopy(rival))
    assert run(old.Driver, p, front) is False and run(new.Driver, p, front) is True
    labels = ['wet_early_proposal_when_baseline_cannot_catch_in_six_seconds']
    for label in ('no_grip', 'first_lap', 'merging', 'near_boundary', 'far_boundary', 'not_closing',
                  'tracking', 'lane_change', 'heading', 'front_slide', 'rear_slide', 'pit', 'not_racing',
                  'defending', 'qualifying', 'no_upcoming_braking', 'dry'):
        q, f = copy.deepcopy(p), dict(front)
        if label == 'no_grip': q.gg_active = False
        elif label == 'first_lap': q.c['lap'] = 0
        elif label == 'merging': f['merging'] = True
        elif label == 'near_boundary': f['gap_m'] = 90.
        elif label == 'far_boundary': f['gap_m'] = 180.
        elif label == 'not_closing': f['speed_mps'] = 48.
        elif label == 'tracking': q.tracking_error = lambda: .41
        elif label == 'lane_change': q.offset = .26
        elif label == 'heading': q.c['heading_error'] = .101
        elif label == 'front_slide': q.c['front_sliding'] = True
        elif label == 'rear_slide': q.c['rear_sliding'] = True
        elif label == 'pit': q.c['pit_state'] = 1
        elif label == 'not_racing': q.c['status'] = 'retired'
        elif label == 'defending': q.defender = 2
        elif label == 'qualifying': q.o['phase'] = 'qualifying'
        elif label == 'no_upcoming_braking': q.pace = lambda s: 50.
        elif label == 'dry': q.wet = .4
        assert run(old.Driver, q, f) == run(new.Driver, q, f), label
        labels.append(label + '_preserved')
    return labels


def source_guards():
    before = blob(BASE, 'agent.py').decode()
    after = (ROOT/'strategies/racing/agent.py').read_text(encoding='utf-8')
    def methods(source):
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'Driver')
        return {n.name: ast.get_source_segment(source,n) for n in cls.body if isinstance(n,ast.FunctionDef)}
    a, b = methods(before), methods(after)
    changed = [name for name in a if a[name] != b[name]]
    assert set(changed) == {'approach_conflict', 'tactics'}, changed
    for name in ('preview_driver.py','design.json','weights/plan.npz','weights/mechanics.npz'):
        data = (ROOT/'strategies/racing'/name).read_bytes()
        if not name.endswith('.npz'): data=data.replace(b'\r\n',b'\n')
        assert data == blob(BASE,name)
    return dict(changed_driver_methods=changed, all_other_driver_methods_byte_equivalent=True,
                preview_design_and_weights_unchanged=True)


def compare(path):
    old, new = Policy('wet_control',BASE), Policy('wet_candidate')
    digest = hashlib.sha256();rows=raw=agree=changes=commits=proposals=0
    phases, laps, fields, examples = {}, {}, Counter(), []
    with path.open('rb') as source:
        h=json.loads(next(source));own=h['participants'][0]
        actual_ref=ACTUAL[own['source_hash']]
        actual=old if actual_ref==BASE else Policy('wet_logged_actual',actual_ref)
        footer=None
        for line in source:
            r=json.loads(line)
            if 'observation' not in r:
                if r.get('type')=='export_complete':footer=r
                continue
            rows+=1;raw+=len(line);digest.update(line)
            o=r['observation'];a=old.act(o);b=new.act(o);lateral.legal(b)
            agree+=equal(a if actual is old else actual.act(o),r['action'])
            d=new.main._driver;diag=d.diagnostic
            proposals+= 'wet_preparation_front' in diag
            commit=bool(diag.get('wet_preparation_commit'));commits+=commit
            if commit:
                assert o['phase']=='race' and o['self']['lap']>=1 and d.wet>=.5
                assert not o['self']['front_sliding'] and not o['self']['rear_sliding']
                assert diag['approach_selected_utility']>diag['approach_stay_utility']+.05
                stations=[d.s+d.progress*t for t in (.7,1.4,2.2)]
                low=max(d.bounds(x)[0]-d.at(d.safe_line,x) for x in stations)
                high=min(d.bounds(x)[1]-d.at(d.safe_line,x) for x in stations)
                assert low-1e-9<=d.goal<=high+1e-9
            q=phases.setdefault(o['phase'],dict(rows=0,changed=0,wet_commits=0))
            q['rows']+=1;q['wet_commits']+=commit
            lap=laps.setdefault(str(o['self']['lap']),dict(changed=0,wet_commits=0)) if o['phase']=='race' else None
            if lap is not None:lap['wet_commits']+=commit
            changed=not equal(a,b)
            if changed:
                assert o['phase']=='race' and o['self']['lap']>=1
                changes+=1;q['changed']+=1;lap['changed']+=1
                for key in a:fields[key]+=not equal(a[key],b[key])
                if len(examples)<6 and (not examples or o['time']-examples[-1]['global_time_s']>=3):
                    examples.append(dict(global_time_s=o['time'],lap_zero_based=o['self']['lap'],
                        baseline_mode=old.main._driver.mode,candidate_mode=d.mode,
                        baseline_goal=old.main._driver.goal,candidate_goal=d.goal,
                        baseline_steer=a['steer'],candidate_steer=b['steer']))
            if rows%5000==0:print(path.stem,rows,flush=True)
    assert footer and footer['complete'] and (rows,raw,digest.hexdigest())==(footer['rows'],footer['raw_bytes'],footer['sha256'])
    assert footer['seat_calls']=={str(own['seat']):rows} and agree==rows
    return dict(match_id=h['job_id'],actual_source_commit=actual_ref,rows=rows,
        actual_frozen_action_agreement=agree,decision_bytes_sha256=digest.hexdigest(),
        changed_actions=changes,unchanged_actions=rows-changes,proposals=proposals,
        model_preparation_commits=commits,phases=phases,race_laps=laps,changed_fields=fields,examples=examples,
        note='Historical own positions stay fixed: repeated model commits are not actual lane changes or overtakes. No new collisions, lap times or match result simulated.')


def blocked_options_fixture(path):
    """Replay to a demonstrated proposal, then inject unavailable alternatives."""
    policy=Policy('wet_blocked_fixture')
    original=policy.main.Driver.tactics
    results=[]
    def inspect(self):
        selected=abs(self.o['time']-1505.0333)<.001 and self.o['phase']=='race'
        snapshot=copy.deepcopy(self) if selected else None
        original(self)
        if not selected:return
        assert self.diagnostic.get('wet_preparation_commit')
        for label in ('occupied_alternatives','narrow_corridor','negative_progress_value'):
            q=copy.deepcopy(snapshot)
            if label=='occupied_alternatives':
                q.path_value=lambda *args: (20.,-100.,100.)
            elif label=='narrow_corridor':
                q.bounds=lambda station: (q.at(q.safe_line,station)-.1,q.at(q.safe_line,station)+.1)
                q.tracking_error=lambda: .1
            else:
                q.approach_loss=lambda front,goal: 0. if abs(goal)<1e-9 else 100000.
            original(q)
            assert 'wet_preparation_front' in q.diagnostic
            assert not q.diagnostic.get('wet_preparation_commit'),label
            results.append(label+'_veto_preserved')
    policy.main.Driver.tactics=inspect
    with path.open(encoding='utf-8') as source:
        for line in source:
            row=json.loads(line)
            if 'observation' in row:
                policy.act(row['observation'])
                if results:break
    assert len(results)==3
    return dict(match_id='0853449c-127a-44cc-8061-26e9e88f20d1',fixture_time_s=1505.0333,
                synthetic_vetoes=results,note='Injected decision constraints, not simulated physical blockers or collisions.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log',action='append',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    old,new=Policy('wet_guard_old',BASE),Policy('wet_guard_new')
    result=dict(schema='wet-preparation-candidate-v1',baseline_commit=BASE,
        source_invariants=source_guards(),eligibility_guards=guards(old.main,new.main),
        candidate_agent_lf_sha256=hashlib.sha256((ROOT/'strategies/racing/agent.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        logged_comparisons=[compare(path) for path in args.log],
        blocked_options_fixture=blocked_options_fixture(next(path for path in args.log if '0853449c' in path.name)),
        platform_validation='not_run',
        competition_activation='not_run',candidate_real_matches=0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([dict(match=r['match_id'],changed=r['changed_actions'],commits=r['model_preparation_commits'],laps=r['race_laps']) for r in result['logged_comparisons']],indent=2))


if __name__=='__main__':main()
