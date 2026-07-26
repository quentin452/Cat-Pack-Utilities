#!/usr/bin/env python3
"""Drive a REPRODUCIBLE traversal over the matoulib devtools RPC, for matou-engine T-02 baselines.

A baseline captured by hand is not a baseline. The two T-02 runs (cut flags OFF vs hardCut ON) differ only
in configuration, so anything else that differs — how far you walked, how fast, which way you looked —
lands in the numbers and cannot be separated from the effect being measured. This script performs the
identical action sequence every time, so the only variable left is the flag profile.

Deterministic by construction: fixed yaw sequence, fixed segment length, fixed sprint state. No randomness,
no wall-clock pacing decisions.

Usage:
  python3 baseline_run.py --laps 4                  # ~4 min of sprinting traversal
  python3 baseline_run.py --laps 4 --dry-run        # print the plan, touch nothing

Prerequisites: the client is running, in a world, with matoulib.rpc=true and
matoulib.engine.frameStats=true. Capture afterwards with baseline_capture.py.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import packenv as E  # noqa: E402

BASE = f"http://{E.RPC_HOST}:{E.RPC_PORT}"

# The scenario. Four headings, so a lap returns roughly to the start and keeps chunk loading continuous
# without drifting forever into ungenerated terrain.
YAWS = (0.0, 90.0, 180.0, 270.0)
TICKS_PER_SEGMENT = 200  # ~10 s at 20 tps
PITCH = 0.0


def rpc(path, **params):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--laps", type=int, default=4, help="laps of 4 segments (default 4 ~= 4 min)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    segments = args.laps * len(YAWS)
    seconds = segments * TICKS_PER_SEGMENT / 20.0
    plan = (f"{args.laps} laps x {len(YAWS)} headings x {TICKS_PER_SEGMENT} ticks "
            f"= {segments} segments, ~{seconds:.0f}s sprinting")
    print(f"scenario: {plan}")
    if args.dry_run:
        return

    try:
        pong = rpc("/ping")
    except Exception as exc:
        sys.exit(f"RPC unreachable at {BASE}: {exc}\n(client running? matoulib.rpc=true?)")
    if not pong.get("inWorld"):
        sys.exit("client is not in a world — enter one first, the traversal needs a loaded world")

    started = time.time()
    for lap in range(args.laps):
        for yaw in YAWS:
            rpc("/look", yaw=yaw, pitch=PITCH)
            rpc("/playermove", forward=1, sprint="true", ticks=TICKS_PER_SEGMENT, yaw=yaw)
            print(f"  lap {lap + 1}/{args.laps} yaw {yaw:>5.0f} done")
    elapsed = time.time() - started

    print(f"traversal complete in {elapsed:.0f}s")
    try:
        gc = rpc("/gcstats")
        print(f"  gc: minor={gc.get('minorGc')} ({gc.get('minorGcMs')}ms) "
              f"major={gc.get('majorGc')} ({gc.get('majorGcMs')}ms)")
    except Exception:
        pass
    print("\nnow capture, quoting this exact scenario:")
    print(f'  python3 baseline_capture.py --label <a-cutflags-off|b-hardcut-on> --note "{plan}"')


if __name__ == "__main__":
    main()
