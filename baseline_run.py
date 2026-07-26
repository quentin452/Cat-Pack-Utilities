#!/usr/bin/env python3
"""Drive a REPRODUCIBLE traversal over the matoulib devtools RPC, for matou-engine T-02 baselines.

A baseline captured by hand is not a baseline. The two T-02 runs (cut flags OFF vs hardCut ON) differ only
in configuration, so anything else that differs — how far you walked, how fast, which way you looked —
lands in the numbers and cannot be separated from the effect being measured. This script performs the
identical action sequence every time, so the only variable left is the flag profile.

Deterministic by construction: fixed yaw sequence, fixed segment length, fixed sprint state. No randomness,
no wall-clock pacing decisions.

By default the traversal arms /noclip (FLIGHT + noClip) before the segments and disarms it after, so the
run no longer depends on WHERE the player spawns: a relaunch that respawns over a hillside or a ravine no
longer terrain-blocks a segment (<25 blocks moved), which used to make baseline_pair.py refuse to produce a
baseline. Both baselines of a pair must use the same --noclip/--altitude values, same as --headings.

Usage:
  python3 baseline_run.py --laps 4                  # ~4 min of noclip-sprinting traversal
  python3 baseline_run.py --laps 4 --dry-run        # print the plan, touch nothing
  python3 baseline_run.py --laps 4 --no-noclip      # old terrain-dependent ground traversal

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
DEFAULT_ALTITUDE = 120.0
# A sprinting player covers ~56 blocks in 200 ticks. Well under that means terrain stopped the run.
MIN_SEGMENT_BLOCKS = 25.0
# In flight, /noclip's contract says sprint doubles fly speed, so a segment covers ~2x the ground distance.
# Using the ground threshold under noclip would flag every real segment as "blocked" (false positive), so
# the floor scales with the same multiplier — the "did it actually move" check stays meaningful either way.
NOCLIP_SPEED_MULTIPLIER = 2.0


def rpc(path, **params):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_move_done(expected_ticks, poll=0.25):
    """Block until the move driver reports it is finished.

    /playermove INSTALLS a driver and returns immediately — it does not run for `ticks`. Firing the next
    segment without waiting silently overwrites the previous one, which is exactly how the first run of this
    script "completed" 160 seconds of traversal in 2 seconds and produced a meaningless baseline. So the wait
    is mandatory, and a timeout fails loudly rather than yielding numbers that look plausible.
    """
    deadline = time.time() + expected_ticks / 20.0 + 15.0
    seen_running = False
    while time.time() < deadline:
        try:
            tr = rpc("/playermove/trace")
        except Exception:
            time.sleep(poll)
            continue
        running = bool(tr.get("running"))
        if running:
            seen_running = True
        elif seen_running:
            return tr
        time.sleep(poll)
    sys.exit("segment did not finish within its deadline — refusing to produce a bogus baseline")


def check_altitude(trace, altitude, tolerance=8.0):
    """Fail loudly if the player left the altitude the traversal was armed at.

    noClip without flight is a fall through solid ground: the run keeps reporting ticks while the player drops,
    and past y=-64 it dies in the void. That happened, and the segment only surfaced as a generic timeout. The
    altitude is therefore checked per segment — a run that fell is not a slow run, it is a void run, and it must
    not reach the capture step.
    """
    s = trace.get("samples") or []
    if not s:
        return
    y = s[-1][2]
    if abs(y - altitude) > tolerance:
        sys.exit(f"player left the armed altitude (y={y:.1f}, armed y={altitude:.1f}) — the noclip hold failed"
                 f" and the run is falling, NOT traversing; refusing to produce a baseline")


def displacement(trace):
    """Horizontal distance actually covered by a segment.

    A sprint into a hillside still reports its full tick count while the player has not moved, so no chunks
    stream and the run measures an idle camera. Distance is the only signal that separates "traversed" from
    "pressed forward against a wall", so it is checked rather than assumed.
    """
    s = trace.get("samples") or []
    if len(s) < 2:
        return 0.0
    x0, z0 = s[0][1], s[0][3]
    x1, z1 = s[-1][1], s[-1][3]
    return ((x1 - x0) ** 2 + (z1 - z0) ** 2) ** 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--laps", type=int, default=4, help="laps of 4 segments (default 4 ~= 4 min)")
    ap.add_argument("--headings", default=",".join(str(int(y)) for y in YAWS),
                    help="comma-separated yaws to traverse. Pick ones that are CLEAR at the start point: a "
                         "blocked heading streams no chunks. Both baselines must use the same value.")
    ap.add_argument("--noclip", dest="noclip", action="store_true", default=True,
                    help="arm /noclip (FLIGHT + noClip) before the traversal so it no longer depends on "
                         "WHERE the player spawns (default: on). Both baselines must use the same value.")
    ap.add_argument("--no-noclip", dest="noclip", action="store_false",
                    help="disable noclip and fall back to the old terrain-dependent ground traversal.")
    ap.add_argument("--altitude", type=float, default=DEFAULT_ALTITUDE,
                    help=f"altitude to teleport to when arming noclip (default {DEFAULT_ALTITUDE:.0f}). "
                         "Both baselines must use the same value.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    yaws = [float(y) for y in args.headings.split(",") if y.strip()]
    segments = args.laps * len(yaws)
    seconds = segments * TICKS_PER_SEGMENT / 20.0
    noclip_desc = f", noclip @ y={args.altitude:.0f}" if args.noclip else ", no noclip (ground)"
    plan = (f"{args.laps} laps x headings[{args.headings}] x {TICKS_PER_SEGMENT} ticks "
            f"= {segments} segments, ~{seconds:.0f}s sprinting{noclip_desc}")
    print(f"scenario: {plan}")
    if args.dry_run:
        return

    try:
        pong = rpc("/ping")
    except Exception as exc:
        sys.exit(f"RPC unreachable at {BASE}: {exc}\n(client running? matoulib.rpc=true?)")
    if not pong.get("inWorld"):
        sys.exit("client is not in a world — enter one first, the traversal needs a loaded world")

    min_segment_blocks = MIN_SEGMENT_BLOCKS * (NOCLIP_SPEED_MULTIPLIER if args.noclip else 1.0)

    if args.noclip:
        armed = rpc("/noclip", on="true", y=args.altitude)
        if not armed.get("ok"):
            sys.exit(f"/noclip arm failed: {armed.get('error')}")

    try:
        blocked = []
        started = time.time()
        for lap in range(args.laps):
            for yaw in yaws:
                rpc("/look", yaw=yaw, pitch=PITCH)
                rpc("/playermove", forward=1, sprint="true", ticks=TICKS_PER_SEGMENT, yaw=yaw)
                tr = wait_move_done(TICKS_PER_SEGMENT)
                if args.noclip:
                    check_altitude(tr, args.altitude)
                dist = displacement(tr)
                blocked.append(dist < min_segment_blocks)
                flag = "  BLOCKED" if dist < min_segment_blocks else ""
                print(f"  lap {lap + 1}/{args.laps} yaw {yaw:>5.0f}: {tr.get('ticks', 0)} ticks,"
                      f" {dist:.0f} blocks{flag}")
        elapsed = time.time() - started
    finally:
        # Disarm even if a segment above threw (sys.exit in wait_move_done, RPC error, ...) — leaving the
        # player flying/noclipping into a bad baseline capture is worse than a partially-run traversal.
        if args.noclip:
            disarmed = rpc("/noclip", on="false")
            if not disarmed.get("ok"):
                print(f"  WARNING: /noclip disarm failed: {disarmed.get('error')}")

    expected = segments * TICKS_PER_SEGMENT / 20.0
    print(f"traversal complete in {elapsed:.0f}s (expected ~{expected:.0f}s)")
    if elapsed < expected * 0.5:
        sys.exit("ran far too fast — segments did not actually execute; the baseline is NOT valid")
    n_blocked = sum(blocked)
    if n_blocked:
        print(f"  WARNING: {n_blocked}/{len(blocked)} segments were terrain-blocked (<{min_segment_blocks:.0f} blocks)")
        if n_blocked > len(blocked) // 4:
            sys.exit("too many blocked segments — little chunk streaming happened; the baseline is NOT valid")
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
