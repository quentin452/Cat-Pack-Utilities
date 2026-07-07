#!/usr/bin/env python3
"""
perf_bench.py — "where is the time lost" profiler for the matoulib de-risk battery.

De-risks the Rust sim layer BEFORE writing any Rust: it answers "at N mobs, how much of the
server tick is AI (offloadable to Rust) vs collision / sync / tick-overhead?". If AI dominates,
the sim layer wins; if collision/render dominates, the wrong layer was picked (pivot to RHI).
Same harness profiles worldgen (--worldgen) for a later "where is gen time lost" pass.

Method: async-profiler (asprof, CPU sampling, no safepoint bias) attached to the running MC JVM
while a scenario runs, then classify the folded stacks into subsystem buckets.

Assumes a matoulib-RPC instance is ALREADY booted + in-world (127.0.0.1:25580) — boot it first
per the devtools skill (minimal instance = ~90s, clean OaT+matoulib signal). This script only
spawns the load, profiles, and classifies; it never boots/kills the game (single-instance rule).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

RPC = "http://127.0.0.1:25580"
ASPROF = os.path.expanduser("~/Documents/GitHub/async-profiler/build/bin/asprof")

# Subsystem buckets: a sample is attributed to the bucket of its DEEPEST matching frame
# (= where the CPU actually is). Order within a list does not matter; substrings are matched
# against every frame. SRG names (func_*) included because production runs obfuscated.
BUCKETS = [
    ("AI/pathfinding", ["entity.ai.", "EntityAI", "PathNavigate", "PathFinder", "Pathfinding",
                        "AsyncPathfind", "pathfinding", "IEntitySelector", "func_75842_i"]),
    ("collision/physics", ["moveEntity", "getCollidingBoundingBoxes", "func_72945_a", "AxisAlignedBB",
                           "func_70091_d", "func_70055_a", "handleLavaMovement", "func_145771_j"]),
    ("entity-sync/track", ["EntityTrackerEntry", "EntityTracker", "DataWatcher", "func_151260_c",
                           "func_73256_a", "Packet", "NetworkManager"]),
    ("worldgen", ["ChunkProviderServer", "func_73154_d", "populate", "IWorldGenerator", "MapGen",
                  "generateWorld", "func_147424_a", "provideChunk", "func_73158_c"]),
    ("render(client)", ["RenderManager", "doRender", "GeckoLib", "geckolib", "Tessellator",
                        "EntityRenderer", "RenderGlobal", "func_78471_a", "func_147589_a"]),
    ("entity-tick-other", ["onLivingUpdate", "onEntityUpdate", "func_70071_h_", "func_70636_d",
                           "updateEntities", "func_72939_s", "EntityLivingBase", "onUpdate"]),
    ("chunk/light/save", ["func_147451_t", "updateLightByType", "AnvilChunkLoader", "checkLight",
                          "func_76590_a", "NibbleArray", "ExtendedBlockStorage"]),
]


def rpc(path, timeout=15):
    with urllib.request.urlopen(RPC + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_in_world(deadline_s=180):
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            if rpc("/ping", timeout=3).get("inWorld"):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def mean_tick_ms():
    """/metrics reports mean server tick time; fall back to the raw key names it exposes."""
    m = rpc("/metrics")
    for k in ("meanTickMs", "meanMs", "tickMs", "msPerTick", "meanTickTimeMs"):
        if k in m:
            return float(m[k])
    # nested?
    for v in m.values():
        if isinstance(v, dict):
            for k in ("meanMs", "meanTickMs"):
                if k in v:
                    return float(v[k])
    return None


def player_xz():
    """Extract player x,z from /state (robust to a few shapes: top-level, nested pos/player)."""
    st = rpc("/state")
    for cand in (st, st.get("pos"), st.get("player"), (st.get("players") or [{}])[0]):
        if isinstance(cand, dict) and "x" in cand and "z" in cand:
            return float(cand["x"]), float(cand["z"])
    return 0.0, 0.0


def find_pid(pattern):
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    pids = [int(p) for p in out.stdout.split() if p.isdigit()]
    # drop our own pgrep / this script
    me = {os.getpid(), os.getppid()}
    pids = [p for p in pids if p not in me]
    return pids[0] if pids else None


def profile(pid, seconds, out_path):
    """asprof: CPU-sample the JVM for `seconds`, dump folded (collapsed) stacks."""
    cmd = [ASPROF, "-e", "cpu", "-d", str(seconds), "-o", "collapsed", "-f", out_path, str(pid)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("asprof failed:\n" + r.stdout + r.stderr)
    return out_path


def classify(folded_path):
    """Each folded line = 'frameA;frameB;...;leaf count'. Attribute count to the bucket of the
    DEEPEST matching frame. Returns {bucket: samples}, total, and the unclassified count."""
    tally = {name: 0 for name, _ in BUCKETS}
    tally_other = 0
    total = 0
    with open(folded_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            sp = line.rfind(" ")
            if sp < 0:
                continue
            try:
                count = int(line[sp + 1:])
            except ValueError:
                continue
            total += count
            frames = line[:sp].split(";")
            hit = None
            for frame in reversed(frames):  # deepest first
                for name, needles in BUCKETS:
                    if any(n in frame for n in needles):
                        hit = name
                        break
                if hit:
                    break
            if hit:
                tally[hit] += count
            else:
                tally_other += count
    return tally, tally_other, total


def report(tally, other, total, header):
    print("\n=== " + header + " ===")
    if total == 0:
        print("no samples")
        return
    rows = sorted(tally.items(), key=lambda kv: -kv[1])
    for name, n in rows:
        if n:
            print(f"  {name:20s} {100.0 * n / total:5.1f}%  ({n})")
    if other:
        print(f"  {'(unclassified)':20s} {100.0 * other / total:5.1f}%  ({other})")
    ai = tally.get("AI/pathfinding", 0)
    print(f"\n  -> AI/pathfinding = {100.0 * ai / total:.1f}% of CPU samples")
    if ai / total >= 0.35:
        print("  -> VERDICT: AI is a major cost — Rust sim layer likely wins. Proceed to slice/infra.")
    else:
        print("  -> VERDICT: AI is NOT dominant — a Rust sim wins little here. Re-check the target")
        print("     (collision? render? sync?) before committing to the sim layer.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=200, help="mobs to spawn (mob mode)")
    ap.add_argument("--def", dest="defid", default="gigapig", help="mob def id")
    ap.add_argument("--radius", type=float, default=20.0)
    ap.add_argument("--seconds", type=int, default=30, help="profile duration")
    ap.add_argument("--pid", type=int, default=None, help="MC JVM pid (else autodetect)")
    ap.add_argument("--pid-pattern", default="Minimal-Pathfinding|minimal.*arg",
                    help="pgrep pattern to find the MC JVM")
    ap.add_argument("--worldgen", action="store_true",
                    help="worldgen mode: skip mob spawn, just profile (caller drives gen, e.g. /genchunks)")
    ap.add_argument("--out", default="/tmp/perf_bench.folded")
    args = ap.parse_args()

    if not os.path.exists(ASPROF):
        sys.exit("asprof not found at " + ASPROF + " (build async-profiler first)")
    if not wait_in_world():
        sys.exit("RPC not in-world at " + RPC + " — boot the instance first (devtools skill)")

    pid = args.pid or find_pid(args.pid_pattern)
    if not pid:
        sys.exit("MC JVM pid not found (pattern: " + args.pid_pattern + ") — pass --pid")
    print(f"MC JVM pid = {pid}")

    if args.worldgen:
        print(f"worldgen mode: profiling {args.seconds}s (drive gen NOW: /genchunks or move around)...")
        profile(pid, args.seconds, args.out)
        tally, other, total = classify(args.out)
        report(tally, other, total, f"worldgen CPU breakdown ({args.seconds}s)")
        return

    base = mean_tick_ms()
    print(f"baseline mean tick = {base} ms")
    # spawn AT the player: on real terrain, mobs in unloaded chunks never tick (false "no AI").
    px, pz = player_xz()
    print(f"spawning {args.count}x '{args.defid}' at player ({px:.0f},{pz:.0f}), radius {args.radius}...")
    r = rpc(f"/stress?def={args.defid}&count={args.count}&x={px}&z={pz}&radius={args.radius}")
    if not r.get("ok"):
        sys.exit("stress failed: " + json.dumps(r))
    time.sleep(5)  # let AI/pathing reach steady state
    loaded = mean_tick_ms()
    print(f"loaded mean tick  = {loaded} ms  (delta = "
          f"{None if (loaded is None or base is None) else round(loaded - base, 2)} ms for {args.count} mobs)")
    print(f"profiling {args.seconds}s...")
    profile(pid, args.seconds, args.out)
    tally, other, total = classify(args.out)
    report(tally, other, total, f"{args.count} mobs — server-tick CPU breakdown ({args.seconds}s)")


if __name__ == "__main__":
    main()
