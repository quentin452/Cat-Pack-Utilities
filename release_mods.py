#!/usr/bin/env python3
"""
release_mods.py — bulk release the pending mods from release_manifest.json.

Per mod, with SAFETY GATES first (nothing is released if a gate fails):
  1. clean working tree      (never release with uncommitted changes)
  2. on the expected branch
  3. PUSH pass               (push any unpushed commits — a release must reference code
                             that is on origin, not only local)
  4. tag the version         (git tag <version>, local)
  5. COMPILE/BUILD pass       (build green + jar produced; on failure the tag is rolled back)
Then release: push tag -> GitHub release (gh) -> CurseForge (cf_upload.py) -> Modrinth
(modrinth_upload.py), each only if configured. Changelog = git commits since the previous tag.

DRY-RUN BY DEFAULT. Pass --execute to actually tag/push/publish.

Usage:
  release_mods.py                     # dry-run all mods (gates + plan, no publish)
  release_mods.py --only OptimizationsAndTweaks
  release_mods.py --execute           # for real
  release_mods.py --execute --skip-build   # trust an existing build (not recommended)
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# The manifest (release plan) lives in the Mod-Sandbox hub (planning data, versioned +
# auto-pushed, next to pipeline-mods.md). Override with --manifest.
MANIFEST = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory/release-manifest.json")


def run(cmd, cwd=None, env=None, check=True, capture=True):
    r = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.STDOUT if capture else None)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {' '.join(cmd)}\n{r.stdout or ''}")
    return (r.stdout or "").strip(), r.returncode


def git(repo, *args, check=True):
    return run(["git", "-C", repo, *args], check=check)[0]


def build_run(cmd, cwd, env):
    """Run a build streaming to a temp FILE, never a PIPE. A gradle daemon inherits gradlew's
    stdout write-end; with a PIPE it keeps it open forever (no EOF) and subprocess.run's
    communicate() hangs even after gradlew exits. A file has no EOF wait — run() returns as soon
    as the direct child (gradlew) exits. Returns (returncode, tail_output)."""
    with tempfile.TemporaryFile("w+") as f:
        code = subprocess.run(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT).returncode
        f.seek(0)
        return code, f.read()


def origin_slug(repo):
    """owner/repo of the origin remote — so gh targets the FORK, not its upstream parent."""
    url = git(repo, "remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def dirty_paths(repo, ignore=()):
    """Porcelain paths that are dirty, excluding build-touched noise listed in ignore.
    Parses the path as the last whitespace field (robust to run()'s output .strip();
    assumes mod repos have no spaces in tracked paths)."""
    out = []
    for line in git(repo, "status", "--porcelain").splitlines():
        parts = line.split()
        if not parts:
            continue
        path = parts[-1].strip('"')
        if os.path.basename(path) not in ignore and path not in ignore:
            out.append(path)
    return out


def verify_targets(mod):
    """Stage-1: confirm the configured upload destinations are valid before any build/upload.
    Returns list of failure strings (empty = ok)."""
    fails = []
    cf = mod.get("curseforge")
    if cf:
        _, code = run(["python3", os.path.join(SCRIPT_DIR, "cf_upload.py"), "--verify",
                       "--project-id", str(cf["project_id"]),
                       "--game-version", cf.get("game_version", "1.7.10")], check=False)
        if code != 0:
            fails.append(f"CF project {cf['project_id']} verify failed")
    mr = mod.get("modrinth")
    if mr:
        _, code = run(["python3", os.path.join(SCRIPT_DIR, "modrinth_upload.py"), "--verify",
                       "--project", str(mr["project"])], check=False)
        if code != 0:
            fails.append(f"Modrinth {mr['project']} verify failed")
    return fails


def stage1_checks(mod):
    """Non-destructive pre-pass. Returns (ok, [messages])."""
    repo = os.path.expanduser(mod["repo"])
    msgs = []
    if not os.path.isdir(os.path.join(repo, ".git")):
        return False, [f"not a git repo: {repo}"]
    dirty = dirty_paths(repo, mod.get("ignore_dirty", []))
    if dirty:
        return False, [f"working tree not clean: {', '.join(dirty[:5])}"]
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != mod["branch"]:
        return False, [f"on branch '{cur}', expected '{mod['branch']}'"]
    _, code = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "@{u}"], check=False)
    if code != 0:
        return False, ["no upstream (git push -u once first)"]
    ahead = git(repo, "rev-list", "--count", "@{u}..HEAD")
    if ahead != "0":
        msgs.append(f"{ahead} unpushed commit(s) (push pass will handle)")
    tfails = verify_targets(mod)
    if tfails:
        return False, tfails
    if mod.get("curseforge") or mod.get("modrinth"):
        msgs.append("upload targets verified")
    return True, msgs


def find_jar(repo, mod):
    cands = [p for p in glob.glob(os.path.join(repo, mod["jar_glob"]))
             if not any(x in os.path.basename(p) for x in mod.get("jar_exclude", []))]
    if not cands:
        raise RuntimeError(f"no jar matched {mod['jar_glob']} (excludes {mod.get('jar_exclude')})")
    return max(cands, key=os.path.getmtime)


def release_one(mod, execute, skip_build):
    name = mod["name"]
    repo = os.path.expanduser(mod["repo"])
    version = mod["version"]
    log = lambda m: print(f"  [{name}] {m}")
    print(f"=== {name}  ->  {version} ===")

    if not os.path.isdir(os.path.join(repo, ".git")):
        log(f"SKIP: not a git repo: {repo}")
        return False

    # Gate 1: clean tree (ignoring build-touched noise)
    dirty = dirty_paths(repo, mod.get("ignore_dirty", []))
    if dirty:
        log(f"SKIP: working tree not clean: {', '.join(dirty[:5])}")
        return False

    # Gate 2: expected branch
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != mod["branch"]:
        log(f"SKIP: on branch '{cur}', expected '{mod['branch']}'")
        return False

    # Gate 3: push pass
    up, code = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "@{u}"], check=False)
    if code != 0:
        log("SKIP: no upstream configured (push -u once first)")
        return False
    ahead = git(repo, "rev-list", "--count", "@{u}..HEAD")
    if ahead != "0":
        if execute:
            log(f"push pass: {ahead} unpushed commit(s) -> git push")
            git(repo, "push")
        else:
            log(f"[dry-run] would push {ahead} unpushed commit(s)")

    # Gate 4: tag (local)
    existing = git(repo, "tag", "-l", version)
    # Previous release tag = latest tag that is NOT this version. If the version tag already
    # exists (re-run), look before its commit; otherwise the current latest tag (before tagging).
    if existing:
        prev_tag = git(repo, "describe", "--tags", "--abbrev=0", f"{version}^", check=False)
    else:
        prev_tag = git(repo, "describe", "--tags", "--abbrev=0", check=False)
    if existing:
        log(f"tag {version} already exists (re-using)")
    else:
        if execute:
            git(repo, "tag", version)
            log(f"tagged {version}")
        else:
            log(f"[dry-run] would tag {version}")

    # Gate 5: compile/build pass
    if skip_build:
        log("build skipped (--skip-build)")
    else:
        env = dict(os.environ)
        jh = mod["build"].get("java_home")
        if jh:
            env["JAVA_HOME"] = jh
        log(f"build pass: {' '.join(mod['build']['cmd'])}" + (f"  (JAVA_HOME={jh})" if jh else ""))
        code, out = build_run(mod["build"]["cmd"], cwd=repo, env=env)
        if code != 0:
            log("BUILD FAILED -> rolling back tag, skipping release")
            for ln in out.splitlines()[-8:]:
                print(f"      {ln}")
            if execute and not existing:
                git(repo, "tag", "-d", version, check=False)
            return False
        log("build OK")

    # Locate jar (best-effort in dry-run: build may not have run with a clean tag name)
    try:
        jar = find_jar(repo, mod)
        log(f"jar: {os.path.basename(jar)}")
    except RuntimeError as e:
        log(f"{'SKIP' if execute else '[dry-run] note'}: {e}")
        if execute:
            return False
        jar = None

    # Changelog = commits since previous tag. Format as a markdown bullet list: raw subject lines
    # separated by single newlines collapse into ONE paragraph in markdown (CF/Modrinth/GitHub all
    # render the changelog as markdown), so CF showed the whole changelog on a single line. Bullets
    # render as separate lines everywhere.
    rng = f"{prev_tag}..HEAD" if prev_tag else "HEAD"
    subjects = [s for s in git(repo, "log", "--reverse", "--format=%s", rng, check=False).splitlines()
                if s.strip()]
    log(f"changelog ({rng}): {len(subjects)} commit(s)")
    for line in subjects[:12]:
        print(f"      - {line}")
    changelog = "\n".join(f"- {s}" for s in subjects)

    if not execute:
        tgt = ["GitHub"]
        if mod.get("curseforge"):
            tgt.append(f"CF:{mod['curseforge']['project_id']}")
        if mod.get("modrinth"):
            tgt.append(f"Modrinth:{mod['modrinth']['project']}")
        log(f"[dry-run] would publish to: {', '.join(tgt)}")
        return True

    # --- real publish ---
    git(repo, "push", "origin", version)  # push the tag
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(changelog + "\n")
        notes = f.name
    gh_cmd = ["gh", "release", "create", version, "-t", mod["gh_title"], "-F", notes, jar]
    slug = origin_slug(repo)
    if slug:
        gh_cmd += ["-R", slug]  # target the fork, not gh's default (the upstream parent)
    run(gh_cmd, cwd=repo)
    log(f"GitHub release {version} created ({slug or 'default repo'})")

    if mod.get("curseforge"):
        cf = mod["curseforge"]
        run(["python3", os.path.join(SCRIPT_DIR, "cf_upload.py"),
             "--project-id", str(cf["project_id"]), "--file", jar,
             "--display-name", version, "--game-version", cf.get("game_version", "1.7.10"),
             "--release-type", "release", "--changelog-file", notes], capture=False)
        log("CurseForge upload done")

    if mod.get("modrinth"):
        mr = mod["modrinth"]
        run(["python3", os.path.join(SCRIPT_DIR, "modrinth_upload.py"),
             "--project", mr["project"], "--file", jar, "--version", version,
             "--game-version", mr.get("game_version", "1.7.10"),
             "--release-type", "release", "--changelog-file", notes], capture=False)
        log("Modrinth upload done")

    os.unlink(notes)
    return True


def main():
    ap = argparse.ArgumentParser(description="Bulk-release the pending mods.")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--only", help="release only this mod name")
    ap.add_argument("--execute", action="store_true", help="actually tag/push/publish (default: dry-run)")
    ap.add_argument("--skip-build", action="store_true", help="trust an existing build")
    args = ap.parse_args()

    mods = json.load(open(args.manifest))["mods"]
    if args.only:
        mods = [m for m in mods if m["name"] == args.only]
        if not mods:
            sys.exit(f"no mod named {args.only!r} in manifest")

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"===== release_mods [{mode}] — {len(mods)} mod(s) =====\n")

    # Stage 1: non-destructive checks for ALL mods (fail fast before any build/publish).
    print("----- Stage 1: verify (tree/branch/upstream + upload targets) -----")
    ready = []
    for m in mods:
        try:
            ok, msgs = stage1_checks(m)
        except Exception as e:
            ok, msgs = False, [f"error: {e}"]
        tag = "OK " if ok else "FAIL"
        print(f"  [{tag}] {m['name']}" + (f"  ({'; '.join(msgs)})" if msgs else ""))
        if ok:
            ready.append(m)
    print(f"  -> {len(ready)}/{len(mods)} pass Stage 1\n")
    if not ready:
        sys.exit("Nothing passes Stage 1.")

    # Stage 2: release only the mods that passed Stage 1.
    print("----- Stage 2: release -----")
    done = 0
    for m in ready:
        try:
            if release_one(m, args.execute, args.skip_build):
                done += 1
        except Exception as e:
            print(f"  [{m['name']}] ERROR: {e}")
        print()
    print(f"===== {done}/{len(ready)} {'released' if args.execute else 'ready'} =====")
    if done < len(ready) or len(ready) < len(mods):
        sys.exit(1)  # surface partial failure with a non-zero exit code


if __name__ == "__main__":
    main()
