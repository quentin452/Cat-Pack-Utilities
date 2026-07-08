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
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CF_READ_API = "https://api.curseforge.com"       # read API (CF_API_KEY) — existence checks only
MODRINTH_API = "https://api.modrinth.com/v2"
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


def read_env(key):
    """Read KEY from .env / .env.local (real env wins) — used for the CF_API_KEY read key. Never
    prints the value."""
    if os.environ.get(key):
        return os.environ[key]
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
    return None


# --- idempotency: read-only "already published?" probes per target ---------------------------
# These make a re-run of an already-released version a no-op instead of a duplicate upload
# (gh release create would error; cf_upload would post a DUPLICATE file — there is no dedupe on CF).

def gh_release_exists(slug, version):
    """True if a GitHub release tagged <version> already exists on the fork (`gh release view`)."""
    if not slug:
        return False
    _, code = run(["gh", "release", "view", version, "-R", slug], check=False)
    return code == 0


def _version_matches_file(version, f):
    """A CF/Modrinth file 'carries' this version if the version string appears in its fileName or
    displayName. CF author uploads set displayName = the version we pass (e.g. 'V1.17.5', '1.0.4',
    '1.9.1-fork9'); the raw fileName may differ (build artifact name), so we check BOTH — robust to
    either convention."""
    v = str(version).lower()
    return (v in str(f.get("fileName", "")).lower()
            or v in str(f.get("displayName", "")).lower())


def cf_file_exists(project_id, version, api_key):
    """True if the CF project already has a file for <version>, False if not, None if UNVERIFIABLE
    (no read key or API error — caller decides whether to skip-for-safety or force)."""
    if not api_key:
        return None
    url = f"{CF_READ_API}/v1/mods/{project_id}/files?pageSize=50"
    req = urllib.request.Request(url, method="GET")
    req.add_header("x-api-key", api_key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            files = json.load(r).get("data", [])
    except Exception as e:
        print(f"  [cf-check] WARN: could not query CF files for project {project_id}: {e}")
        return None
    return any(_version_matches_file(version, f) for f in files)


def modrinth_version_exists(project, version):
    """True if the Modrinth project already has a version numbered <version>, False if not, None if
    UNVERIFIABLE (public API, so None = transient error only)."""
    url = f"{MODRINTH_API}/project/{project}/version"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            versions = json.load(r)
    except Exception as e:
        print(f"  [mr-check] WARN: could not query Modrinth for {project}: {e}")
        return None
    return any(str(v.get("version_number")) == str(version) for v in versions)


def publish_plan(mod, slug, cf_api_key, force, force_cf):
    """Read-only per-target plan so dry-run and execute agree and neither duplicates. Returns
    {target: (action, note)} with action in {'publish','skip'}. Targets: 'GitHub' always; 'CF' and
    'Modrinth' only if configured."""
    version = mod["version"]
    plan = {}

    # GitHub — always a target. gh itself refuses a duplicate tag, so on exists we ALWAYS skip
    # (even with --force; there is nothing safe to force here).
    if gh_release_exists(slug, version):
        plan["GitHub"] = ("skip", f"GitHub {version} already released")
    else:
        plan["GitHub"] = ("publish", None)

    if mod.get("curseforge"):
        pid = mod["curseforge"]["project_id"]
        if force:
            plan["CF"] = ("publish", "forced (--force)")
        else:
            exists = cf_file_exists(pid, version, cf_api_key)
            if exists is True:
                plan["CF"] = ("skip", f"CF file for {version} already exists")
            elif exists is False:
                plan["CF"] = ("publish", None)
            elif force_cf:
                plan["CF"] = ("publish", "unverifiable but --force-cf")
            else:  # cannot verify (no CF_API_KEY / API error) -> skip to avoid a duplicate
                plan["CF"] = ("skip", "cannot verify CF (no CF_API_KEY) — skipping to avoid a "
                                      "duplicate; pass --force-cf to upload anyway")

    if mod.get("modrinth"):
        proj = mod["modrinth"]["project"]
        if force:
            plan["Modrinth"] = ("publish", "forced (--force)")
        else:
            exists = modrinth_version_exists(proj, version)
            if exists is True:
                plan["Modrinth"] = ("skip", f"Modrinth {version} already exists")
            elif exists is False:
                plan["Modrinth"] = ("publish", None)
            else:  # public API; None = transient error -> attempt (Modrinth rejects true dupes itself)
                plan["Modrinth"] = ("publish", "could not verify Modrinth — will attempt")
    return plan


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


def release_one(mod, execute, skip_build, changelog_override=None, force=False, force_cf=False,
                cf_api_key=None):
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
    if changelog_override:
        changelog = changelog_override
        log(f"changelog OVERRIDDEN (--changelog-file): {len(changelog.splitlines())} line(s) — curated wording replaces the raw commit subjects")

    # Idempotency: probe every target read-only so a re-run skips what is already published
    # (no duplicate GitHub release / CF file / Modrinth version) instead of erroring or duplicating.
    slug = origin_slug(repo)
    plan = publish_plan(mod, slug, cf_api_key, force, force_cf)

    if not execute:
        for tgt, (action, note) in plan.items():
            if action == "publish":
                log(f"[dry-run] would publish {tgt}" + (f" ({note})" if note else ""))
            else:
                log(f"[dry-run] {tgt}: already released — would skip" + (f" ({note})" if note else ""))
        return True

    if all(action == "skip" for action, _ in plan.values()):
        log(f"already fully released — nothing to publish ({version})")
        return True

    # --- real publish (only the targets that are actually missing) ---
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(changelog + "\n")
        notes = f.name
    try:
        gh_action, gh_note = plan["GitHub"]
        if gh_action == "publish":
            git(repo, "push", "origin", version)  # push the tag
            gh_cmd = ["gh", "release", "create", version, "-t", mod["gh_title"], "-F", notes, jar]
            if slug:
                gh_cmd += ["-R", slug]  # target the fork, not gh's default (the upstream parent)
            run(gh_cmd, cwd=repo)
            log(f"GitHub release {version} created ({slug or 'default repo'})")
        else:
            log(f"GitHub: {gh_note} — skip")

        if mod.get("curseforge"):
            cf = mod["curseforge"]
            cf_action, cf_note = plan["CF"]
            if cf_action == "publish":
                cf_cmd = ["python3", os.path.join(SCRIPT_DIR, "cf_upload.py"),
                          "--project-id", str(cf["project_id"]), "--file", jar,
                          "--display-name", version, "--game-version", cf.get("game_version", "1.7.10"),
                          "--release-type", "release", "--changelog-file", notes]
                if force:
                    cf_cmd.append("--force")  # let cf_upload's own duplicate guard through too
                run(cf_cmd, capture=False)
                log("CurseForge upload done")
            else:
                log(f"CurseForge: {cf_note} — skip")

        if mod.get("modrinth"):
            mr = mod["modrinth"]
            mr_action, mr_note = plan["Modrinth"]
            if mr_action == "publish":
                run(["python3", os.path.join(SCRIPT_DIR, "modrinth_upload.py"),
                     "--project", mr["project"], "--file", jar, "--version", version,
                     "--game-version", mr.get("game_version", "1.7.10"),
                     "--release-type", "release", "--changelog-file", notes], capture=False)
                log("Modrinth upload done")
            else:
                log(f"Modrinth: {mr_note} — skip")
    finally:
        os.unlink(notes)
    return True


def main():
    ap = argparse.ArgumentParser(description="Bulk-release the pending mods.")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--only", help="release only this mod name")
    ap.add_argument("--execute", action="store_true", help="actually tag/push/publish (default: dry-run)")
    ap.add_argument("--skip-build", action="store_true", help="trust an existing build")
    ap.add_argument("--changelog-file", help="curated release notes (markdown); only with --only")
    ap.add_argument("--force", action="store_true",
                    help="publish to EVERY configured target even if it already has this version "
                         "(danger: re-uploads a duplicate CF/Modrinth file). GitHub is never forced.")
    ap.add_argument("--force-cf", action="store_true",
                    help="upload to CurseForge even when it can't be verified (no CF_API_KEY read "
                         "key); default without a key is to SKIP the CF upload for safety")
    args = ap.parse_args()

    mods = json.load(open(args.manifest))["mods"]
    # Skip pack-delivery-only entries (no build config) — those exist purely to drive pack_sync
    # (e.g. a fork already released upstream-by-me, tracked only for the pack bundle version).
    mods = [m for m in mods if m.get("build")]
    if args.only:
        mods = [m for m in mods if m["name"] == args.only]
        if not mods:
            sys.exit(f"no releasable mod named {args.only!r} in manifest (pack-only entries are skipped)")
    override = None
    if args.changelog_file:
        if not args.only:
            sys.exit("--changelog-file requires --only (one curated text for one mod)")
        with open(args.changelog_file, encoding="utf-8") as f:
            override = f.read().strip()
        if not override:
            sys.exit(f"--changelog-file {args.changelog_file} is empty")

    cf_api_key = read_env("CF_API_KEY")  # read key (never printed) — for the CF existence probe

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
            if release_one(m, args.execute, args.skip_build, changelog_override=override,
                           force=args.force, force_cf=args.force_cf, cf_api_key=cf_api_key):
                done += 1
        except Exception as e:
            print(f"  [{m['name']}] ERROR: {e}")
        print()
    print(f"===== {done}/{len(ready)} {'released' if args.execute else 'ready'} =====")
    if done < len(ready) or len(ready) < len(mods):
        sys.exit(1)  # surface partial failure with a non-zero exit code


if __name__ == "__main__":
    main()
