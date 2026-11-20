"""Paired self-play using the supplied peer release and a real replay static map."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def load_seat(source, seat, seed):
    spec = importlib.util.spec_from_file_location(f'seat_{seat}', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, 'init'):
        module.init({'seat': seat, 'seed': seed * 10 + seat, 'weights_dir': str(source.parent)})
    return module


def run_match(arena_type, args, use_candidate):
    arena = arena_type()
    observations = arena.reset(map_id=args.map_id, seed=args.seed)
    policies = [load_seat(args.candidate if use_candidate and s == args.seat else args.baseline,
                          s, args.seed) for s in range(10)]
    started = time.monotonic()
    done = False
    for step in range(1, args.steps + 1):
        actions = [policy.act(o) or {} for policy, o in zip(policies, observations)]
        observations, _, done, info = arena.step(actions)
        if done:
            break
    return dict(candidate_present=use_candidate, done=done, steps=step, score=info['round_wins'],
                faults=info['faults'], elapsed_s=round(time.monotonic() - started, 2))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('peer-root', 'maps', 'static-dir', 'baseline', 'candidate', 'out'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--assets', type=Path)
    p.add_argument('--map-id', required=True)
    p.add_argument('--seat', type=int, choices=range(10), default=0)
    p.add_argument('--seed', type=int, default=1811)
    p.add_argument('--steps', type=int, default=75000)
    args = p.parse_args()
    if args.steps < 1:
        p.error('--steps must be at least one')
    static_path = args.static_dir / f'static_{args.map_id}.json'
    if not static_path.is_file():
        p.error('Missing replay static map: ' + str(static_path))
    d = json.loads(static_path.read_text())
    d = d.get('map', d)
    if not d.get('nav') or not d.get('sites') or not d.get('spawns'):
        p.error('Expected replay map with nav, sites and spawns. Official mapdata is a different format.')
    if d.get('id') != args.map_id:
        p.error('Map ID does not match static file contents')
    if args.map_id.split('-')[0] in {'dust2', 'mirage'}:
        if not args.assets:
            p.error('This mesh map also needs its collision and material assets')
    os.environ.update(FPS_STATIC_DIR=str(args.static_dir.resolve()), FPS_KIT_DIR=str(args.maps.resolve()))
    if args.assets:
        os.environ['FPS_ASSET_DIR'] = str(args.assets.resolve())
    sys.path.insert(0, str(args.peer_root.resolve()))
    from fpssim.arena import Arena
    results = [run_match(Arena, args, use_candidate) for use_candidate in (False, True)]
    record = dict(map=args.map_id, seed=args.seed, tested_seat=args.seat, results=results,
                  static_sha256=sha(static_path), baseline_sha256=sha(args.baseline),
                  candidate_sha256=sha(args.candidate))
    args.out.parent.mkdir(exist_ok=True, parents=True)
    args.out.write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
