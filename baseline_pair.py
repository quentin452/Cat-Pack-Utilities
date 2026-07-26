#!/usr/bin/env python3
"""Produce BOTH matou-engine T-02 baselines unattended: flags → launch → traverse → capture, twice.

The pair is the point. Two runs that differ only in their cut-flag profile are comparable; two runs that
also differ in what the operator did are not. Automating the whole cycle is what makes "only the flags
changed" a fact rather than an intention.

Composes what already exists rather than reimplementing it:
  gate.py flags/launch/stop   — the fragile single-instance boilerplate, already hard-won
  scripts/deploy.sh           — build + deploy, with its own live-game guard
  baseline_run.py             — the scripted traversal, with its own validity checks
  baseline_capture.py         — capture + flag profile

Usage:
  python3 baseline_pair.py --instance Minimal-Matou --headings 90,270 --laps 3
  python3 baseline_pair.py --instance Minimal-Matou --only a --deploy
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import packenv as E  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(E.HUB, "scripts", "deploy.sh")

# The two profiles. Everything else in the instance config is left untouched — these are the only knobs
# that define WHICH baseline a run is (matou-engine PRD §5.5).
CUT_FLAGS = [
    "matoulib.hardCut",
    "matoulib.entity.trackerCut",
    "matoulib.entity.netInterest",
    "matoulib.client.ebsCut",
    "matoulib.collision.own",
    "matoulib.physics.own",
    "matoulib.entity.spawnOwn",
]
PROFILES = {
    "a": ("a-cutflags-off", "false"),
    "b": ("b-hardcut-on", "true"),
}
# Always on for a baseline run, whichever profile.
ALWAYS = ["matoulib.engine.frameStats=true", "matoulib.rpc=true", "matoulib.autoworld=true"]


def run(cmd, **kw):
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        sys.exit(f"failed ({r.returncode}): {' '.join(cmd)}")
    return r


def one(profile, args):
    label, value = PROFILES[profile]
    print(f"\n{'=' * 70}\n  BASELINE {profile.upper()} — {label} (cut flags = {value})\n{'=' * 70}")

    run([sys.executable, os.path.join(HERE, "gate.py"), "stop"], check=False)

    if args.deploy:
        # deploy.sh guards on a live game itself; stopping first is what makes that guard pass.
        run(["bash", DEPLOY])

    sets = [f"{f}={value}" for f in CUT_FLAGS] + ALWAYS
    run([sys.executable, os.path.join(HERE, "gate.py"), "flags", args.instance, "--set", *sets])
    run([sys.executable, os.path.join(HERE, "gate.py"), "launch", args.instance,
         "--timeout", str(args.launch_timeout)])

    traversal = [sys.executable, os.path.join(HERE, "baseline_run.py"),
                 "--laps", str(args.laps), "--headings", args.headings]
    run(traversal)

    note = (f"scripted+automated: {args.laps} laps x headings[{args.headings}] x 200 ticks "
            f"(baseline_pair.py, profile {profile})")
    run([sys.executable, os.path.join(HERE, "baseline_capture.py"), "--label", label, "--note", note])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="Minimal-Matou")
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--headings", default="90,270",
                    help="must be CLEAR at the spawn point — a blocked heading streams no chunks. "
                         "The SAME value is used for both profiles, which is what makes them comparable.")
    ap.add_argument("--only", choices=["a", "b"], help="run a single profile instead of the pair")
    ap.add_argument("--deploy", action="store_true", help="build + deploy the jar before each run")
    ap.add_argument("--launch-timeout", type=int, default=180)
    args = ap.parse_args()

    profiles = [args.only] if args.only else ["b", "a"]
    for p in profiles:
        one(p, args)

    print("\nboth captures live under memory/bench-captures/matou-engine-t02/ — review, then commit.")
    print("NOTE: the captures are gitignored; add them with -f, deliberately.")


if __name__ == "__main__":
    main()
