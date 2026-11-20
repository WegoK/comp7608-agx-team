"""Five fixed local game fixtures. Measure full scores and prolonged movement with no progress."""
import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

CASES = [('borgo', 1811, 0), ('borgo', 1812, 5), ('molo', 1811, 0),
         ('molo', 1812, 5), ('nevaio', 1813, 0)]

class Motion:
    """Post-step XY outcome for consecutive stationary commands during live rounds."""
    def __init__(self):
        self.anchor = None
        self.ticks = 0
        self.request_ticks = 0
        self.intervals = []

    def finish(self):
        if self.ticks >= 60:
            self.intervals.append(self.ticks)
        self.anchor = None
        self.ticks = 0

    def update(self, before, command, after):
        old, new = before['me'], after['me']
        dt = after['match_tick'] - before['match_tick']
        active = (old['alive'] and new['alive'] and before['phase'] == after['phase'] == 'live'
                  and before['round_index'] == after['round_index'] and dt == 3
                  and math.hypot(command.get('move_forward', 0), command.get('move_right', 0)) >= 0.1)
        if not active:
            self.finish()
            return
        self.request_ticks += dt
        if self.anchor is None:
            self.anchor = old['x'], old['y']
        if math.hypot(self.anchor[0] - new['x'], self.anchor[1] - new['y']) <= 0.15:
            self.ticks += dt
        else:
            self.finish()

    def result(self):
        self.finish()
        return dict(moving_s=self.request_ticks / 60, stuck_intervals=len(self.intervals),
                    stuck_s=sum(self.intervals) / 60, longest_stuck_s=max(self.intervals, default=0) / 60)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(case, opts):
    name, seed, seat = case
    block = opts.block
    lab = block / 'AGX_Local_Lab'
    project = block / 'AGX_Competition'
    base = project / 'sources/01_Official_Starter/FPS/agent.py'
    static = lab / ('static_nevaio' if name == 'nevaio' else 'static')
    os.environ.update(FPS_STATIC_DIR=str(static), FPS_KIT_DIR=str(block / 'fps_maps'))
    sys.path[:0] = [str(project/'simulation/fps'), str(lab/'fpssim-release')]
    from fpssim.arena import Arena
    from run_peer_cpu import load_seat
    arena = Arena()
    map_id = name + '-r3-v54'
    obs = arena.reset(map_id=map_id, seed=seed)
    policies = [load_seat(opts.agent if i == seat else base, i, seed) for i in range(10)]
    motion = Motion()
    start = time.monotonic()
    for steps in range(1, 75001):
        actions = [policy.act(o) or {} for policy, o in zip(policies, obs)]
        previous = obs[seat]
        obs, _, done, info = arena.step(actions)
        motion.update(previous, actions[seat], obs[seat])
        if done:
            break
    if not done:
        raise RuntimeError(f'{case} exceeded match length')
    ref = json.loads((project/'experiments/R01/fps'/('nevaio/native_results' if name == 'nevaio' else 'native_results') / f'{name}-{seed}.json').read_text())
    assert ref['static_sha256'] == sha(static / f'static_{map_id}.json') and ref['baseline_sha256'] == sha(base)
    if sha(opts.agent) == sha(base):
        assert ref['results'][0]['score'] == info['round_wins'] and ref['results'][0]['steps'] == steps
    result = dict(map_id=map_id, seed=seed, seat=seat, steps=steps, done=done,
                  team_scores=info['round_wins'], seat_team_score=info['round_wins'][::(1 if seat < 5 else -1)],
                  faulty_actions=info['faults'], motion=motion.result(), agent_sha256=sha(opts.agent),
                  opponent_sha256=sha(base), static_sha256=ref['static_sha256'],
                  peer_release_sha256=sha(block/'fpssim-release(2).tar.gz'),
                  python=platform.python_version(), elapsed_s=round(time.monotonic()-start, 2))
    dest = opts.out / f'{name}-{seed}.json'
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,indent=2)+'\n')
    tmp.replace(dest)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--block',type=Path,default=Path.home()/'Desktop/Block4')
    p.add_argument('--agent',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=3)
    opts=p.parse_args()
    opts.block=opts.block.resolve()
    opts.agent=opts.agent.resolve()
    opts.out=opts.out.resolve()
    opts.out.mkdir(parents=True,exist_ok=True)
    with ProcessPoolExecutor(max_workers=opts.workers, max_tasks_per_child=1) as pool:
        fs=[pool.submit(run,case,opts) for case in CASES]
        for f in as_completed(fs):
            r=f.result()
            print(r['map_id'],r['seed'],r['seat_team_score'],r['motion'],flush=True)


if __name__ == '__main__':
    main()
