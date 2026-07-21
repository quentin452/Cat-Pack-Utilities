#!/usr/bin/env python3
"""gate.py — one-call game-gate launch / flags / stop for the matoulib Prism TEST instances.

Encapsulates the fragile single-instance launch boilerplate — kill-stale, FML-corrupt-autoworld
recovery, sandboxed detach, poll-to-in-world-ready, graceful shutdown — so a game-gate agent runs ONE
command instead of ~15 and never re-derives the recipe. Paths + RPC endpoint come from packenv (single
source). Bakes in the hard-won lessons: jps process guard (bracket-pgrep fallback), /exit before pkill,
delete a corrupt disposable test world instead of stalling on the FML "backup level.dat" screen.

RUN EACH INVOCATION WITH THE SANDBOX DISABLED (the game runs detached on the real host; the launch poll
lives inside this process, so the game survives after the command returns).

  python3 gate.py launch <instance-id> [--timeout 180] [--no-fix-corrupt] [--player NAME]
      kill stale game -> repair a corrupt autoworld save -> launch -> BLOCK until the RPC reports in-world.
      prints 'READY pos=x,y,z dim=D' (exit 0) or 'FAILED: <reason>' + a log tail (exit 1).
  python3 gate.py flags <instance-id> --set key=val [key=val ...]
      set matoulib.* flags in <instance>/minecraft/config/matoulib.properties (add or replace a line,
      keep the rest, LF endings). Idempotent.
  python3 gate.py stop [--timeout 8]
      graceful GET /exit; fall back to killing org.prismlauncher.EntryPoint only if it does not respond.
  python3 gate.py status
      print whether a game JVM is alive + whether the RPC answers (+ in-world pos if any).

Note: this drives the CLIENT that binds RPC on packenv RPC_HOST:RPC_PORT (single-instance). Never run two
game instances at once. For instances not under PRISM_ROOT, pass an absolute path as <instance-id>.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import packenv as E  # noqa: E402

GAME_MAIN = "org.prismlauncher.EntryPoint"
# The live game is TWO cooperating JVMs: the Prism launcher wrapper (EntryPoint) AND the actual instance
# JVM it spawns, whose main class is LaunchWrapper. Matching only EntryPoint missed the in-world game JVM
# (jps listed it but not under EntryPoint) -> game_pids() returned [] mid-boot -> a FALSE "boot-failed"
# (and kill_stale left the real JVM alive). Match either main class.
GAME_MAINS = ("org.prismlauncher.EntryPoint", "net.minecraft.launchwrapper.Launch")


def _mc(instance):
    """The instance minecraft/ dir (accepts a bare Prism id or an absolute instance-root path)."""
    if os.path.isabs(instance):
        root = instance
    else:
        root = os.path.join(E.PRISM_ROOT, instance)
    mc = os.path.join(root, "minecraft")
    return mc if os.path.isdir(mc) else root  # curseforge instances have mods/ at the root, no minecraft/


def _props(instance):
    return os.path.join(_mc(instance), "config", "matoulib.properties")


def _rpc(path):
    return "http://%s:%d%s" % (E.RPC_HOST, E.RPC_PORT, path)


def _get(path, timeout=4):
    """GET an RPC endpoint; return parsed JSON (or raw text) or None on any failure (boot = refused)."""
    try:
        with urllib.request.urlopen(_rpc(path), timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return body


def game_pids():
    """PIDs of the running game JVM. jps is authoritative (a JVM list; a shell grep can't self-match it);
    fall back to a bracket-pgrep (the [o] stops pgrep matching its own cmdline)."""
    try:
        out = subprocess.run(["jps", "-l"], capture_output=True, text=True, timeout=10).stdout
        pids = [int(ln.split()[0]) for ln in out.splitlines()
                if any(m in ln for m in GAME_MAINS)]
        if pids:
            return pids
        # jps ran but matched nothing — DON'T trust that as "no game" (jps may list the instance JVM under a
        # main class we don't recognize, or not at all). Fall through to the cmdline pgrep below. The old
        # `if pids or out` returned [] here -> a live in-world game read as dead (false boot-failed + kill miss).
    except Exception:
        pass
    # bracket-pgrep each main class on the full cmdline ([o]/[n] stops pgrep matching its own cmdline). The
    # instance JVM's cmdline always ends in `net.minecraft.launchwrapper.Launch ...` even when jps hides it.
    pids = set()
    for pat in ("[o]rg.prismlauncher.EntryPoint", "[n]et.minecraft.launchwrapper.Launch"):
        try:
            out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True, timeout=10).stdout
            pids.update(int(x) for x in out.split())
        except Exception:
            pass
    return list(pids)


def kill_stale(hard_wait=8):
    """TERM then KILL any live game JVM; return only once 0 remain (single-instance guarantee)."""
    pids = game_pids()
    if not pids:
        return
    print("gate: killing %d stale game JVM(s): %s" % (len(pids), pids))
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(hard_wait):
        time.sleep(1)
        if not game_pids():
            return
    for p in game_pids():
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(1)
    if game_pids():
        print("gate: WARNING stale game JVM survived SIGKILL")


def repair_corrupt(instance):
    """Delete a corrupt DISPOSABLE test world (main level.dat gone, only level.dat_old / _mcr left) so the
    autoworld regenerates instead of stalling forever on the FML 'backup level.dat is being used' screen.
    Test-instance worlds are throwaway. Returns the names deleted (logged loudly)."""
    saves = os.path.join(_mc(instance), "saves")
    if not os.path.isdir(saves):
        return []
    repaired = []
    for name in os.listdir(saves):
        wd = os.path.join(saves, name)
        if not os.path.isdir(wd):
            continue
        has_main = os.path.exists(os.path.join(wd, "level.dat"))
        has_backup = os.path.exists(os.path.join(wd, "level.dat_old")) or \
            os.path.exists(os.path.join(wd, "level.dat_mcr"))
        if not has_main and has_backup:
            shutil.rmtree(wd, ignore_errors=True)
            repaired.append(name)
            print("gate: REPAIRED corrupt world '%s' (level.dat missing) -> deleted, autoworld regenerates" % name)
    return repaired


def _logfile(instance):
    base = os.environ.get("CLAUDE_JOB_DIR")
    d = os.path.join(base, "tmp") if base else "/tmp"
    os.makedirs(d, exist_ok=True)
    tag = os.path.basename(instance.rstrip("/")).replace(" ", "_")
    return os.path.join(d, "gate-%s.log" % tag)


def _log_tail(path, n=25):
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return "(no log)"


def launch(args):
    mc = _mc(args.instance)
    if not os.path.isdir(mc):
        print("FAILED: instance dir not found: %s" % mc)
        return 1
    kill_stale()
    if not args.no_fix_corrupt:
        repair_corrupt(args.instance)

    log = _logfile(args.instance)
    cmd = [E.PRISM_BIN, "-l", os.path.basename(args.instance.rstrip("/")), "-o", args.player]
    env = dict(os.environ, DISPLAY=E.GATE_DISPLAY)
    print("gate: launching %s (DISPLAY=%s, log=%s)" % (" ".join(cmd), E.GATE_DISPLAY, log))
    with open(log, "w") as lf:
        subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)

    deadline = time.time() + args.timeout
    saw_rpc = False
    while time.time() < deadline:
        if not game_pids():
            # A dead process early = boot crash; give it a couple seconds after start before trusting this.
            if time.time() > deadline - args.timeout + 20:
                print("FAILED: game process exited during boot\n--- log tail ---\n" + _log_tail(log))
                return 1
        st = _get("/state")
        if st is not None:
            saw_rpc = True
            if isinstance(st, dict):
                if st.get("playerDead"):
                    print("FAILED: player is DEAD at spawn (world/health issue)\n--- log tail ---\n" + _log_tail(log))
                    return 1
                pl = st.get("player")
                screen = st.get("currentScreen")
                if pl and not screen:
                    print("READY pos=%.3f,%.3f,%.3f dim=%s" % (pl["x"], pl["y"], pl["z"], pl.get("dim")))
                    return 0
        time.sleep(3)

    tail = _log_tail(log)
    hint = ""
    if "backup level.dat" in tail:
        hint = " (FML backup-level.dat screen — a corrupt world slipped the repair; delete it and relaunch)"
    elif not saw_rpc:
        hint = " (RPC never answered — check matoulib.rpc=true + the jar is deployed)"
    else:
        hint = " (RPC up but never reached in-world — stuck on a screen? autoworld off?)"
    print("FAILED: timeout after %ds%s\n--- log tail ---\n%s" % (args.timeout, hint, tail))
    return 1


def flags(args):
    path = _props(args.instance)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            lines = f.read().splitlines()
    updates = {}
    for kv in args.set:
        if "=" not in kv:
            print("FAILED: --set expects key=val, got %r" % kv)
            return 1
        k, v = kv.split("=", 1)
        updates[k.strip()] = v.strip()
    seen = set()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                lines[i] = "%s=%s" % (k, updates[k])
                seen.add(k)
    for k, v in updates.items():
        if k not in seen:
            lines.append("%s=%s" % (k, v))
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("gate: set %s in %s" % (", ".join("%s=%s" % (k, v) for k, v in updates.items()), path))
    return 0


def stop(args):
    pids = game_pids()
    if not pids:
        print("gate: no game running")
        return 0
    r = _get("/exit", timeout=args.timeout)
    if r is not None:
        for _ in range(args.timeout):
            time.sleep(1)
            if not game_pids():
                print("gate: stopped gracefully via /exit")
                return 0
    print("gate: /exit did not stop it; killing")
    kill_stale()
    return 0


def status(args):
    pids = game_pids()
    st = _get("/state")
    alive = "ALIVE pids=%s" % pids if pids else "no game JVM"
    if isinstance(st, dict) and st.get("player"):
        pl = st["player"]
        rpc = "RPC in-world pos=%.1f,%.1f,%.1f dim=%s" % (pl["x"], pl["y"], pl["z"], pl.get("dim"))
    elif st is not None:
        rpc = "RPC up (not in-world: screen=%s)" % (st.get("currentScreen") if isinstance(st, dict) else "?")
    else:
        rpc = "RPC not answering"
    print("gate status: %s | %s" % (alive, rpc))
    return 0


def main():
    ap = argparse.ArgumentParser(description="one-call game-gate launch/flags/stop (matoulib Prism test instances)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("launch", help="kill stale + repair + launch + block until in-world")
    pl.add_argument("instance", help="Prism instance id (under PRISM_ROOT) or an absolute instance path")
    pl.add_argument("--timeout", type=int, default=180, help="seconds to wait for in-world (default 180)")
    pl.add_argument("--no-fix-corrupt", action="store_true", help="do NOT delete a corrupt autoworld save")
    pl.add_argument("--player", default=E.GATE_PLAYER, help="offline pseudo (default packenv GATE_PLAYER)")
    pl.set_defaults(fn=launch)

    pf = sub.add_parser("flags", help="set matoulib.* flags in the instance properties")
    pf.add_argument("instance")
    pf.add_argument("--set", nargs="+", required=True, metavar="key=val")
    pf.set_defaults(fn=flags)

    ps = sub.add_parser("stop", help="graceful /exit, fallback kill")
    ps.add_argument("--timeout", type=int, default=8)
    ps.set_defaults(fn=stop)

    pst = sub.add_parser("status", help="is a game alive + does the RPC answer")
    pst.set_defaults(fn=status)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
