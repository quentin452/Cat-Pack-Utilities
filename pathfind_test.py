#!/usr/bin/env python3
"""Automated in-game pathfinding regression test for the OaT async pathfinding refonte (BUG-008).

Drives the matoulib devtools RPC (must already be up and in-world — launch the pack TEST client
via the `devtools` skill or `release.py smoke --client` first). It:

  1. POSTs /pathtest  -> server builds a walled arena, spawns an AI-cleared mob, and commands its
     navigator to a goal on the far side of a wall (open at both ends: a straight line fails, only
     real pathfinding detours around).
  2. Polls /entity?id= until the mob reaches the goal or times out (bypasses /state's 50-entity cap).
  3. Samples /pathstats (OaT async counters) + /metrics (TPS) throughout.
  4. Asserts:  mob REACHED  AND  0 new failed/timeout paths  AND  TPS stayed above --tps-min.
  5. /screenshot for the record.

Exit 0 = PASS, 1 = FAIL, 2 = harness/setup error. Meant to be run headless in the user's display
session after the client is in a world.
"""
import argparse
import json
import math
import sys
import time
import urllib.request


def rpc(base, path, timeout=8):
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as r:
        return json.load(r)


def wait_in_world(base, deadline):
    while time.time() < deadline:
        try:
            if rpc(base, "/ping", timeout=3).get("inWorld"):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def dist_xz(a, b):
    return math.hypot(a["x"] - b["x"], a["z"] - b["z"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:25580", help="RPC base URL")
    ap.add_argument("--def", dest="mobdef", default="gigapig", help="mob definition id")
    ap.add_argument("--dist", type=int, default=16, help="start->goal distance in blocks")
    ap.add_argument("--speed", type=float, default=1.2, help="navigation speed multiplier")
    ap.add_argument("--timeout", type=float, default=35.0, help="seconds to reach the goal")
    ap.add_argument("--reach", type=float, default=1.8, help="reached when within this many blocks")
    ap.add_argument("--tps-min", type=float, default=17.0, help="min acceptable server TPS during the run")
    ap.add_argument("--ping-wait", type=float, default=0.0, help="wait up to N s for inWorld before starting")
    ap.add_argument("--screenshot", default="pathtest", help="screenshot name (no extension)")
    args = ap.parse_args()
    base = args.base

    if args.ping_wait > 0 and not wait_in_world(base, time.time() + args.ping_wait):
        print("[pathtest] ERROR: not in world after ping-wait", file=sys.stderr)
        return 2

    try:
        stats0 = rpc(base, "/pathstats")
    except Exception as e:
        print(f"[pathtest] ERROR: RPC unreachable ({e}). Is the client in-world with -Dmatoulib.rpc=true?",
              file=sys.stderr)
        return 2
    oat = bool(stats0.get("available"))
    if not oat:
        print("[pathtest] WARN: OaT AsyncPathfindingExecutor absent -> the async pathfinder is NOT being "
              "exercised (mob uses vanilla pathing). failed/timeout assertions skipped.", file=sys.stderr)

    setup = rpc(base, f"/pathtest?def={args.mobdef}&dist={args.dist}&speed={args.speed}")
    if not setup.get("ok"):
        print(f"[pathtest] ERROR: /pathtest failed: {setup.get('error')}", file=sys.stderr)
        return 2
    eid, goal = setup["entityId"], setup["goal"]
    before = setup.get("statsBefore")  # int[7] or None
    print(f"[pathtest] entity {eid} def={args.mobdef} dist={args.dist} "
          f"start={setup['start']} goal={goal} issuedImmediate={setup.get('issuedImmediate')}")

    deadline = time.time() + args.timeout
    min_dist = float("inf")
    min_tps = float("inf")
    reached = False
    moved = False
    last_tps_sample = 0.0
    start_pos = None
    while time.time() < deadline:
        ent = rpc(base, f"/entity?id={eid}")
        if not ent.get("found"):
            print(f"[pathtest] ERROR: entity {eid} vanished (unloaded/despawned/crash)", file=sys.stderr)
            break
        if start_pos is None:
            start_pos = {"x": ent["x"], "z": ent["z"]}
        if dist_xz(ent, start_pos) > 1.5:
            moved = True
        d = dist_xz(ent, goal)
        min_dist = min(min_dist, d)
        now = time.time()
        if now - last_tps_sample >= 2.0:
            last_tps_sample = now
            try:
                m = rpc(base, "/metrics")
                if m.get("ok"):
                    min_tps = min(min_tps, float(m.get("tps", min_tps)))
            except Exception:
                pass
        if d <= args.reach:
            reached = True
            break
        time.sleep(0.5)

    after = rpc(base, "/pathstats") if oat else None
    try:
        rpc(base, f"/screenshot?name={args.screenshot}")
    except Exception:
        pass

    # --- verdict ---
    failed_delta = timeout_delta = None
    if oat and before and after and after.get("available"):
        failed_delta = after["failed"] - before[2]
        timeout_delta = after["timeout"] - before[3]

    ok = reached
    reasons = []
    if not reached:
        reasons.append(f"did not reach goal (min dist {min_dist:.1f} > {args.reach}, moved={moved})")
    if min_tps < args.tps_min and min_tps != float("inf"):
        ok = False
        reasons.append(f"TPS dropped to {min_tps:.1f} < {args.tps_min}")
    if failed_delta:
        ok = False
        reasons.append(f"{failed_delta} pathfinding request(s) FAILED during run")
    if timeout_delta:
        ok = False
        reasons.append(f"{timeout_delta} pathfinding request(s) TIMED OUT during run")

    summary = {
        "pass": ok,
        "reached": reached,
        "moved": moved,
        "minDistToGoal": round(min_dist, 2),
        "minTps": None if min_tps == float("inf") else round(min_tps, 2),
        "oatStatsAvailable": oat,
        "failedDelta": failed_delta,
        "timeoutDelta": timeout_delta,
        "statsAfter": after if oat else None,
    }
    print(json.dumps(summary, indent=2))
    if ok:
        print("[pathtest] PASS")
        return 0
    print("[pathtest] FAIL: " + "; ".join(reasons), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
