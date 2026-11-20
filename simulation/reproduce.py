"""Repeat the R01 comparison from any working directory. Outputs stay in the local lab."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys

sim = Path(__file__).resolve().parent
repo = sim.parent
block = repo.parent
lab = block / "AGX_Local_Lab"
starter = repo / "01_Official_Starter"
candidate = repo / "strategies/local/fps/R01-recovery"


def run(script, *args):
    subprocess.run([sys.executable, "-B", str(script), *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["quick", "matches", "logs", "all"], default="quick", nargs="?")
    parser.add_argument("--logs", type=Path, default=Path.home() / "Downloads")
    args = parser.parse_args()
    out = lab / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    fps = sim / "fps"
    versions = ["--baseline", starter / "FPS/agent.py", "--candidate", candidate / "agent.py"]
    path_version = [*versions[:3], fps / "variants/r01_shortest_path.py"]
    if args.mode in ("quick", "all"):
        run(fps / "check_routes.py", versions[1], path_version[3], "--official-kit", block / "agx-fps-kit/agent/agent.py", "--out", out / "synthetic.json")
        run(fps / "evaluate_routes.py", *path_version, "--maps", lab / "static", "--replay-static", "--out", out / "routes")
        run(fps / "evaluate_routes.py", *path_version, "--maps", lab / "static_nevaio", "--replay-static", "--out", out / "nevaio-routes")
        run(fps / "evaluate_routes.py", *path_version, "--maps", block / "fps_maps", "--out", out / "grid")
        for label, flags in (("racing", []), ("racing-stress", ["--stress"])):
            run(sim / "racing/bench.py", "--baseline", starter / "Racing", "--candidate", sim / "racing/preview_experiment", "--maps", block / "racing_maps", "--out", out / label, *flags)
    if args.mode in ("matches", "all"):
        for name in ("borgo", "molo"):
            for seed, seat in [(1811, 0), (1812, 5)]:
                run(fps / "run_peer_cpu.py", *versions, "--peer-root", lab / "fpssim-release",
                    "--maps", block / "fps_maps", "--static-dir", lab / "static",
                    "--map-id", f"{name}-r3-v54", "--seed", seed, "--seat", seat,
                    "--out", out / f"{name}-{seed}.json")
        run(fps / "run_peer_cpu.py", *versions, "--peer-root", lab / "fpssim-release", "--maps", block / "fps_maps", "--static-dir", lab / "static_nevaio", "--map-id", "nevaio-r3-v54", "--seed", 1813, "--seat", 0, "--out", out / "nevaio-1813.json")
    if args.mode in ("logs", "all"):
        run(fps / "check_logged_routes.py", args.logs / "training-d283f152-9354-40de-8f88-6b6787ceb7ac.jsonl", versions[1], path_version[3], "--out", out / "logged-borgo.json")
        run(fps / "evaluate_recovery.py", *versions, "--peer-root", lab / "fpssim-release", "--log", args.logs / "training-d283f152-9354-40de-8f88-6b6787ceb7ac.jsonl", "--out", out / "current-movement.json")
        for match in ("7f617533-fe63-4b70-a2b4-c2f8c9c42ae5", "765a3940-3809-4e7f-aeaa-db42d6f7dd4f"):
            run(sim / "racing/inspect_export.py", args.logs / f"training-{match}.jsonl", "--baseline", starter / "Racing", "--candidate", sim / "racing/preview_experiment", "--output", out / f"racing-{match[:8]}.json")
            run(sim / "racing/verify.py", args.logs / f"training-{match}.jsonl", out / f"live-{match[:8]}.json")
        run(sim / "racing/launch.py", args.logs / "training-7f617533-fe63-4b70-a2b4-c2f8c9c42ae5.jsonl", out / "launch.json")
    print(f"本次结果保存在 {out}")


if __name__ == "__main__":
    main()
