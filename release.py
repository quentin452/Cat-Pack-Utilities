#!/usr/bin/env python3
"""
release.py — Biggess Pack Cat Edition release pipeline.

Phases (see docs/10-automatisation-pack.md):
  smoke    boot a throwaway server (+ client auto-join, WIP) and report PASS/FAIL
  package  bump versions + build client/server zips        (WIP)
  upload   GitHub release (+ CurseForge)                    (WIP)
  all      smoke -> package -> upload (gated on PASS)        (WIP)

Design rules baked in:
  - Never touch the real server/game instance. The smoke test runs on a full copy
    (cp -a) that we mutate/boot freely and delete after. (Hardlink clones are unsafe
    here: a running server writes logs/configs/world, which would corrupt the source
    through shared inodes.)
  - Every poll loop checks the child process is still alive (no zombie waits).
  - Personal mod jars are swapped in by REPLACING the manually-placed jar; the
    mod-director bundle references an older, already-disabled OaT, so there is no
    re-download to fight.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()

# --- Paths -------------------------------------------------------------------
SERVER_SRC = HOME / "Bureau/SERVERS/Biggess Pack Cat Edition V1 Server"
SMOKE_CLONE = HOME / "Bureau/SERVERS/_smoke_clone"
CLIENT_INSTANCE = HOME / "Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST"
CLIENT_ARGFILE = HOME / "Documents/GitHub/Mod-Sandbox/docs/captures/client-relaunch.arg"
OAT_REPO = HOME / "Documents/GitHub/OptimizationsAndTweaks"

JAVA = Path("/usr/lib/jvm/default-runtime/bin/java")  # java 21, what the pack runs on
FORGE_PATCHES_JAR = "lwjgl3ify-2.1.15-forgePatches.jar"
SERVER_JVM_FLAGS = [
    "-Xmx7G", "-Xms500M", "-XX:+UseG1GC", "-XX:MaxGCPauseMillis=100",
    "-XX:+ParallelRefProcEnabled", "-XX:+UseCompressedOops", "-XX:+TieredCompilation",
    "-XX:+OptimizeStringConcat", "-XX:SoftRefLRUPolicyMSPerMB=100",
]

# OaT jar swap: remove this manually-placed jar, drop our fresh build in its place.
# Remove ANY existing OaT jar (the server src may carry any version — update_local swaps it too),
# not one hardcoded version, else two OaT jars coexist -> DuplicateModsFoundException.
OAT_REMOVE_GLOB = "optimizationsandtweaks-*.jar"
OAT_DEST_NAME = "optimizationsandtweaks-smoke.jar"

SERVER_READY_MARK = "Done ("           # vanilla/forge "Done (X.XXXs)! For help..."
ASYNC_PF_MARK = "[AsyncPathfinding]"    # our init/stats log lines
# Client auto-join (V1): the client GUI boots ~900 mods then connects to the kept-alive smoke
# server. Join is confirmed via the SERVER log (vanilla FML "joined the game") — reliable and
# already captured, so no fragile client-log marker. Needs a DISPLAY (desktop session or Xvfb).
CLIENT_GAMEDIR = HOME / "Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST"
JOIN_MARK = "joined the game"
CLIENT_TIMEOUT = 480


def log(msg):
    print(f"[release] {msg}", flush=True)


def die(msg, code=1):
    log(f"FATAL: {msg}")
    sys.exit(code)


# --- OaT jar resolution ------------------------------------------------------
def newest_oat_jar():
    libs = OAT_REPO / "build/libs"
    cands = [
        p for p in libs.glob("optimizationsandtweaks-*.jar")
        if "-dev" not in p.name and "-sources" not in p.name
    ]
    if not cands:
        die(f"no OaT jar in {libs} — build it first (./gradlew build)")
    return max(cands, key=lambda p: p.stat().st_mtime)


# --- Server clone + prepare --------------------------------------------------
def clone_server(fresh=True):
    if not SERVER_SRC.is_dir():
        die(f"server source not found: {SERVER_SRC}")
    if SMOKE_CLONE.exists():
        if not fresh:
            log(f"reusing existing clone {SMOKE_CLONE}")
            return
        log(f"removing old clone {SMOKE_CLONE}")
        shutil.rmtree(SMOKE_CLONE)
    log(f"cloning server (full copy) -> {SMOKE_CLONE}")
    # Full recursive copy: a booting server writes logs/config/world, so the clone
    # must be independent inodes from the source. ~1.6G, seconds on nvme.
    subprocess.run(["cp", "-a", str(SERVER_SRC), str(SMOKE_CLONE)], check=True)


def swap_oat(jar):
    mods = SMOKE_CLONE / "mods"
    removed = 0
    for p in mods.glob(OAT_REMOVE_GLOB):
        p.unlink()
        removed += 1
    dest = mods / OAT_DEST_NAME
    if dest.exists():
        dest.unlink()
    shutil.copy2(jar, dest)
    log(f"OaT swapped: removed {removed} old jar(s), installed {jar.name} -> {OAT_DEST_NAME}")


def configure_server(online_mode=False, fresh_world=True):
    props = SMOKE_CLONE / "server.properties"
    text = props.read_text(encoding="utf-8", errors="replace") if props.exists() else ""
    text = _set_prop(text, "online-mode", "true" if online_mode else "false")
    text = _set_prop(text, "spawn-protection", "0")
    text = _set_prop(text, "level-name", "smoke_world" if fresh_world else "world")
    props.write_text(text, encoding="utf-8")
    (SMOKE_CLONE / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    if fresh_world:
        wdir = SMOKE_CLONE / "smoke_world"
        if wdir.exists():
            shutil.rmtree(wdir)
    log(f"server configured (online-mode={online_mode}, fresh_world={fresh_world})")


def _set_prop(text, key, value):
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pat.search(text):
        return pat.sub(f"{key}={value}", text)
    return text + ("" if text.endswith("\n") or not text else "\n") + f"{key}={value}\n"


# --- Server boot + watch -----------------------------------------------------
def _build_client_argfile(host, port):
    """Copy the captured stage-2 client argfile: repoint --gameDir at the TEST instance and append
    auto-connect args. Returns the temp argfile path."""
    if not CLIENT_ARGFILE.exists():
        die(f"client argfile not captured: {CLIENT_ARGFILE} (recapture it — see docs/captures)")
    lines = CLIENT_ARGFILE.read_text(encoding="utf-8", errors="replace").splitlines()
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        if lines[i].strip().strip('"') == "--gameDir" and i + 1 < len(lines):
            out.append(f'"{CLIENT_GAMEDIR}"')   # replace the (now-deleted) old gameDir line
            i += 2
            continue
        i += 1
    out += ['"--server"', f'"{host}"', '"--port"', f'"{port}"']
    tmp = SMOKE_CLONE / "smoke-client.arg"
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    return tmp


def _stop_client(proc):
    if proc.poll() is not None:
        return
    log("stopping client...")
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def boot_client(server_log, host="localhost", port=25565, timeout=CLIENT_TIMEOUT):
    """Boot the GUI client, auto-connect to the kept-alive server, confirm join via the SERVER log."""
    res = {"joined": False, "client_exit": None, "reason": None}
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        log("WARNING: no DISPLAY/WAYLAND_DISPLAY set — the GUI client will fail. Run in a desktop "
            "session or under Xvfb.")
    argfile = _build_client_argfile(host, port)
    client_log = CLIENT_GAMEDIR / "logs" / "smoke-client-console.log"
    client_log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(JAVA), f"@{argfile}"]
    log(f"booting client (auto-join {host}:{port}) — gameDir {CLIENT_GAMEDIR.name}")
    console = open(client_log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=str(CLIENT_GAMEDIR), stdin=subprocess.DEVNULL,
                            stdout=console, stderr=subprocess.STDOUT)
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            res["client_exit"] = proc.returncode
            res["reason"] = f"client exited early (code {proc.returncode}) before join — see {client_log}"
            log(res["reason"])
            break
        content = server_log.read_text(encoding="utf-8", errors="replace") if server_log.exists() else ""
        if JOIN_MARK in content:
            res["joined"] = True
            res["reason"] = f"player joined (server saw '{JOIN_MARK}') in {round(time.time()-start,1)}s"
            log("CLIENT " + res["reason"])
            time.sleep(8)   # let a few in-world ticks run before teardown
            break
        time.sleep(5)
    else:
        res["reason"] = f"no join within {timeout}s (see {client_log})"
        log(res["reason"])
    console.close()
    _stop_client(proc)
    if res["client_exit"] is None:
        res["client_exit"] = proc.returncode
    return res


def boot_server(timeout=600, soak=180, extra_flags=None, run_client=False):
    """Boot the clone headless, wait for readiness, soak, then stop. Returns a result dict."""
    # Capture the server console to our own file and watch THAT (the log4j console
    # appender prints everything: mod loading, "Done (", our AsyncPathfinding lines).
    # This also avoids a PIPE deadlock: an undrained stdout=PIPE fills at 64KB and the
    # server blocks. Writing straight to a file has no such limit.
    server_log = SMOKE_CLONE / "logs" / "smoke-console.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)
    crash_dir = SMOKE_CLONE / "crash-reports"
    crashes_before = set(p.name for p in crash_dir.glob("*.txt")) if crash_dir.exists() else set()

    cmd = [str(JAVA), *SERVER_JVM_FLAGS, *(extra_flags or []), "@java9args.txt", "-jar", FORGE_PATCHES_JAR, "nogui"]
    log(f"booting server: {' '.join(cmd)}")
    console = open(server_log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, cwd=str(SMOKE_CLONE),
        stdin=subprocess.PIPE, stdout=console, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    result = {
        "ready": False, "ready_secs": None, "async_pf_init": False,
        "async_pf_stats": None, "unsatisfied_link": False, "new_crashes": [],
        "fatal_lines": [], "timed_out": False, "exit_code": None, "client": None,
    }

    start = time.time()
    ready = False
    # Phase 1: wait for readiness (poll the fml log; watch the process is alive).
    while True:
        if proc.poll() is not None:
            result["exit_code"] = proc.returncode
            log(f"server process exited early (code {proc.returncode}) before ready")
            break
        elapsed = time.time() - start
        if server_log.exists():
            content = server_log.read_text(encoding="utf-8", errors="replace")
            if "UnsatisfiedLinkError" in content:
                result["unsatisfied_link"] = True
            if ASYNC_PF_MARK in content and not result["async_pf_init"]:
                result["async_pf_init"] = True
                log("AsyncPathfinding log line seen (native executor reached)")
            if SERVER_READY_MARK in content:
                ready = True
                result["ready"] = True
                result["ready_secs"] = round(elapsed, 1)
                log(f"server READY in {result['ready_secs']}s")
                break
        if elapsed > timeout:
            result["timed_out"] = True
            log(f"TIMEOUT after {timeout}s waiting for readiness")
            break
        time.sleep(5)

    # Phase 1.5: client auto-join (only if ready and requested) — server stays up during this.
    if ready and run_client:
        result["client"] = boot_client(server_log)

    # Phase 2: soak (only if ready) — let mob AI run, watch for exceptions/stats.
    if ready and soak > 0:
        log(f"soaking {soak}s...")
        soak_start = time.time()
        while time.time() - soak_start < soak:
            if proc.poll() is not None:
                result["exit_code"] = proc.returncode
                log(f"server died during soak (code {proc.returncode})")
                break
            time.sleep(10)

    # Phase 3: stop gracefully.
    _stop_server(proc)
    if result["exit_code"] is None:
        result["exit_code"] = proc.returncode
    console.close()

    # Scan results.
    if server_log.exists():
        content = server_log.read_text(encoding="utf-8", errors="replace")
        result["fatal_lines"] = [
            ln for ln in content.splitlines()
            if "/FATAL]" in ln or "UnsatisfiedLinkError" in ln
        ][:20]
        stats = [ln for ln in content.splitlines() if "Async Pathfinding Stats" in ln]
        if stats:
            result["async_pf_stats"] = stats[-1].strip()
    if crash_dir.exists():
        result["new_crashes"] = sorted(
            p.name for p in crash_dir.glob("*.txt") if p.name not in crashes_before
        )
    return result


def _stop_server(proc):
    if proc.poll() is not None:
        return
    log("stopping server (console 'stop')...")
    try:
        proc.stdin.write("stop\n")
        proc.stdin.flush()
    except Exception:
        pass
    for _ in range(24):  # up to 120s graceful
        if proc.poll() is not None:
            log("server stopped cleanly")
            return
        time.sleep(5)
    log("server did not stop — terminating")
    proc.terminate()
    for _ in range(6):
        if proc.poll() is not None:
            return
        time.sleep(5)
    proc.kill()
    log("server killed (SIGKILL)")


def report_smoke(result):
    log("=" * 60)
    log("SMOKE RESULT (server phase)")
    for k in ("ready", "ready_secs", "async_pf_init", "async_pf_stats",
              "unsatisfied_link", "timed_out", "exit_code"):
        log(f"  {k}: {result[k]}")
    if result["new_crashes"]:
        log(f"  NEW crash-reports: {result['new_crashes']}")
    if result["fatal_lines"]:
        log(f"  FATAL/link lines ({len(result['fatal_lines'])}):")
        for ln in result["fatal_lines"]:
            log(f"    {ln}")
    pass_ = (
        result["ready"] and not result["unsatisfied_link"]
        and not result["new_crashes"] and not result["timed_out"]
    )
    client = result.get("client")
    if client is not None:
        log(f"  client: joined={client['joined']} exit={client['client_exit']} — {client['reason']}")
        pass_ = pass_ and client["joined"]
    log("=" * 60)
    phase = "SMOKE (server+client)" if client is not None else "SERVER PHASE"
    log(f"{phase}: {'PASS' if pass_ else 'FAIL'}")
    return pass_


# --- Commands ----------------------------------------------------------------
def cmd_smoke(args):
    jar = Path(args.oat_jar) if args.oat_jar else newest_oat_jar()
    log(f"OaT jar under test: {jar.name}")
    clone_server(fresh=not args.no_clone)
    swap_oat(jar)
    configure_server(online_mode=False, fresh_world=not args.keep_world)
    result = boot_server(timeout=args.timeout, soak=args.soak,
                         extra_flags=["-Dmoddirector.devMode=true"] if args.devmode else None,
                         run_client=args.client)
    ok = report_smoke(result)
    if not args.keep:
        log(f"cleaning up clone {SMOKE_CLONE}")
        shutil.rmtree(SMOKE_CLONE, ignore_errors=True)
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="Biggess pack release pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("smoke", help="server (+client, WIP) smoke test on a throwaway clone")
    sp.add_argument("--oat-jar", help="path to the OaT jar to test (default: newest build/libs)")
    sp.add_argument("--timeout", type=int, default=600, help="server readiness timeout (s)")
    sp.add_argument("--soak", type=int, default=180, help="soak time after ready (s)")
    sp.add_argument("--no-clone", action="store_true", help="reuse existing clone (skip cp -al)")
    sp.add_argument("--keep-world", action="store_true", help="keep existing world (no fresh gen)")
    sp.add_argument("--keep", action="store_true", help="do not delete the clone afterwards")
    sp.add_argument("--devmode", action="store_true",
                    help="boot with -Dmoddirector.devMode=true (FileDirector smoke: exercises the "
                         "keep-existing-variant path + the dir-listing cache)")
    sp.add_argument("--client", action="store_true",
                    help="after the server is ready, boot the GUI client and auto-join it (needs a "
                         "DISPLAY); PASS also requires the client to join")
    sp.set_defaults(func=cmd_smoke)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
