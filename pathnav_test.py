#!/usr/bin/env python3
"""Generic pathfinding test that tolerates 1.7.10's intermittent mob AI.

Unlike a one-shot tryMoveToXYZ + instant snapshot (which can't tell "idle between wander cycles"
from "pathfinding broken" in 1.7.10), this RE-ISSUES navigation periodically on the SAME entity and
measures NET PROGRESS toward a goal over a long window. Reaching the goal (even in fits) = PASS.

Generic like /opengui: spawn ANY entity by class name (must extend EntityCreature to navigate), or
pass --id to drive an already-spawned mob.

Requires the matoulib RPC in-world with the /spawnentity + /navigate + /entity endpoints.
Exit 0 = PASS (reached or clear net progress), 1 = FAIL (no progress), 2 = setup error.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:25580")
    ap.add_argument("--class", dest="cls", default="net.minecraft.entity.passive.EntityCow",
                    help="entity FQN to spawn (must extend EntityCreature)")
    ap.add_argument("--id", type=int, default=None, help="drive an existing entity id instead of spawning")
    ap.add_argument("--dist", type=int, default=12, help="goal distance from spawn/player, in +X blocks")
    ap.add_argument("--window", type=float, default=60.0, help="seconds to observe")
    ap.add_argument("--renav", type=float, default=4.0, help="re-issue navigation every N seconds")
    ap.add_argument("--speed", type=float, default=1.3)
    ap.add_argument("--reach", type=float, default=2.0, help="reached when within this many blocks")
    ap.add_argument("--clearai", action="store_true", help="strip AI so only the assigned path drives it")
    args = ap.parse_args()
    base = args.base

    st = rpc(base, "/state")
    p = st.get("player")
    if not p:
        print("[pathnav] ERROR: not in world / no player", file=sys.stderr)
        return 2
    sx, sy, sz = int(p["x"]), int(p["y"]), int(p["z"])
    gx, gy, gz = sx + args.dist, sy, sz  # goal on flat +X

    eid = args.id
    if eid is None:
        r = rpc(base, f"/spawnentity?class={args.cls}&x={sx}&y={sy + 1}&z={sz + 2}")
        if not r.get("ok"):
            print(f"[pathnav] ERROR: spawn failed: {r.get('error')}", file=sys.stderr)
            return 2
        eid = r["entityId"]
        print(f"[pathnav] spawned {r.get('class')} id={eid} at ({sx},{sy+1},{sz+2}) goal=({gx},{gy},{gz})")
    else:
        print(f"[pathnav] driving existing entity id={eid} goal=({gx},{gy},{gz})")

    goal = {"x": gx + 0.5, "z": gz + 0.5}
    start_pos = None
    min_dist = float("inf")
    max_moved = 0.0
    deadline = time.time() + args.window
    last_nav = 0.0
    reached = False
    ci = "&clearai=true" if args.clearai else ""

    while time.time() < deadline:
        now = time.time()
        if now - last_nav >= args.renav:
            last_nav = now
            rpc(base, f"/navigate?id={eid}&x={gx}&y={gy}&z={gz}&speed={args.speed}{ci}")
        ent = rpc(base, f"/entity?id={eid}")
        if not ent.get("found"):
            print(f"[pathnav] ERROR: entity {eid} vanished", file=sys.stderr)
            break
        pos = {"x": ent["x"], "z": ent["z"]}
        if start_pos is None:
            start_pos = pos
        d = math.hypot(pos["x"] - goal["x"], pos["z"] - goal["z"])
        moved = math.hypot(pos["x"] - start_pos["x"], pos["z"] - start_pos["z"])
        min_dist = min(min_dist, d)
        max_moved = max(max_moved, moved)
        if d <= args.reach:
            reached = True
            break
        time.sleep(1.0)

    start_dist = math.hypot(start_pos["x"] - goal["x"], start_pos["z"] - goal["z"]) if start_pos else args.dist
    progress = 0.0 if start_dist == 0 else (start_dist - min_dist) / start_dist
    summary = {
        "reached": reached,
        "startDist": round(start_dist, 1),
        "minDistToGoal": round(min_dist, 1),
        "maxMovedFromSpawn": round(max_moved, 1),
        "progressFraction": round(progress, 2),
    }
    print(json.dumps(summary, indent=2))
    # PASS if it reached, or made clear net progress (>50% of the way) — tolerates idle pauses.
    if reached or progress >= 0.5:
        print("[pathnav] PASS")
        return 0
    print(f"[pathnav] FAIL: no meaningful progress (moved {max_moved:.1f}b, "
          f"got {progress*100:.0f}% closer over {args.window:.0f}s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
