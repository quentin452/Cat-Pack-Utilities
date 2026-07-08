#!/usr/bin/env python3
"""release_pack.py — gated one-shot release pipeline for the modpack (Biggess Pack Cat Edition).

Chains the existing tools with HARD GATES so a pack update cannot ship unless every mod it
references is actually deliverable:

  GATE 1  pack repo working tree must be clean (the build bumps version files)
  GATE 2  pack_sync.py dry-run        — release-manifest pack blocks consistent, FileDirector
                                        client==server fork, Approved-only fileIDs (BUG-023 guard)
  GATE 3  pack_sync.py --audit        — personal url.bundle forks not stale vs GitHub releases
                                        (skippable --skip-audit: needs GitHub API)
  GATE 4  bundle_check.py             — EVERY bundle mod fetchable (CF: Approved + CDN 200;
                                        URLs: 200) AND client manifest files[] all Approved.
                                        This is the "valid url / approved mods" ship gate.
  GATE 4b boot-verify [--boot-verify] — OPT-IN (OFF by default, full pack boots ~8 min): boot the
                                        pack TEST instance + boot_crash_scan the fresh log; refuse
                                        to zip/upload on a fatal (compile-green != apply-green).
  GATE 4c smoke       [--smoke]       — OPT-IN (OFF, boots a server ~minutes): build/verify the FRESH
                                        shipped local-build mods, sync them in (update_local), then
                                        release.py smoke (throwaway server clone + optional client
                                        auto-join); refuse to ship unless it reports PASS.
  GATE 7  bundle drift                — bundle_drift.py: does the TEST instance's mods/ match what the
                                        bundles DECLARE, both ways (undeclared additions + removals)?
                                        Report-only (--strict-drift to hard-fail, --skip-drift to skip).
  step 5  changelog derive            — changelog_from_bundles.py <baseline>..HEAD --markdown
                                        (or --changelog-file for hand-curated wording)
  step 6  build zips [--execute]      — generate_modpack_zips.py <version> (bumps manifest+modpack.json)
  step 7  upload CF [--execute]       — cf_upload.py: client zip, then serverpack with
                                        --parent-file-id <client file id> (additional file)
  step 8  publish changelog [--execute] — changelog_publish.py --from-derive --prepend

DRY-RUN (default) is READ-ONLY: runs gates 1-4 + shows the changelog + prints the plan.
--execute does the mutations/uploads. Nothing is ever git-pushed — the user pushes.

Usage:
  python3 release_pack.py --version 1.1.9 --baseline 9dabb717            # dry-run (gates + plan)
  python3 release_pack.py --version 1.1.9 --baseline 9dabb717 --execute  # ship for real
  # --changelog-file curated.md   use hand-curated derive output for CF + published changelog
  # --skip-audit                  skip gate 3 (offline / GitHub rate-limited)

Baseline = the commit of the LAST PUBLISHED pack version (what players have), not the last commit.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CF_READ_API = "https://api.curseforge.com"
PACK_REPO = Path.home() / "Documents/GitHub/privates-minecraft-modpack"
PACK_DIR = PACK_REPO / "MODPACKS/Biggess Pack Cat Edition"
DIST = PACK_DIR / "dist"
PACK_PROJECT_ID = 830694          # CF modpack project (biggess-pack-cat-edition)
GAME_VERSION = "1.7.10"
# --boot-verify gate: boot the pack TEST instance + scan the boot log before shipping.
PACK_TEST_INSTANCE = Path.home() / "Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST"
PACK_TEST_ARGFILE = "pack-worldgen.arg"   # auto-into-a-world argfile in the TEST instance
PACK_BOOT_LOG = "boot-relverify.log"      # dedicated fresh log (the instance keeps many boot-*.log)
JAVA = "/usr/lib/jvm/default-runtime/bin/java"
CRASH_SCAN = HERE / "boot_crash_scan.py"
PACK_BOOT_TIMEOUT = 660                    # the full pack boots in ~8 min
# --smoke gate (GATE 4c): boot a throwaway server clone with the FRESH shipped binaries.
RELEASE_MANIFEST = Path.home() / "Documents/GitHub/Mod-Sandbox/memory/release-manifest.json"
RELEASE_PY = HERE / "release.py"
UPDATE_LOCAL = HERE / "update_local.py"
# dirs never scanned for a mod's "newest source" mtime (build outputs / VCS / IDE / caches).
_SRC_SKIP_DIRS = {"build", ".git", ".gradle", ".idea", "run", "out", "bin", ".settings", "libs"}
_SRC_SKIP_EXT = (".jar", ".zip", ".class", ".log")


def run(cmd, gate, cwd=None, capture=False):
    print(f"\n=== {gate}: {' '.join(str(c) for c in cmd)}")
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, text=True,
                          capture_output=capture)
    if capture and proc.stdout:
        print(proc.stdout[-4000:])
    if proc.returncode != 0:
        sys.exit(f"⛔ {gate} FAILED (exit {proc.returncode}) — NOT releasing.")
    return proc


def cf_read_key():
    """CF_API_KEY read key from env or .env.local/.env (never printed)."""
    if os.environ.get("CF_API_KEY"):
        return os.environ["CF_API_KEY"]
    for name in (".env.local", ".env"):
        p = HERE / name
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("CF_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _bump_patch(ver):
    """Increment the trailing dotted component (1.1.10 -> 1.1.11)."""
    nums = [int(x) for x in ver.split(".")]
    nums[-1] += 1
    return ".".join(str(x) for x in nums)


def latest_pack_cut(pack_repo):
    """CONCERN A: (version, commit) of the latest 'release: cut V<ver>' commit on the pack repo, or
    (None, None). That commit = the last PUBLISHED pack state -> its version is what shipped (auto
    --version base) and its commit is the correct changelog --baseline for the next release."""
    out = subprocess.run(["git", "-C", str(pack_repo), "log", "--grep=release: cut V",
                          "-1", "--format=%H%x09%s"], capture_output=True, text=True).stdout.strip()
    if not out:
        return None, None
    commit, _, subj = out.partition("\t")
    m = re.search(r"release: cut V([0-9]+(?:\.[0-9]+)*)", subj)
    return (m.group(1) if m else None), commit


def latest_cf_pack_version(project_id):
    """Highest V<x.y.z> already published on the CF pack project — fallback for auto --version when
    there is no local 'release: cut' commit. None if unverifiable (no read key / API error)."""
    key = cf_read_key()
    if not key:
        return None
    url = f"{CF_READ_API}/v1/mods/{project_id}/files?pageSize=50"
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-api-key", key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            files = json.load(r).get("data", [])
    except Exception:
        return None
    best = None
    for f in files:
        for field in ("displayName", "fileName"):
            m = re.search(r"V([0-9]+(?:\.[0-9]+)+)", str(f.get(field, "")))
            if m:
                t = tuple(int(x) for x in m.group(1).split("."))
                if best is None or t > best[0]:
                    best = (t, m.group(1))
    return best[1] if best else None


def pack_already_published(project_id, version):
    """Return the CF file that already carries V<version> for this pack (dict), False if none, or
    None if UNVERIFIABLE (no read key / API error). Guards against re-shipping a live pack version."""
    key = cf_read_key()
    if not key:
        return None
    url = f"{CF_READ_API}/v1/mods/{project_id}/files?pageSize=50"
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-api-key", key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            files = json.load(r).get("data", [])
    except Exception as e:
        print(f"⚠️ could not query CF pack files (no dup check): {e}")
        return None
    # Boundary-aware so 'V1.1.1' does NOT match 'V1.1.10' (versions are sequential, this matters).
    pat = re.compile(re.escape(f"V{version}") + r"(?![0-9.])")
    for f in files:
        if pat.search(str(f.get("displayName", ""))) or pat.search(str(f.get("fileName", ""))):
            return f
    return False


def _running_mc_pids():
    """PIDs of live Minecraft JVMs (real `bin/java` procs matched by launch markers)."""
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        if "bin/java" not in line:
            continue
        low = line.lower()
        if "curseforge/minecraft" in low or "launchwrapper" in low or ".arg" in low:
            try:
                pids.append(int(line.split()[0]))
            except (ValueError, IndexError):
                pass
    return pids


def _kill_all_minecraft():
    """Single-instance discipline: kill every MC JVM then CONFIRM 0 (re-scan; the JVM re-execs)."""
    if not _running_mc_pids():
        print("  boot-verify: 0 MC process(es) running — clear to boot")
        return True
    print("  boot-verify: killing running MC %s (single-instance)" % _running_mc_pids())
    import signal
    for _ in range(12):
        for pid in _running_mc_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(1)
        if not _running_mc_pids():
            print("  boot-verify: confirmed 0 MC process(es)")
            return True
    print("  boot-verify: WARN could not confirm 0 MC — aborting gate")
    return False


def pack_boot_verify():
    """GATE 4b (opt-in --boot-verify): boot the pack TEST instance and scan the boot log so a
    compile-green-but-apply-broken mixin cannot ship. Refuses (sys.exit) on a fatal. OFF by default
    (the full pack boots ~8 min). BOOTS the game -> single-instance -> run live on the MAIN thread;
    in dry-run this still boots (it is a preflight), so the main thread drives it either way."""
    argfile = PACK_TEST_INSTANCE / PACK_TEST_ARGFILE
    boot_log = PACK_TEST_INSTANCE / PACK_BOOT_LOG
    print("\n=== GATE 4b (--boot-verify): boot pack TEST instance + scan")
    if not argfile.is_file():
        sys.exit(f"⛔ GATE 4b: launch argfile not found: {argfile} — cannot boot-verify the pack. "
                 f"Fix PACK_TEST_ARGFILE or drop --boot-verify.")
    if not _kill_all_minecraft():
        sys.exit("⛔ GATE 4b: could not clear running MC (single-instance) — aborting.")
    print(f"  booting pack TEST via {argfile.name} (~8 min); log -> {boot_log.name}")
    with open(boot_log, "w") as lf:
        proc = subprocess.Popen([JAVA, "@" + str(argfile)], cwd=str(PACK_TEST_INSTANCE),
                                stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    # Wait for a world / a fatal / process death / timeout — cheap poll of the fresh log.
    reached = None
    for _ in range(PACK_BOOT_TIMEOUT // 6):
        if proc.poll() is not None:
            reached = "process-exited"
            break
        try:
            txt = boot_log.read_text(errors="ignore")
        except OSError:
            txt = ""
        if re.search(r"reached the world|Sound engine started|Narrator library", txt):
            reached = "world"
            break
        if re.search(r"A fatal error has occurred|Game crashed|has crashed", txt):
            reached = "fatal"
            break
        time.sleep(6)
    print(f"  boot poll -> {reached or 'timeout'}")
    # Scan ONLY the fresh log (the instance holds many large boot-*.log we must not sweep).
    proc_scan = subprocess.run([sys.executable, str(CRASH_SCAN), str(boot_log)],
                               text=True, capture_output=True)
    if proc_scan.stdout:
        print(proc_scan.stdout)
    _kill_all_minecraft()  # never leave the pack instance running
    if proc_scan.returncode != 0:
        sys.exit("⛔ GATE 4b: FATAL detected booting the pack — NOT releasing (fix + re-verify).")
    if reached != "world":
        sys.exit(f"⛔ GATE 4b: pack boot did not reach a world (status={reached or 'timeout'}) and "
                 f"no fatal captured — UNVERIFIED; refusing to ship. Inspect {boot_log}.")
    print("  GATE 4b: pack booted to a world, no fatal ✓")


def config_gate(strict):
    """GATE 5 (CONCERN C): run config_sync.py in PREVIEW (no --apply) and REPORT if the TEST
    instance's game config differs from canonical in real content (EOL-agnostic; mod-director is
    already excluded by config_sync). The canonical is what ships, so a diff means the instance was
    tuned but not synced back (or vice-versa) — a visible gate. Not a hard fail by default (config
    drift is often benign game-repopulation); --strict-config turns it into a hard fail."""
    print("\n=== GATE 5 (config drift): config_sync.py --instance TEST (preview)")
    proc = subprocess.run([sys.executable, str(HERE / "config_sync.py"), "--instance", "TEST"],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    total = sum(int(n) for n in re.findall(r"-> (\d+) differing", out))
    drift = [ln for ln in out.splitlines() if re.search(r"^\s*(DIFFERS|NEW)\b", ln)]
    if proc.returncode != 0 and total == 0:      # config_sync itself errored (e.g. canonical missing)
        print(out[-1500:])
        sys.exit("⛔ GATE 5: config_sync failed to run — cannot verify config drift.")
    if total == 0:
        print("  config in sync: TEST instance matches canonical (EOL-agnostic) ✓")
        return
    print(f"  ⚠️ {total} config file(s) DIFFER between the TEST instance and canonical "
          f"(canonical is what ships):")
    for ln in drift[:20]:
        print("   " + ln.strip())
    if len(drift) > 20:
        print(f"   … +{len(drift) - 20} more")
    if strict:
        sys.exit("⛔ GATE 5 (--strict-config): config drift — reconcile canonical<->instance "
                 "(config_sync.py [--from-instance TEST] --apply) then re-run.")
    print("  (WARNING only — drift is often benign game-repopulation; confirm the canonical is what "
          "you intend to ship. Pass --strict-config to hard-fail here.)")


def thirdparty_gate():
    """GATE 6 (CONCERN C): run mod_update_checker.py in PREVIEW (no --apply) against the CANONICAL
    bundles (what ships) and REPORT third-party mods with an Approved update available — closes the
    hole where the pipeline shipped stale third-party mods silently. Report-only (the human bumps
    via mod_update_checker.py --apply then re-runs); --skip-modcheck bypasses it."""
    inst = PACK_DIR / "src/common"     # canonical config/mod-director + no mods/ -> skip zip dates
    print("\n=== GATE 6 (third-party staleness): mod_update_checker.py (preview, canonical bundles)")
    # curse.bundle only: url.bundle (GitHub) staleness is GATE 3's domain (fork audit) and floods with
    # the pack's INTENTIONAL GTNH version pins — the actionable third-party signal is the Approved-only
    # curse.bundle bump (the checker's --apply enforces fileStatus==4).
    proc = subprocess.run([sys.executable, str(HERE / "mod_update_checker.py"),
                           "--instance", str(inst), "--skip-zipdates", "--skip-github"],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    approved = re.findall(r"addonId \d+: \d+ -> \d+.*", out)   # Approved-only curse.bundle bumps
    if not approved:
        if "CF_API_KEY absent" in out:
            print("  CF_API_KEY absent — curse.bundle staleness UNVERIFIED (add a read key to enforce).")
        else:
            print("  no Approved third-party curse.bundle updates pending ✓")
        return
    print(f"  ⚠️ {len(approved)} third-party curse.bundle mod(s) have an Approved update available:")
    for ln in approved:
        print("   " + ln.strip())
    print("  (REPORT only — bump third-party mods via mod_update_checker.py --apply "
          "(Approved-only), re-run. Pass --skip-modcheck to bypass this gate.)")


def _shipped_local_build_mods():
    """Manifest mods that ship in the pack AND build from HEAD locally (jar_glob) — the binaries the
    smoke test must boot FRESH. Skips pack-delivery-only entries (no jar_glob), mods not in the pack
    (pack falsy), and PINNED deliveries (FileDirector: a fixed CF-Approved file, NOT a HEAD build —
    rebuilding/swapping it would smoke-test something that never ships)."""
    mods = json.load(open(RELEASE_MANIFEST))["mods"]
    out = []
    for m in mods:
        if not m.get("jar_glob") or not m.get("pack"):
            continue
        pk = m["pack"]
        if isinstance(pk, dict) and ("_pin" in pk or str(pk.get("delivery", "")).startswith("client_manifest")):
            continue
        out.append(m)
    return out


def _newest_built_jar(m):
    repo = os.path.expanduser(m["repo"])
    cands = [p for p in glob.glob(os.path.join(repo, m["jar_glob"]))
             if not any(x in os.path.basename(p) for x in m.get("jar_exclude", []))]
    return max(cands, key=os.path.getmtime) if cands else None


def _newest_source_mtime(repo):
    """(mtime, path) of the newest SOURCE file in the repo (build outputs / VCS / caches excluded).
    Heuristic freshness signal: if the built jar predates this, the jar is a stale build."""
    repo = os.path.expanduser(repo)
    newest, newest_f = 0.0, None
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in _SRC_SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if fn.endswith(_SRC_SKIP_EXT):
                continue
            p = os.path.join(dp, fn)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt > newest:
                newest, newest_f = mt, p
    return newest, newest_f


def _smoke_freshness_guard(mods, build):
    """Guarantee the smoke test boots FRESH binaries: for each shipped local-build mod, either
    ./gradlew build it (--smoke-build), or FAIL loudly if its newest built jar is older than its
    newest source file (a stale build) or missing. Never silently smoke-test a stale jar."""
    for m in mods:
        repo = os.path.expanduser(m["repo"])
        if build:
            print(f"  building {m['name']} (./gradlew build in {repo})...")
            r = subprocess.run(["./gradlew", "build"], cwd=repo)
            if r.returncode != 0:
                sys.exit(f"⛔ GATE 4c (smoke): {m['name']} ./gradlew build FAILED — fix + re-run.")
        jar = _newest_built_jar(m)
        if not jar:
            sys.exit(f"⛔ GATE 4c (smoke): no built jar for {m['name']} ({m['jar_glob']}) — build it "
                     f"first (./gradlew build in {repo}) or pass --smoke-build. Refusing to smoke-test "
                     f"a missing binary.")
        src_mt, src_f = _newest_source_mtime(repo)
        if src_mt and os.path.getmtime(jar) < src_mt:
            sys.exit(f"⛔ GATE 4c (smoke): {m['name']} built jar is STALE — {os.path.basename(jar)} is "
                     f"OLDER than source {os.path.relpath(src_f, repo)}. Rebuild it (./gradlew build) "
                     f"or pass --smoke-build; refusing to smoke-test a stale binary.")
        print(f"  fresh ✓ {m['name']}: {os.path.basename(jar)} (newer than newest source)")


def smoke_gate(args):
    """GATE 4c (opt-in --smoke, hole #3): boot a throwaway SERVER clone (release.py smoke) with the
    FRESH shipped binaries and refuse the release unless it reports PASS. release.py smoke already
    clones the real server (never mutating it) + optionally auto-joins a GUI client; the gated pipeline
    never called it, so a server-crashing pack could ship. OFF by default (boots ~minutes; --smoke-client
    needs a DISPLAY). Freshness sub-step first: build/verify each shipped local-build mod then sync the
    SAME jars into the instances via update_local, so smoke boots exactly what will ship (not a stale jar)."""
    print("\n=== GATE 4c (--smoke): server smoke-test (release.py smoke) before ship")
    if not args.smoke:
        print("  SKIPPED (--smoke to enable — boots a throwaway server ~minutes; --smoke-client also "
              "boots a GUI client and needs a DISPLAY)")
        return
    if not RELEASE_MANIFEST.is_file():
        sys.exit(f"⛔ GATE 4c: release manifest not found: {RELEASE_MANIFEST}")
    mods = _shipped_local_build_mods()
    print(f"  freshness guard — {len(mods)} shipped local-build mod(s): "
          f"{', '.join(m['name'] for m in mods) or '(none)'}")
    print("  (pinned/pack-delivery-only mods e.g. FileDirector are excluded — they don't build from HEAD)")
    _smoke_freshness_guard(mods, args.smoke_build)
    # Sync those fresh jars into the instances the smoke test boots: update_local copies into the REAL
    # server mods/ (release.py smoke then cp -a clones it) AND the TEST client (used by --smoke-client).
    for m in mods:
        print(f"  sync fresh {m['name']} -> instances (update_local.py --only {m['name']} --apply)")
        r = subprocess.run([sys.executable, str(UPDATE_LOCAL), "--only", m["name"], "--apply"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"⛔ GATE 4c (smoke): update_local failed to sync {m['name']} — {r.stderr.strip()}")
    # Boot the throwaway clone (+ optional client) with the same interpreter; require exit 0 = PASS.
    cmd = [sys.executable, str(RELEASE_PY), "smoke",
           "--soak", str(args.smoke_soak), "--timeout", str(args.smoke_timeout)]
    if args.smoke_client:
        cmd.append("--client")
    print(f"  booting smoke: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tail = "\n".join((proc.stdout or "").splitlines()[-25:])
    print(tail)
    if proc.returncode != 0:
        sys.exit(f"⛔ GATE 4c (smoke): server smoke-test FAILED (release.py smoke exit {proc.returncode}) "
                 f"— NOT releasing. Fix the crash + re-run. Tail above.")
    print("  GATE 4c: smoke PASS — the fresh shipped binaries boot a server"
          + (" + client join" if args.smoke_client else "") + " clean ✓")


def drift_gate(strict):
    """GATE 7 (hole #7): run bundle_drift.py — does the TEST instance's mods/ match what the
    canonical bundles DECLARE, BOTH ways (undeclared additions + removals)? A stale/hand-edited test
    instance can smoke/boot-test a binary set that never ships. bundle_drift exits 1 on HARD drift
    (an undeclared jar OR a declared jar missing); 0 if clean or only a version swap. Report-only by
    default (drift is often intentional test state — swapped local builds); --strict-drift hard-fails."""
    print("\n=== GATE 7 (bundle drift): bundle_drift.py --instance TEST (instance mods/ vs bundles)")
    proc = subprocess.run([sys.executable, str(HERE / "bundle_drift.py"), "--instance", "TEST"],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = out.splitlines()
    # echo the section headers + the verdict tail (the per-jar lists can be long)
    for ln in lines:
        if re.match(r"^\s*[+\-~.] ", ln) or ln.startswith(("UNDECLARED", "REMOVALS", "VERSION DRIFT",
                                                            "DECLARED-BUT", "DRIFT —", "IN SYNC")):
            print("   " + ln.rstrip())
    if proc.returncode == 0:
        print("  no undeclared additions or removals — TEST instance matches the declared set ✓")
        return
    # returncode 1 = hard drift (also covers a bundle_drift error; the tail above shows which)
    if strict:
        print(out[-1200:])
        sys.exit("⛔ GATE 7 (--strict-drift): the TEST instance does NOT match the declared bundles "
                 "(undeclared addition or removal) — reconcile mods/ (update_local.py / mod-director) "
                 "then re-run.")
    print("  (WARNING only — drift is often intentional test state, e.g. a locally-built jar swapped "
          "in. Confirm you are smoke-testing the mods players will actually get. Pass --strict-drift "
          "to hard-fail here.)")


def main():
    ap = argparse.ArgumentParser(description="Gated modpack release pipeline.")
    ap.add_argument("--version", help="pack version to ship, e.g. 1.1.9 (no V prefix). Default: AUTO "
                    "= last published cut version + patch bump (CONCERN A)")
    ap.add_argument("--baseline", help="git ref of the LAST PUBLISHED pack version (changelog derive "
                    "base). Default: AUTO = the commit of the last 'release: cut V…' (CONCERN A)")
    ap.add_argument("--changelog-file", type=Path,
                    help="hand-curated derive-markdown; default = raw derive output")
    ap.add_argument("--skip-audit", action="store_true", help="skip the fork-staleness audit gate")
    ap.add_argument("--strict-config", action="store_true",
                    help="GATE 5: hard-fail on config drift (default: report as a warning gate)")
    ap.add_argument("--skip-modcheck", action="store_true",
                    help="GATE 6: skip the third-party mod staleness gate (mod_update_checker)")
    ap.add_argument("--skip-drift", action="store_true",
                    help="GATE 7: skip the instance<->bundle drift check (bundle_drift)")
    ap.add_argument("--strict-drift", action="store_true",
                    help="GATE 7: hard-fail on instance/bundle drift (default: report as a warning)")
    ap.add_argument("--boot-verify", action="store_true",
                    help="GATE 4b: boot the pack TEST instance + scan the boot log before shipping "
                         "(refuse on fatal). OFF by default — full pack boots ~8 min; boots the "
                         "game so run single-instance on the main thread")
    ap.add_argument("--smoke", action="store_true",
                    help="GATE 4c: boot a throwaway SERVER clone (release.py smoke) with the FRESH "
                         "shipped binaries and require PASS before ship. OFF by default (boots "
                         "~minutes); boots the game so run single-instance on the main thread")
    ap.add_argument("--smoke-build", action="store_true",
                    help="GATE 4c: ./gradlew build each shipped local-build mod before smoke "
                         "(default: FAIL if a built jar is older than its source = stale build)")
    ap.add_argument("--smoke-client", action="store_true",
                    help="GATE 4c: also boot the GUI client + auto-join (needs a DISPLAY); PASS then "
                         "also requires the client to join")
    ap.add_argument("--smoke-soak", type=int, default=120,
                    help="GATE 4c: server soak seconds after ready (default 120)")
    ap.add_argument("--smoke-timeout", type=int, default=600,
                    help="GATE 4c: server readiness timeout seconds (default 600)")
    ap.add_argument("--project-id", type=int, default=PACK_PROJECT_ID)
    ap.add_argument("--execute", action="store_true", help="build zips + upload to CF + publish changelog")
    ap.add_argument("--force", action="store_true",
                    help="ship even if CF already has a file for V<version> (default: REFUSE to "
                         "avoid re-shipping a live pack version)")
    args = ap.parse_args()

    # CONCERN A — auto-version / auto-baseline when not given explicitly (no more hand-set stale
    # values). Explicit flags still override. Both default off the last 'release: cut V…' commit.
    cut_ver, cut_commit = latest_pack_cut(PACK_REPO)
    if not args.version:
        base_ver = cut_ver or latest_cf_pack_version(args.project_id)
        if not base_ver:
            sys.exit("could not auto-derive --version (no 'release: cut V…' commit and no CF read "
                     "key) — pass --version explicitly.")
        args.version = _bump_patch(base_ver)
        src = "last cut" if cut_ver else "CF published"
        print(f"=== auto-version: pack V{base_ver} -> V{args.version} ({src} + patch bump)")
    if not args.baseline:
        if not cut_commit:
            sys.exit("could not auto-derive --baseline (no 'release: cut V…' commit) — pass --baseline.")
        args.baseline = cut_commit
        print(f"=== auto-baseline: {cut_commit[:9]} (last 'release: cut' commit)")

    if not re.fullmatch(r"[0-9]+(\.[0-9]+)*", args.version):
        sys.exit(f"--version must be a plain dotted number (got {args.version!r})")

    # GATE 1 — clean tree (the zip build bumps version files; a dirty tree muddles the release commit)
    dirty = subprocess.run(["git", "-C", str(PACK_REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        sys.exit(f"⛔ GATE 1: pack repo working tree not clean:\n{dirty}\n— commit/stash first.")
    print("=== GATE 1: pack repo tree clean ✓")

    # GATE 2 — pack config consistency + Approved-only fileIDs
    run([sys.executable, HERE / "pack_sync.py"], "GATE 2 (pack_sync consistency)")

    # GATE 3 — personal fork bundles not stale vs their GitHub releases
    if args.skip_audit:
        print("\n=== GATE 3: SKIPPED (--skip-audit)")
    else:
        run([sys.executable, HERE / "pack_sync.py", "--audit"], "GATE 3 (fork staleness audit)")

    # GATE 4 — every shipped mod fetchable + manifest files[] Approved
    run([sys.executable, HERE / "bundle_check.py"], "GATE 4 (bundle fetchability + manifest Approved)")

    # GATE 4b — OPT-IN boot-verify: the pack must actually BOOT (mixins applied, no crash)
    if args.boot_verify:
        pack_boot_verify()
    else:
        print("\n=== GATE 4b (boot-verify): SKIPPED (pass --boot-verify to boot the pack + scan)")

    # GATE 4c — OPT-IN smoke: the SHIPPED binaries must boot a server (+client) clean (hole #3)
    smoke_gate(args)

    # GATE 5 — config drift (CONCERN C): canonical config is what ships; report/enforce drift
    config_gate(args.strict_config)

    # GATE 6 — third-party staleness (CONCERN C): Approved updates for bundled third-party mods
    if args.skip_modcheck:
        print("\n=== GATE 6 (third-party staleness): SKIPPED (--skip-modcheck)")
    else:
        thirdparty_gate()

    # GATE 7 — instance<->bundle drift (hole #7): does the TEST instance test what actually ships?
    if args.skip_drift:
        print("\n=== GATE 7 (bundle drift): SKIPPED (--skip-drift)")
    else:
        drift_gate(args.strict_drift)

    # step 5 — changelog
    if args.changelog_file:
        changelog = args.changelog_file.read_text(encoding="utf-8")
        print(f"\n=== changelog (curated: {args.changelog_file}):\n{changelog}")
    else:
        proc = run([sys.executable, HERE / "changelog_from_bundles.py",
                    f"{args.baseline}..HEAD", "--markdown"], "changelog derive", capture=True)
        changelog = proc.stdout
    if not changelog.strip():
        sys.exit("⛔ empty changelog — wrong baseline?")

    # Anti-duplicate: is V<version> already published on CF? (read-only probe; report in dry-run,
    # ENFORCE in execute unless --force). Prevents re-shipping a live pack version + a wasted build.
    published = pack_already_published(args.project_id, args.version)
    if published:
        msg = (f"CF already has a file for V{args.version} "
               f"(id {published.get('id')}, displayName {published.get('displayName')!r})")
        if args.execute and not args.force:
            sys.exit(f"⛔ {msg} — REFUSING to re-ship. Pass --force to override, or bump --version.")
        print(f"\n⚠️ {msg}" + (" — --force set, will re-ship" if args.execute else
                                " — would REFUSE to ship (pass --force to override)"))
    elif published is None:
        print("\n⚠️ could not verify whether V%s is already on CF (no read key) — not enforced." % args.version)

    if not args.execute:
        print("\n=== DRY-RUN COMPLETE — all gates PASS. Plan with --execute:")
        print(f"  1. generate_modpack_zips.py {args.version}  (bumps manifest+modpack.json)")
        print(f"  2. cf_upload client zip  -> project {args.project_id} (gameVersion {GAME_VERSION})")
        print("  3. cf_upload serverpack  -> same project, --parent-file-id <client id>")
        print(f"  4. changelog_publish.py --from-derive - --version V{args.version} --prepend")
        print("  5. YOU: review + commit the bump/changelog, push, watch CF review.")
        return

    # step 6 — build zips
    run([sys.executable, PACK_DIR / "generate_modpack_zips.py", args.version],
        "build zips", cwd=PACK_DIR)
    client_zip = DIST / f"BiggessPackCatEdition{args.version}.zip"
    server_zip = DIST / f"BiggessPackCatEditionServerPack{args.version}.zip"
    for z in (client_zip, server_zip):
        if not z.is_file():
            sys.exit(f"⛔ expected zip missing after build: {z}")

    # step 7 — upload client, then serverpack as its additional file
    chlog_tmp = DIST / f"changelog-{args.version}.md"
    chlog_tmp.write_text(changelog, encoding="utf-8")
    proc = run([sys.executable, HERE / "cf_upload.py", "--project-id", args.project_id,
                "--file", client_zip, "--display-name", f"Biggess Pack Cat Edition V{args.version}",
                "--game-version", GAME_VERSION, "--release-type", "release",
                "--changelog-file", chlog_tmp, "--changelog-type", "markdown"],
               "upload client zip", capture=True)
    m = re.search(r"file id: (\d+)", proc.stdout or "")
    if not m:
        sys.exit("⛔ could not parse the client file id from cf_upload output — serverpack NOT uploaded.")
    client_fid = m.group(1)
    run([sys.executable, HERE / "cf_upload.py", "--project-id", args.project_id,
         "--file", server_zip, "--display-name", f"Biggess Pack Cat Edition V{args.version} Server Pack",
         "--game-version", GAME_VERSION, "--release-type", "release",
         "--changelog-file", chlog_tmp, "--changelog-type", "markdown",
         "--parent-file-id", client_fid], "upload serverpack", capture=True)

    # step 8 — publish the changelog into the pack CHANGELOG file
    pub = subprocess.run([sys.executable, str(PACK_REPO / "changelog_publish.py"),
                          "--from-derive", "-", "--version", f"V{args.version}", "--prepend"],
                         input=changelog, text=True)
    if pub.returncode != 0:
        print("⚠️ changelog_publish failed — prepend it by hand.")

    # step 9 — commit the cut + push. The release is already PUBLIC on CF at this point:
    # origin must reflect what players have (and the next release's --baseline is this commit).
    subprocess.run(["git", "-C", str(PACK_REPO), "add",
                    str(PACK_DIR / "src/client/manifest.json"),
                    str(PACK_DIR / "src/common/config/mod-director/modpack.json"),
                    str(PACK_REPO / "CHANGELOGS/SUPPORTED/1.7.10 Bigges Pack Cat Edition.md")], check=True)
    msg = (f"release: cut V{args.version} (CF files {client_fid} client + serverpack attached)")
    subprocess.run(["git", "-C", str(PACK_REPO), "commit", "-m", msg], check=True)
    cut_ref = subprocess.run(["git", "-C", str(PACK_REPO), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(PACK_REPO), "branch", "--show-current"],
                            capture_output=True, text=True).stdout.strip()
    push = subprocess.run(["git", "-C", str(PACK_REPO), "push", "origin", branch])
    push_note = "pushed" if push.returncode == 0 else "⚠️ PUSH FAILED — push manually"

    print(f"""
=== RELEASE {args.version} UPLOADED ===
client file id: {client_fid} (serverpack attached as additional file)
cut commit: {cut_ref} ({push_note}) — use it as --baseline for the next release
Remaining (manual):
  - watch CF review (modpack = manual review)
  - housekeeping skill (archive bugs), update pipeline-mods.md""")


if __name__ == "__main__":
    main()
