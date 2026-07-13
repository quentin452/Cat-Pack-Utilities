#!/usr/bin/env python3
"""matou_bench.py — automated TPS/FPS A/B bench over FULL game-leg lifecycles (Prism Minimal-Matou).

chunkgen_bench.py measures INSIDE a running instance; this harness owns the whole leg:

  per leg:  kill game -> (fresh world) -> write instance.cfg JvmArgs -> launch Prism headless
            -> wait in-world -> warmup -> standard workload -> collect -> kill game

so a flag-set A/B (baseline vs -Dmatoulib.X) is reproducible end-to-end with no hand steps —
the gap that made every profile of the 2026-07-13 session a hand-driven one-off.

Standard workload (each phase optional via CLI):
  1. FPS station   — sample /fps at 1 Hz while the player stands still (render-loop health).
  2. tp-walk       — server-auth /tp steps across VIRGIN terrain (chunk serve/gen under movement);
                     /fps + /metrics sampled concurrently (TickSampler).
  3. gen region    — synchronous /worldgen/genprofile sweep (ms/chunk distribution).
  4. spawn window  — optional /spawnmob crowd, then a dwell measuring tick health under load.
  5. spark         — optional client profiler auto-dump: sparkc --start/--stop --save-to-file,
                     the .sparkprofile is copied into the artifact dir and summarized locally via
                     spark_parse.py (no web viewer).

Pairing: the autoworld ('saves/dev') is seed-0 FIXED (DevAutoWorld), so wiping it per leg replays
IDENTICAL terrain for every leg -> per-anchor paired A/B, same rigor doctrine as chunkgen_bench
(median+p95, spread-based INCONCLUSIVE verdicts, machine-idle guard).

Flag semantics: the BASE stack for every leg defaults to the instance.cfg's current JvmArgs — the
full modded stack the user actually plays (a bare-flags leg would silently bench a VANILLA GenLayer
overworld instead of the matou-owned one). A leg spec then overrides individual -D keys on top.

Traps baked in (CONTROLLER.md game-gate recipe):
  * SINGLE-INSTANCE — refuses to start while a game java (org.prismlauncher.EntryPoint) is alive;
    kills ONLY that process, never the user's Prism GUI (pkill -x prismlauncher would kill it).
  * instance.cfg is edited ONLY at 0 game process, backed up, and restored at the end (--keep-cfg
    to leave the last leg's flags in place).
  * options.txt maxFps/vsync — a vsync-capped fps hides the CPU delta; forced to 260/off for the
    bench (backed up + restored).
  * /fps 404 => the deployed matoulib jar predates the endpoint — deploy the fresh jar first.

Usage:
  python3 matou_bench.py run --leg "baseline:" \
      --leg "noglerr:-Dmatoulib.gpu.skipErrorChecks=true" [--spark] [--tag mybench]
  python3 matou_bench.py report <artifact.json> [<artifact.json> ...]
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import packenv as E
from chunkgen_bench import (rpc, wait_in_world, median, percentile, spread, idle_guard, TickSampler)

DEFAULT_RPC = "http://127.0.0.1:25580"
# The GAME java — NEVER the prismlauncher GUI. Bracket trick: a caller whose own cmdline embeds the
# pattern (bash -c wrappers, tail pipelines) would otherwise self-match and get killed.
GAME_PROC_PATTERN = "org.prismlauncher.EntryPoin[t]"
AUTOWORLD_SAVE = "dev"  # DevAutoWorld.WORLD_NAME, fixed seed 0 => identical terrain across wipes

# Flags the runner itself depends on — forced into every leg regardless of base/leg spec.
REQUIRED_FLAGS = "-Dmatoulib.rpc=true -Dmatoulib.autoworld=true"


def parse_dflags(s):
    """'-Da=b -Dc=d ...' -> ordered {key: token}. Non -D tokens keep their own token as key (verbatim)."""
    out = {}
    for tok in s.split():
        key = tok.split("=", 1)[0] if tok.startswith("-D") else tok
        out[key] = tok
    return out


def merge_flags(*flag_strs):
    """Later strings override earlier ones PER -D KEY (a leg toggles one flag on top of the base
    modded stack instead of replacing the whole JVM line — the whole point of an A/B leg)."""
    merged = {}
    for s in flag_strs:
        merged.update(parse_dflags(s or ""))
    return " ".join(merged.values())


def read_jvm_args(cfg_path):
    with open(cfg_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("JvmArgs="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


# ── process lifecycle ────────────────────────────────────────────────────────────────────────────


def game_pids():
    out = subprocess.run(["pgrep", "-f", GAME_PROC_PATTERN], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.isdigit()]


def kill_game(timeout_s=30):
    """Kill the game java only (SINGLE-INSTANCE rule). Escalates to -9 after timeout."""
    if not game_pids():
        return
    subprocess.run(["pkill", "-f", GAME_PROC_PATTERN])
    end = time.time() + timeout_s
    while time.time() < end:
        if not game_pids():
            return
        time.sleep(1)
    subprocess.run(["pkill", "-9", "-f", GAME_PROC_PATTERN])
    time.sleep(2)
    if game_pids():
        sys.exit("cannot kill the running game — refusing to continue (single-instance rule)")


def launch_game(instance_id, display, log_path, player):
    env = dict(os.environ, DISPLAY=display)
    with open(log_path, "ab") as log:
        subprocess.Popen(["setsid", "prismlauncher", "-l", instance_id, "-o", player],
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=env, start_new_session=True)


def wait_boot(rpc_base, deadline_s):
    """Poll /ping until inWorld, guarding that the game process stays alive (no zombie loop)."""
    end = time.time() + deadline_s
    booted_seen = False
    while time.time() < end:
        if not game_pids() and booted_seen:
            return False, "game process died during boot"
        if game_pids():
            booted_seen = True
        try:
            p = rpc(rpc_base, "/ping", timeout=3)
            if p.get("inWorld"):
                return True, None
        except Exception:
            pass
        time.sleep(3)
    return False, f"not in-world after {deadline_s}s"


# ── instance.cfg / options.txt (edit-at-0-process, backup + restore) ─────────────────────────────


class FileGuard:
    """Backup a file's exact bytes and restore them on close (unless told to keep)."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            self.original = fh.read()

    def restore(self):
        with open(self.path, "wb") as fh:
            fh.write(self.original)


def set_jvm_args(cfg_path, flags):
    if game_pids():
        sys.exit("refusing to edit instance.cfg while the game is running")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines(True)
    out, seen = [], False
    for line in lines:
        if line.startswith("JvmArgs="):
            out.append(f'JvmArgs="{flags}"\n')
            seen = True
        elif line.startswith("OverrideJavaArgs="):
            out.append("OverrideJavaArgs=true\n")
        else:
            out.append(line)
    if not seen:
        out.append(f'JvmArgs="{flags}"\n')
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.writelines(out)


def force_bench_options(options_path):
    """maxFps:260 + vsync off — a capped render loop masks the CPU delta (CONTROLLER.md trap)."""
    if not os.path.isfile(options_path):
        return
    with open(options_path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines(True)
    out = []
    for line in lines:
        if line.startswith("maxFps:"):
            out.append("maxFps:260\n")
        elif line.startswith("enableVsync:"):
            out.append("enableVsync:false\n")
        else:
            out.append(line)
    with open(options_path, "w", encoding="utf-8") as fh:
        fh.writelines(out)


def wipe_autoworld(mc_dir):
    save = os.path.join(mc_dir, "saves", AUTOWORLD_SAVE)
    if os.path.isdir(save):
        shutil.rmtree(save)


# ── samplers ─────────────────────────────────────────────────────────────────────────────────────


class FpsSampler(threading.Thread):
    """1 Hz /fps poll (debugFPS refreshes once per second — faster polling just re-reads)."""

    def __init__(self, rpc_base):
        super().__init__(daemon=True)
        self.rpc_base = rpc_base
        self._stop = threading.Event()
        self.samples = []
        self.missing = False  # /fps 404 => stale jar

    def run(self):
        while not self._stop.is_set():
            try:
                r = rpc(self.rpc_base, "/fps", timeout=5)
                if r.get("ok") and r.get("inWorld"):
                    self.samples.append(int(r["fps"]))
            except Exception as e:
                if "404" in str(e):
                    self.missing = True
                    return
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()


def sample_fps_window(rpc_base, seconds):
    s = FpsSampler(rpc_base)
    s.start()
    time.sleep(seconds)
    s.stop()
    s.join(timeout=5)
    return s


# ── workload phases ──────────────────────────────────────────────────────────────────────────────


def phase_station(rpc_base, args):
    print(f"  phase: FPS station {args.fps_window}s")
    rpc(rpc_base, "/tp", {"x": args.anchor_x, "y": args.y, "z": args.anchor_z, "pitch": 10})
    time.sleep(3)  # let chunk streaming settle before measuring
    s = sample_fps_window(rpc_base, args.fps_window)
    return {"fps": s.samples, "fpsMissing": s.missing}


def phase_walk(rpc_base, args):
    print(f"  phase: tp-walk {args.walk_steps} x {args.walk_stride} blocks")
    fps = FpsSampler(rpc_base)
    ticks = TickSampler(rpc_base)
    fps.start()
    ticks.start()
    t0 = time.time()
    for k in range(args.walk_steps):
        x = args.anchor_x + (k + 1) * args.walk_stride
        rpc(rpc_base, "/tp", {"x": x, "y": args.y, "z": args.anchor_z, "pitch": 10})
        time.sleep(args.walk_dwell)
    wall = time.time() - t0
    fps.stop()
    ticks.stop()
    fps.join(timeout=5)
    ticks.join(timeout=5)
    return {"wallS": round(wall, 1), "fps": fps.samples, "ticks": ticks.samples,
            "fpsMissing": fps.missing}


def phase_gen(rpc_base, args):
    print(f"  phase: genprofile r={args.gen_radius}")
    gx = args.anchor_x + (args.walk_steps + 4) * args.walk_stride + 1024  # clear of walked terrain
    ticks = TickSampler(rpc_base)
    ticks.start()
    r = rpc(rpc_base, "/worldgen/genprofile",
            {"x": gx, "z": args.anchor_z, "r": args.gen_radius, "dim": args.dim})
    ticks.stop()
    ticks.join(timeout=5)
    return {"genprofile": r, "ticks": ticks.samples}


def phase_spawn(rpc_base, args):
    print(f"  phase: spawn window {args.spawn_mobs} mobs, dwell {args.spawn_dwell}s")
    spawned = []
    for _ in range(args.spawn_mobs):
        try:
            spawned.append(rpc(rpc_base, "/spawnmob", {"id": args.spawn_id}).get("ok", False))
        except Exception:
            spawned.append(False)
    ticks = TickSampler(rpc_base)
    fps = FpsSampler(rpc_base)
    ticks.start()
    fps.start()
    time.sleep(args.spawn_dwell)
    ticks.stop()
    fps.stop()
    ticks.join(timeout=5)
    fps.join(timeout=5)
    return {"spawnedOk": sum(1 for s in spawned if s), "ticks": ticks.samples, "fps": fps.samples}


# ── spark auto-dump ──────────────────────────────────────────────────────────────────────────────


def spark_start(rpc_base, thread_name):
    """Default: NO --thread flag (sparkc samples the client thread). Forge's executeCommand splits
    args on spaces, so a quoted 'Client thread' arrives as '\"Client' + 'thread\"' -> spark matches
    no thread and saves an EMPTY profile (~1.5 KB, found at the gate). Thread filtering belongs to
    spark_parse at analysis time; only pass --spark-thread for single-word names."""
    q = "sparkc profiler --start"
    if thread_name:
        q += f" --thread {thread_name}"
    r = rpc(rpc_base, "/clientcmd", {"q": q})
    return bool(r.get("ok"))


def spark_stop_save(rpc_base, mc_dir, dest_dir, leg_name):
    """Stop with --save-to-file, then locate the .sparkprofile (chatlog path, else newest on disk)."""
    rpc(rpc_base, "/clientcmd", {"q": "sparkc profiler --stop --save-to-file"})
    time.sleep(3)
    path = None
    try:
        lines = rpc(rpc_base, "/chatlog", {"n": 15}).get("lines", [])
        for line in reversed(lines):
            m = re.search(r"(\S+\.sparkprofile)", line)
            if m:
                path = m.group(1)
                break
    except Exception:
        pass
    if path and not os.path.isabs(path):
        cand = os.path.join(mc_dir, path)
        path = cand if os.path.isfile(cand) else None
    if not path or not os.path.isfile(path):
        recent = sorted(glob.glob(os.path.join(mc_dir, "**", "*.sparkprofile"), recursive=True),
                        key=os.path.getmtime)
        path = recent[-1] if recent else None
    if not path:
        print("  spark: no .sparkprofile found (save-to-file unsupported or profiler not running?)")
        return None
    dest = os.path.join(dest_dir, f"{leg_name}.sparkprofile")
    shutil.copy2(path, dest)
    return dest


def spark_summary(profile_path, top=15):
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spark_parse.py")
    try:
        out = subprocess.run([sys.executable, tool, profile_path, "--top", str(top)],
                             capture_output=True, text=True, timeout=120)
        return out.stdout.strip()
    except Exception as e:
        return f"(spark_parse failed: {e})"


# ── leg lifecycle ────────────────────────────────────────────────────────────────────────────────


def run_leg(name, leg_flags, base_flags, args, paths, cfg_guard):
    print(f"\n== leg '{name}'  overrides: {leg_flags or '(base only)'}")
    kill_game()
    if not args.keep_world:
        wipe_autoworld(paths["mc"])
    flags = merge_flags(base_flags, leg_flags, REQUIRED_FLAGS)
    set_jvm_args(paths["cfg"], flags)
    if not args.no_touch_options:
        force_bench_options(os.path.join(paths["mc"], "options.txt"))

    log_path = os.path.join(paths["out"], f"{name}.boot.log")
    launch_game(args.instance, args.display, log_path, args.player)
    ok, err = wait_boot(args.rpc, args.boot_wait)
    if not ok:
        kill_game()
        sys.exit(f"leg '{name}' failed to boot: {err} (see {log_path})")
    print(f"  in-world; warmup {args.warmup}s")
    time.sleep(args.warmup)

    leg = {"name": name, "flags": flags, "phases": {}}
    if args.spark:
        leg["sparkStarted"] = spark_start(args.rpc, args.spark_thread)

    if args.fps_window > 0:
        leg["phases"]["station"] = phase_station(args.rpc, args)
    if args.walk_steps > 0:
        leg["phases"]["walk"] = phase_walk(args.rpc, args)
    if args.gen_radius > 0:
        leg["phases"]["gen"] = phase_gen(args.rpc, args)
    if args.spawn_mobs > 0:
        leg["phases"]["spawn"] = phase_spawn(args.rpc, args)

    if args.spark and leg.get("sparkStarted"):
        prof = spark_stop_save(args.rpc, paths["mc"], paths["out"], name)
        if prof:
            leg["sparkProfile"] = prof
            leg["sparkTop"] = spark_summary(prof)

    # end-of-leg collectors — tolerant: absent endpoint (flag off / older jar) is recorded, not fatal
    collectors = {}
    for ep in ("/metrics", "/chunkscheduler", "/chunk/capturestats", "/execstats", "/tickstats"):
        try:
            collectors[ep] = rpc(args.rpc, ep, timeout=30)
        except Exception as e:
            collectors[ep] = {"unavailable": str(e)}
    leg["collect"] = collectors

    kill_game()
    return leg


# ── summarize + report ───────────────────────────────────────────────────────────────────────────


def _fps_stats(samples):
    if not samples:
        return None
    return {"median": median(samples), "p05": percentile(samples, 0.05),
            "spread": spread(samples), "n": len(samples)}


def _tick_stats(tick_samples):
    ms = [s["meanTickMs"] for s in tick_samples if isinstance(s, dict) and "meanTickMs" in s]
    tps = [s["tps"] for s in tick_samples if isinstance(s, dict) and "tps" in s]
    if not ms:
        return None
    return {"meanTickMsMedian": median(ms), "meanTickMsP95": percentile(ms, 0.95),
            "spread": spread(ms), "tpsMedian": median(tps) if tps else None, "n": len(ms)}


def summarize(leg):
    p = leg["phases"]
    s = {}
    if "station" in p:
        s["fpsStation"] = _fps_stats(p["station"]["fps"])
    if "walk" in p:
        s["fpsWalk"] = _fps_stats(p["walk"]["fps"])
        s["tickWalk"] = _tick_stats(p["walk"]["ticks"])
    if "gen" in p:
        g = p["gen"]["genprofile"]
        if g.get("ok"):
            # RPC JSON serializes some numbers as strings ("1.279") — coerce here, once.
            s["genMsPerChunk"] = _num(g.get("avgMs"))
            s["genWallMs"] = _num(g.get("elapsedMs"))
            s["genChunks"] = _num(g.get("chunks"))
        s["tickGen"] = _tick_stats(p["gen"]["ticks"])
    if "spawn" in p:
        s["tickSpawn"] = _tick_stats(p["spawn"]["ticks"])
        s["fpsSpawn"] = _fps_stats(p["spawn"]["fps"])
    return s


def _num(v):
    """Coerce RPC values that may arrive as strings; None stays None."""
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _fmt(v):
    return "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _delta_line(base, cand, noise, higher_is_better):
    """Delta % + verdict; a delta inside the noise band (spread) is INCONCLUSIVE, not a win."""
    base, cand = _num(base), _num(cand)  # pre-fix artifacts carry stringly-typed gen numbers
    if base in (None, 0) or cand is None:
        return "-"
    d = (cand - base) / abs(base) * 100.0
    verdict = ""
    if noise is not None and abs(cand - base) <= noise:
        verdict = " (INCONCLUSIVE: within noise)"
    elif (d > 0) == higher_is_better and abs(d) >= 1.0:
        verdict = " (better)"
    elif abs(d) >= 1.0:
        verdict = " (worse)"
    return f"{d:+.1f}%{verdict}"


ROWS = [  # (label, summary path, higher_is_better)
    ("FPS station median", ("fpsStation", "median"), True),
    ("FPS station p05", ("fpsStation", "p05"), True),
    ("FPS walk median", ("fpsWalk", "median"), True),
    ("Tick walk meanMs median", ("tickWalk", "meanTickMsMedian"), False),
    ("TPS walk median", ("tickWalk", "tpsMedian"), True),
    ("Gen ms/chunk", ("genMsPerChunk",), False),
    ("Gen wall ms", ("genWallMs",), False),
    ("Tick spawn meanMs median", ("tickSpawn", "meanTickMsMedian"), False),
]

NOISE_KEY = {"fpsStation": ("fpsStation", "spread"), "fpsWalk": ("fpsWalk", "spread"),
             "tickWalk": ("tickWalk", "spread"), "tickSpawn": ("tickSpawn", "spread")}


def _get(summary, path):
    cur = summary
    for k in path:
        if not isinstance(cur, dict) or cur.get(k) is None:
            return None
        cur = cur[k]
    return cur


def render_report(artifact):
    legs = artifact["legs"]
    base = legs[0]
    lines = [f"# matou_bench — {artifact['tag']}",
             "",
             f"- date: {artifact['date']}  instance: `{artifact['instance']}`",
             f"- workload: station {artifact['args']['fps_window']}s · walk "
             f"{artifact['args']['walk_steps']}×{artifact['args']['walk_stride']} · gen r="
             f"{artifact['args']['gen_radius']} · spawn {artifact['args']['spawn_mobs']}",
             f"- idle guard: {json.dumps(artifact.get('guard', {}).get('cpu', {}))}",
             ""]
    for leg in legs:
        lines.append(f"- leg `{leg['name']}`: `{leg['flags']}`")
    lines += ["", f"| metric | {' | '.join(l['name'] for l in legs)} |"
                  f"{' delta vs ' + base['name'] + ' |' if len(legs) > 1 else ''}",
              "|---|" + "---|" * (len(legs) + (1 if len(legs) > 1 else 0))]
    for label, path, hib in ROWS:
        vals = [_get(l["summary"], path) for l in legs]
        if all(v is None for v in vals):
            continue
        row = f"| {label} | " + " | ".join(_fmt(v) for v in vals) + " |"
        if len(legs) > 1:
            noise = _get(base["summary"], NOISE_KEY.get(path[0], ("",))) if path[0] in NOISE_KEY else None
            row += f" {_delta_line(vals[0], vals[-1], noise, hib)} |"
        lines.append(row)
    for leg in legs:
        if leg.get("sparkTop"):
            lines += ["", f"## spark top — {leg['name']}", "```", leg["sparkTop"], "```"]
    return "\n".join(lines) + "\n"


# ── commands ─────────────────────────────────────────────────────────────────────────────────────


def cmd_run(args):
    legs_spec = []
    for spec in args.leg:
        name, _, flags = spec.partition(":")
        if not name:
            sys.exit(f"bad --leg '{spec}' (want name:flags)")
        legs_spec.append((name.strip(), flags.strip()))
    if not legs_spec:
        sys.exit("no --leg given")

    inst_root = os.path.join(E.PRISM_ROOT, args.instance)
    paths = {"mc": os.path.join(inst_root, "minecraft"),
             "cfg": os.path.join(inst_root, "instance.cfg")}
    if not os.path.isfile(paths["cfg"]):
        sys.exit(f"instance.cfg not found: {paths['cfg']}")

    tag = args.tag or ("bench-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M"))
    paths["out"] = os.path.join(E.BENCH_CAPTURES, tag)
    os.makedirs(paths["out"], exist_ok=True)

    ok, guard = idle_guard(args.cpu_threshold, args.gpu_threshold, args.force)
    if not ok and not args.force:
        sys.exit(2)

    cfg_guard = FileGuard(paths["cfg"])
    opt_path = os.path.join(paths["mc"], "options.txt")
    opt_guard = FileGuard(opt_path) if os.path.isfile(opt_path) else None
    # Base = the instance's CURRENT modded flag stack (bench what the user actually plays), unless
    # overridden. Base-only legs boot the FULL matou stack — never a bare vanilla overworld.
    base_flags = args.base_flags if args.base_flags is not None else read_jvm_args(paths["cfg"])
    print(f"base flags: {merge_flags(base_flags, REQUIRED_FLAGS)}")
    artifact = {"tag": tag, "date": datetime.now(timezone.utc).isoformat(),
                "instance": args.instance, "guard": guard, "baseFlags": base_flags,
                "args": {k: getattr(args, k) for k in
                         ("fps_window", "walk_steps", "walk_stride", "gen_radius",
                          "spawn_mobs", "warmup", "anchor_x", "anchor_z")},
                "legs": []}
    try:
        for name, flags in legs_spec:
            leg = run_leg(name, flags, base_flags, args, paths, cfg_guard)
            leg["summary"] = summarize(leg)
            artifact["legs"].append(leg)
            if leg["phases"].get("station", {}).get("fpsMissing"):
                print("  WARN: /fps returned 404 — deployed matoulib jar predates the endpoint")
    finally:
        kill_game()
        if not args.keep_cfg:
            cfg_guard.restore()
            if opt_guard:
                opt_guard.restore()
            print("\nrestored instance.cfg + options.txt")
        else:
            print("\n--keep-cfg: instance.cfg left with the LAST leg's flags")

    art_path = os.path.join(paths["out"], "artifact.json")
    with open(art_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)
    report = render_report(artifact)
    rep_path = os.path.join(paths["out"], "report.md")
    with open(rep_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"\nartifact: {art_path}\nreport:   {rep_path}\n")
    print(report)


def cmd_report(args):
    for path in args.artifact:
        with open(path, encoding="utf-8") as fh:
            print(render_report(json.load(fh)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run legs end-to-end and write artifact + report")
    r.add_argument("--leg", action="append", default=[],
                   help='leg spec "name:-Dflag1=... -Dflag2=..." — overrides applied PER -D KEY on top of the '
                        "base stack (empty = base as-is). First leg = baseline for deltas.")
    r.add_argument("--base-flags", default=None,
                   help="base JVM flag stack for every leg (default: the instance.cfg's current JvmArgs = "
                        "the modded stack the user plays)")
    r.add_argument("--instance", default="Minimal-Matou")
    r.add_argument("--rpc", default=DEFAULT_RPC)
    r.add_argument("--display", default=os.environ.get("DISPLAY", ":2"))
    r.add_argument("--player", default="BenchBot")
    r.add_argument("--tag", default=None)
    r.add_argument("--boot-wait", type=int, default=420, help="s to wait for in-world (fresh world gens)")
    r.add_argument("--warmup", type=int, default=20, help="s in-world before measuring (JIT + streaming)")
    r.add_argument("--fps-window", type=int, default=30, help="s of stationary FPS sampling (0 = skip)")
    r.add_argument("--walk-steps", type=int, default=20, help="tp-walk steps (0 = skip)")
    r.add_argument("--walk-stride", type=int, default=32, help="blocks per tp step")
    r.add_argument("--walk-dwell", type=float, default=2.0, help="s to dwell per step")
    r.add_argument("--gen-radius", type=int, default=8, help="genprofile chunk radius (0 = skip)")
    r.add_argument("--spawn-mobs", type=int, default=0, help="crowd size for the spawn window (0 = skip)")
    r.add_argument("--spawn-id", default="Zombie")
    r.add_argument("--spawn-dwell", type=int, default=20)
    r.add_argument("--anchor-x", type=int, default=20000, help="virgin-terrain anchor (fixed seed pairs legs)")
    r.add_argument("--anchor-z", type=int, default=20000)
    r.add_argument("--y", type=int, default=90)
    r.add_argument("--dim", type=int, default=0)
    r.add_argument("--spark", action="store_true", help="client spark profiler auto-dump per leg")
    r.add_argument("--spark-thread", default=None,
                   help="optional spark --thread filter (single word only — Forge splits args on spaces)")
    r.add_argument("--keep-world", action="store_true", help="do NOT wipe saves/dev between legs")
    r.add_argument("--keep-cfg", action="store_true", help="leave last leg's JvmArgs in instance.cfg")
    r.add_argument("--no-touch-options", action="store_true")
    r.add_argument("--cpu-threshold", type=float, default=25.0)
    r.add_argument("--gpu-threshold", type=float, default=30.0)
    r.add_argument("--force", action="store_true", help="override the machine-idle guard")
    r.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="re-render markdown report(s) from artifact.json")
    p.add_argument("artifact", nargs="+")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
