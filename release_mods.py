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
  6. BOOT-VERIFY pass         (mods with a boot_test block: boot a fast minimal instance with the
                             freshly built jar + scan the boot log — a mixin that compiles green can
                             still throw InvalidInjectionException at APPLY time. FAIL -> no publish.
                             Boots the game, so runs single-instance; --skip-boot bypasses it)
Then release: push tag -> GitHub release (gh) -> CurseForge (cf_upload.py) -> Modrinth
(modrinth_upload.py), each only if configured. Changelog = git commits since the previous tag.

DRY-RUN BY DEFAULT. Pass --execute to actually tag/push/publish.

Usage:
  release_mods.py                     # dry-run all mods (gates + plan, no publish)
  release_mods.py --only OptimizationsAndTweaks
  release_mods.py --execute           # for real (boots the minimal instance for boot-verify)
  release_mods.py --execute --skip-build   # trust an existing build (not recommended)
  release_mods.py --execute --skip-boot    # bypass the boot-verify gate (not recommended)
"""

import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import packenv as E  # noqa: E402 — shared path/id/secret source of truth
import make_minimal_instance as mmi  # noqa: E402 — reuse poll_boot/kill_instance/INSTANCES/JAVA
from modrinth_upload import normalize_modrinth_version  # noqa: E402 — CONCERN B: shared strip-V
from changelog_from_git import commit_type  # noqa: E402 — shared conventional-commit type classifier
import release_log  # noqa: E402 — append-only release-log.md logger (never throws)

CF_READ_API = "https://api.curseforge.com"       # read API (CF_API_KEY) — existence checks only
MODRINTH_API = "https://api.modrinth.com/v2"
# The manifest (release plan) lives in the Mod-Sandbox hub (planning data, versioned +
# auto-pushed, next to pipeline-mods.md). Override with --manifest.
MANIFEST = E.RELEASE_MANIFEST
# BOOT-VERIFY gate helpers (sibling scripts, reused as subprocesses / a module).
MMI_SCRIPT = os.path.join(SCRIPT_DIR, "make_minimal_instance.py")
CRASH_SCAN = os.path.join(SCRIPT_DIR, "boot_crash_scan.py")
BOOT_TIMEOUT = 300  # seconds to wait for the minimal instance to reach a world (~5 min)


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


# --- auto-versioning: derive the NEXT release version from git tags ---------------------------
# CONCERN A: the manifest `version` literal goes stale (a released version stays named -> the
# idempotency guard below would SKIP and the pending commits would never ship, e.g. OaT V1.17.5).
# So DERIVE the next version from the latest release tag on the branch + a bump, unless the author
# pins (bump=none) or overrides it (a not-yet-tagged version ahead of the latest tag).

FORK_RE = re.compile(r"^(?P<base>.+-fork)(?P<n>\d+)$", re.I)          # <upstream>-fork<N>
SEMVER_RE = re.compile(r"^(?P<prefix>[Vv]?)(?P<nums>\d+(?:\.\d+)*)(?P<suffix>.*)$")  # [V]x.y.z[suffix]


def _style_sig(v):
    """A version's STYLE signature, so we only compare a mod's OWN tag family. A fork repo also
    carries upstream semver tags (0.7.11 vs 0.7.11-fork4); a -CAT fork also carries -GTNH tags.
    Same sig = same lineage — this is what makes 'the latest tag on the branch' the right one."""
    if not v:
        return None
    if FORK_RE.match(v):
        return ("fork",)                       # any <base>-fork<N>
    m = SEMVER_RE.match(v)
    if m:
        return ("semver", m.group("prefix").upper(), m.group("suffix"))
    return ("other", v)


def bump_version(ver, bump="patch"):
    """Compute the NEXT version, preserving STYLE. Fork <base>-fork<N> -> N+1 (single axis). Dotted
    [V]x.y.z[suffix]: patch bumps the last number, minor the 2nd-to-last, major the first (zeroing
    the components after the bumped one). The V prefix and any trailing suffix (e.g. -CAT) survive."""
    m = FORK_RE.match(ver)
    if m:
        return f"{m.group('base')}{int(m.group('n')) + 1}"
    m = SEMVER_RE.match(ver)
    if not m:
        raise ValueError(f"cannot bump unrecognized version style: {ver!r}")
    prefix, suffix = m.group("prefix"), m.group("suffix")
    nums = [int(x) for x in m.group("nums").split(".")]
    n = len(nums)
    idx = 0 if bump == "major" else max(0, n - 2) if bump == "minor" else n - 1  # else patch
    nums[idx] += 1
    for j in range(idx + 1, n):
        nums[j] = 0
    return prefix + ".".join(str(x) for x in nums) + suffix


BREAKING_BODY_RE = re.compile(r"BREAKING[ -]CHANGE", re.I)


def derive_bump(repo, from_ref, to_ref="HEAD", log=None):
    """Derive the semver bump level from conventional-commit types in `git log <from_ref>..<to_ref>`
    (merges excluded). Reuses changelog_from_git.commit_type — the SAME classifier the pending
    changelog uses, so 'what bump did we pick' and 'what does the changelog say' never disagree.

      major - ANY commit has '!' before the colon (feat!:, fix(x)!:) OR a 'BREAKING CHANGE' /
               'BREAKING-CHANGE' token in the body.
      minor - else ANY 'feat:' / 'feat(scope):'.
      patch - else (fix/perf/refactor/revert/config/security/build/chore/docs/test/style/ci/
               unlabeled) — always at least patch, since there ARE commits in range (caller only
               calls this when commits_since != 0).

    `log`, if given, is called with a one-line reason (chosen level + what triggered it) — the
    caller (resolve_version) doesn't have to re-derive the explanation."""
    rng = f"{from_ref}..{to_ref}" if from_ref else to_ref
    out = git(repo, "log", "--no-merges", "--format=%s%x00%b%x1e", rng, check=False)
    records = [r for r in (out or "").split("\x1e") if r.strip("\n")]
    major_hits, minor_hits = [], []
    for rec in records:
        subject, _, body = rec.partition("\x00")
        subject = subject.strip()
        typ, breaking = commit_type(subject)
        if breaking or BREAKING_BODY_RE.search(body):
            major_hits.append(subject)
        elif typ == "feat":
            minor_hits.append(subject)
    if major_hits:
        bump, why = "major", f"BREAKING in {len(major_hits)} commit(s), e.g. {major_hits[0]!r}"
    elif minor_hits:
        bump, why = "minor", f"feat: in {len(minor_hits)} commit(s), e.g. {minor_hits[0]!r}"
    else:
        bump, why = "patch", f"no feat/breaking among {len(records)} commit(s)"
    if log:
        log(f"derive_bump: {rng} -> {bump} ({why})")
    return bump


def _latest_style_tag(repo, ref_version):
    """Latest release tag reachable from HEAD whose STYLE matches ref_version (the manifest literal
    is the lineage marker). None if none match."""
    sig = _style_sig(ref_version)
    out = git(repo, "tag", "--merged", "HEAD", "--sort=-creatordate", check=False)
    for t in out.splitlines():
        t = t.strip()
        if t and _style_sig(t) == sig:
            return t
    return None


def resolve_version(mod, repo, log):
    """CONCERN A: choose the NEXT version to release from git, not a stale manifest literal.
    Returns (version, skip_reason). skip_reason set (version None) = 'nothing to release'.

    Rules:
      - bump == 'none'  -> PIN: use the manifest version verbatim (deliberate re-release over the
        SAME tag, e.g. EssenceOfTheGods keeping its url.bundle pin).
      - manifest version is a NOT-YET-TAGGED value that differs from the latest tag -> OVERRIDE
        (a real minor/major the author wants): use it verbatim.
      - else AUTO: latest style-matched tag + a bump. bump ABSENT or 'auto' (the default) ->
        DERIVE the level from conventional-commit types since that tag (derive_bump); an EXPLICIT
        'patch'/'minor'/'major' in the manifest still overrides (the author's manual call wins).
        HEAD == that tag (0 new commits) -> nothing to release, skip (idempotent)."""
    name = mod["name"]
    manifest_ver = mod.get("version")
    bump = mod.get("bump", "auto")
    if bump == "none":
        log(f"auto-version: {name} PINNED at {manifest_ver} (bump=none — re-release over same tag)")
        return manifest_ver, None
    latest = _latest_style_tag(repo, manifest_ver)
    if not latest:
        log(f"auto-version: {name} no prior tag in this version style — using manifest {manifest_ver} "
            f"(cannot derive a next)")
        return manifest_ver, None
    # explicit override: a manifest version the author bumped ahead by hand and hasn't tagged yet
    if manifest_ver and manifest_ver != latest and not git(repo, "tag", "-l", manifest_ver):
        log(f"auto-version: {name} using manifest OVERRIDE {manifest_ver} (explicit; latest tag {latest})")
        return manifest_ver, None
    commits_since = git(repo, "rev-list", "--count", f"{latest}..HEAD", check=False) or "0"
    if commits_since == "0":
        return None, f"HEAD == latest tag {latest} — no new commits, nothing to release"
    if bump == "auto":
        bump = derive_bump(repo, latest, "HEAD", log=log)
        log(f"auto-version: {name} bump=auto -> using derived '{bump}'")
    try:
        nxt = bump_version(latest, bump)
    except ValueError as e:
        log(f"auto-version: {name} {e} — falling back to manifest {manifest_ver}")
        return manifest_ver, None
    log(f"auto-version: {name} {latest} -> {nxt} ({commits_since} commits since tag, {bump} bump)")
    return nxt, None


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
    """A CF file 'carries' this version if a BOUNDARY-AWARE match of the version appears in its
    fileName or displayName (checked BOTH — displayName = the version we pass, e.g. 'V1.17.5',
    '1.0.4', '1.9.1-fork9'; the raw fileName may differ).

    Plain substring was asymmetric: it false-SKIPPED ('v1.1.1' in 'v1.1.10' -> thinks 1.1.1 exists)
    and false-DUPED (our 'V1.17.5' vs a CF file displayed '1.17.5' -> miss -> duplicate upload). So we
    reuse release_pack.pack_already_published's proven boundary pattern `re.escape(v)(?![0-9.])` (+ a
    left `(?<![0-9.])` so a bare-number version can't match inside a longer number), and — like the
    Modrinth path — compare BOTH the 'V1.17.5' and stripped '1.17.5' forms so a leading-V convention
    drift on CF can't defeat the dup check."""
    v = str(version)
    stripped = v[1:] if v[:1] in ("V", "v") else v
    cands = {v, stripped, "V" + stripped}          # both V-prefixed and bare forms
    pats = [re.compile(r"(?<![0-9.])" + re.escape(c) + r"(?![0-9.])", re.I) for c in cands]
    for field in ("fileName", "displayName"):
        s = str(f.get(field, ""))
        if any(p.search(s) for p in pats):
            return True
    return False


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


def publish_plan(mod, version, slug, cf_api_key, force_cf, force_modrinth):
    """Read-only per-target plan so dry-run and execute agree and neither duplicates. Returns
    {target: (action, note)} with action in {'publish','clobber','skip'}. Targets: 'GitHub' always;
    'CF' and 'Modrinth' only if configured. `version` is the RESOLVED version (auto-derived), not the
    raw manifest literal.

    FORCE is PER-TARGET (never a single blast-all --force): `force_cf` forces the CF leg (upload even
    when it already has the version, or when it can't be verified), `force_modrinth` forces the
    Modrinth leg. This keeps 'retry the ONE leg that failed' from accidentally duplicating the others.
    """
    plan = {}

    # GitHub — always a target. gh refuses a duplicate tag, so on a normal already-released mod we
    # skip. EXCEPTION: bump=none is a deliberate RE-release over the SAME tag (url.bundle pins that
    # tag's asset), so skipping GitHub would ship the OLD jar — instead CLOBBER the asset in place
    # (gh release upload --clobber) so it actually updates.
    bump_none = mod.get("bump") == "none"
    if gh_release_exists(slug, version):
        if bump_none:
            plan["GitHub"] = ("clobber", f"GitHub {version} exists — bump=none re-release, replacing "
                                         f"the asset in place (gh release upload --clobber)")
        else:
            plan["GitHub"] = ("skip", f"GitHub {version} already released")
    else:
        plan["GitHub"] = ("publish", None)

    if mod.get("curseforge"):
        pid = mod["curseforge"]["project_id"]
        exists = cf_file_exists(pid, version, cf_api_key)
        if exists is False:
            plan["CF"] = ("publish", None)
        elif force_cf:
            # force_cf = upload the CF leg regardless: known DUPLICATE, or UNVERIFIABLE (no read key).
            note = ("forced (--force-cf) — CF already has this version, uploading a DUPLICATE"
                    if exists is True else "unverifiable but --force-cf")
            plan["CF"] = ("publish", note)
        elif exists is True:
            plan["CF"] = ("skip", f"CF file for {version} already exists")
        else:  # cannot verify (no CF_API_KEY / API error) -> skip to avoid a duplicate
            plan["CF"] = ("skip", "cannot verify CF (no CF_API_KEY) — skipping to avoid a "
                                  "duplicate; pass --force-cf to upload anyway")

    if mod.get("modrinth"):
        proj = mod["modrinth"]["project"]
        # CONCERN B: Modrinth uses a bare version ('1.17.1'); strip the leading V so BOTH the dedup
        # probe AND the upload compare the same string (default strip-v; per-mod override).
        mr_fmt = mod["modrinth"].get("version_format", "strip-v")
        mr_ver = normalize_modrinth_version(version, mr_fmt)
        exists = modrinth_version_exists(proj, mr_ver)
        if exists is True and not force_modrinth:
            plan["Modrinth"] = ("skip", f"Modrinth {mr_ver} already exists")
        elif exists is True:  # force_modrinth
            plan["Modrinth"] = ("publish", f"forced (--force-modrinth) DUPLICATE; as {mr_ver}")
        elif exists is False:
            plan["Modrinth"] = ("publish", f"as {mr_ver}")
        else:  # public API; None = transient error -> attempt (Modrinth rejects true dupes itself)
            plan["Modrinth"] = ("publish", f"as {mr_ver}; could not verify Modrinth — will attempt")
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


# --- BOOT-VERIFY gate --------------------------------------------------------------------------
# Compile-green is NOT enough to ship: a mixin can compile yet throw InvalidInjectionException at
# APPLY time and ship broken. This gate assembles a fast minimal instance (make_minimal_instance),
# boots it with the FRESHLY BUILT jar, and scans the boot log (boot_crash_scan) for a fatal that is
# attributable to THIS mod or to any mixin-apply failure. It BOOTS the game, so it obeys the
# single-instance rule: kill any running MC first + confirm 0 (the JVM re-execs, so re-scan the
# real `bin/java` procs each pass — never trust one kill). Live boot is meant to run on the MAIN
# thread; dry-run only prints the plan (never boots).

def _running_mc_pids():
    """PIDs of live Minecraft JVMs (real `bin/java` procs, matched by the launch markers — not the
    `ps` line of this scan itself, which has no `bin/java`)."""
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        if "bin/java" not in line:
            continue
        low = line.lower()
        if ("curseforge/minecraft" in low or "minimal-derisk" in low
                or "launchwrapper" in low or ".arg" in low):
            try:
                pids.append(int(line.split()[0]))
            except (ValueError, IndexError):
                pass
    return pids


def _kill_all_minecraft(log):
    """Kill every running MC JVM then CONFIRM 0 (re-scan each pass; the JVM re-execs with a new
    PID). Returns True once 0 is confirmed, False if it could not be cleared."""
    pids = _running_mc_pids()
    if not pids:
        log("boot-verify: 0 MC process(es) running — clear to boot")
        return True
    log("boot-verify: killing %d running MC process(es) %s (single-instance)" % (len(pids), pids))
    for _ in range(12):
        for pid in _running_mc_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(1)
        if not _running_mc_pids():
            log("boot-verify: confirmed 0 MC process(es)")
            return True
    log("boot-verify: WARN could not confirm 0 MC (%d left) — aborting gate" % len(_running_mc_pids()))
    return False


def _minimal_dest(name):
    """The instance dir make_minimal_instance builds for --name <name> (same sanitization)."""
    return os.path.join(mmi.INSTANCES, "Minimal-DeRisk-" + re.sub(r"[^\w.-]", "_", name))


def _minimal_args(mod, jar, bt, name):
    """Translate a manifest boot_test block into make_minimal_instance CLI args (WITHOUT --boot).
    Returns (targets, args). targets = the --mod list (make_minimal requires >=1)."""
    targets = bt.get("mod")
    targets = targets if isinstance(targets, list) else ([targets] if targets else [])
    args = ["--name", name, "--autoworld", bt.get("autoworld", "default")]
    for m in targets:
        args += ["--mod", m]
    for e in bt.get("extra", []):
        args += ["--extra", e]
    if bt.get("use_oat") and jar:
        args += ["--oat", jar]              # the built jar IS the OaT-under-test
    if bt.get("pack"):
        args += ["--pack", os.path.expanduser(bt["pack"])]
    if bt.get("heal_max") is not None:
        args += ["--heal-max", str(bt["heal_max"])]
    return targets, args


def _inject_built_jar(dest, targets, jar, log):
    """Non-OaT case: replace the stale pack copy of the mod-under-test in the assembled instance
    with the freshly built jar (make_minimal pulls the --mod target from the pack)."""
    dest_mods = os.path.join(dest, "mods")
    for tok in targets:
        for p in glob.glob(os.path.join(dest_mods, "*.jar")):
            if tok and tok.lower() in os.path.basename(p).lower():
                os.remove(p)
                log("  boot-verify: removed stale %s" % os.path.basename(p))
    shutil.copy(jar, os.path.join(dest_mods, os.path.basename(jar)))
    log("  boot-verify: injected freshly built %s" % os.path.basename(jar))


def _boot_and_poll(dest):
    """Boot an already-assembled instance and wait (reuses make_minimal_instance.poll_boot).
    Returns 'world'|'deps'|'crash'|'timeout'."""
    argfile = os.path.join(dest, "minimal.arg")
    argname = "minimal.arg"
    boot_log = os.path.join(dest, "boot.log")
    mmi.kill_instance(argname)
    with open(boot_log, "w") as lf:
        subprocess.Popen([mmi.JAVA, "@" + argfile], cwd=dest, stdout=lf, stderr=lf,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    return mmi.poll_boot(dest, argname, timeout=BOOT_TIMEOUT)


def scan_boot_log(boot_log, mod, bt, log):
    """Run boot_crash_scan on a boot log and decide PASS/FAIL. PASS = reached world/menu with no
    fatal. FAIL = a fatal attributable to THIS mod (culprit modid) OR any mixin-apply failure
    (InvalidInjectionException / 'apply failed' / 'was not applied' / critical injection). A fatal
    blamed on an UNRELATED pack mod (a minimal-instance dep gap, not our code) is a loud WARN, not
    a blocker. A clean-but-never-reached-world boot is UNVERIFIED -> fail-safe FAIL. Isolated so the
    main thread can call it directly on any existing boot log."""
    target = boot_log if os.path.isfile(boot_log) else os.path.dirname(boot_log)
    out, code = run(["python3", CRASH_SCAN, target], check=False)
    for ln in out.splitlines():
        if ln.strip():
            print("      " + ln)
    low = out.lower()
    if code != 0:  # boot_crash_scan flagged a fatal
        mixin_fatal = any(s in low for s in ("invalidinjection", "critical injection",
                          "apply failed", "was not applied", "mixin failures"))
        token = re.sub(r"[^a-z0-9]", "", (bt.get("modid") or mod["name"]).lower())
        if mixin_fatal or (token and token in re.sub(r"[^a-z0-9]", "", low)):
            log("boot-verify: FATAL is a mixin-apply failure / attributed to THIS mod — GATE FAIL")
            return False
        # ACCEPTED RISK (audit #3, by design): a fatal NOT attributed to this mod is a WARN, not a
        # block — a minimal-instance dep gap must not fail a good mod. Trade-off: a crash this mod
        # truly caused but that attribution misses would slip through as a WARN. Kept intentionally.
        log("boot-verify: WARN boot has a FATAL but it is NOT attributed to this mod (likely a "
            "minimal-instance dependency gap) — not blocking; inspect %s" % boot_log)
        return True
    if ":: ok ==" in low:  # boot_crash_scan tags OK only when a client/world success marker hit
        log("boot-verify: PASS — booted to world/menu, mixins applied, no fatal captured")
        return True
    log("boot-verify: UNVERIFIED — no fatal but boot did not reach a world (INCONCLUSIVE); "
        "fail-safe GATE FAIL. Inspect %s" % boot_log)
    return False


def boot_verify(mod, jar, execute, skip_boot, log):
    """Gate: the mod must actually BOOT (mixins applied, no crash) before it is published.
    Returns True to allow publish, False to block it. dry-run prints the plan without booting."""
    if skip_boot:
        log("boot-verify: SKIPPED (--skip-boot)")
        return True
    bt = mod.get("boot_test")
    if not bt:
        log("boot-verify: no boot_test config in manifest — gate SKIPPED (add a boot_test block "
            "to enable apply-time verification for this mod)")
        return True
    name = "release-verify-" + re.sub(r"[^\w.-]", "-", mod["name"])
    dest = _minimal_dest(name)
    boot_log = os.path.join(dest, "boot.log")
    targets, mm_args = _minimal_args(mod, jar, bt, name)
    if not targets:
        log("boot-verify: boot_test has no 'mod' target (make_minimal requires one) — gate SKIPPED")
        return True
    use_oat = bool(bt.get("use_oat"))

    if not execute:
        log("[dry-run] boot-verify plan (gate ON; boots the game — run live via the main thread):")
        log("  1. kill %d running MC process(es) + confirm 0" % len(_running_mc_pids()))
        if use_oat:
            log("  2. python3 make_minimal_instance.py %s --boot" % " ".join(mm_args))
        else:
            log("  2. python3 make_minimal_instance.py %s   (assemble only)" % " ".join(mm_args))
            log("     then inject freshly built %s over the pack copy, then boot + poll"
                % (os.path.basename(jar) if jar else "<jar>"))
        log("  3. boot_crash_scan %s  (FAIL on InvalidInjectionException / mixin-apply failure / "
            "culprit=this mod; PASS on world reached + no fatal)" % boot_log)
        return True

    # --- EXECUTE: BOOTS the game (single-instance). Run by the main thread. ---
    if jar is None:
        log("boot-verify: no jar located — cannot boot-verify; GATE FAIL")
        return False
    if not _kill_all_minecraft(log):
        return False
    if use_oat:
        # make_minimal --boot = assemble + boot + heal missing deps (full reuse), writes boot.log.
        log("boot-verify: booting minimal instance via make_minimal_instance --boot (~90s-5min)…")
        try:
            subprocess.run(["python3", MMI_SCRIPT] + mm_args + ["--boot"],
                           timeout=BOOT_TIMEOUT + 120)
        except subprocess.TimeoutExpired:
            log("boot-verify: make_minimal_instance --boot timed out — killing + scanning partial log")
        except Exception as e:
            log("boot-verify: make_minimal_instance error: %s" % e)
            _kill_all_minecraft(log)
            return False
    else:
        log("boot-verify: assembling minimal instance…")
        _out, code = run(["python3", MMI_SCRIPT] + mm_args, check=False)
        if code != 0:
            log("boot-verify: assembly failed — GATE FAIL")
            return False
        _inject_built_jar(dest, targets, jar, log)
        log("boot-verify: booting instance (poll_boot, ~90s-5min)…")
        status = _boot_and_poll(dest)
        log("boot-verify: poll_boot -> %s" % status)
    ok = scan_boot_log(boot_log, mod, bt, log)
    _kill_all_minecraft(log)  # never leave the gate's instance running
    return ok


def verify_one(mod, skip_build, skip_boot):
    """--verify: run the SAME local checks the pre-upload path runs (stage1 + build + boot-verify),
    LOG each as a gate to release-log.md (mode=VERIFY), then STOP. This function contains NO tag /
    push / gh / cf_upload / modrinth code at all: it CANNOT reach an upload by construction — it is
    the local build/test/audit, fully separated from the outward publish.

    It actually builds (honoring --skip-build) and actually boots the boot-verify gate (honoring
    --skip-boot) — it is the real local audit, not just a plan print (dry-run already prints plans).
    Returns True if every executed gate passed. Never uploads/tags/pushes under any argument."""
    name = mod["name"]
    repo = os.path.expanduser(mod["repo"])
    log = lambda m: print(f"  [{name}] {m}")

    if not os.path.isdir(os.path.join(repo, ".git")):
        print(f"=== {name}  [VERIFY] ===")
        log(f"SKIP: not a git repo: {repo}")
        rl = release_log.open_run(name, mod.get("version") or "?", "VERIFY", repo=repo)
        rl.gate("git-repo", False, "not a git repo")
        rl.finish("SKIP", f"not a git repo: {repo}")
        return False

    version, skip_reason = resolve_version(mod, repo, log)
    rl = release_log.open_run(name, version or (mod.get("version") or "?"), "VERIFY", repo=repo)
    if skip_reason:
        print(f"=== {name}  ->  (up to date)  [VERIFY] ===")
        log(skip_reason + " — SKIP")
        rl.gate("resolve-version", None, skip_reason)
        rl.finish("SKIP", "up to date — nothing to verify")
        return True
    print(f"=== {name}  ->  {version}  [VERIFY] ===")

    ok = True

    # Stage-1: tree/branch/upstream + upload-target reachability (read-only probes; NEVER uploads).
    try:
        s_ok, s_msgs = stage1_checks(mod)
    except Exception as e:
        s_ok, s_msgs = False, [f"error: {e}"]
    rl.gate("stage1 (tree/branch/upstream+targets)", s_ok, "; ".join(s_msgs))
    log(f"stage1: {'OK' if s_ok else 'FAIL'}" + (f" ({'; '.join(s_msgs)})" if s_msgs else ""))
    ok = ok and s_ok

    # Build (local compile) — honor --skip-build. NO tagging here (tagging is an upload-path step).
    if skip_build:
        log("build skipped (--skip-build)")
        rl.gate("build", None, "--skip-build")
    else:
        env = dict(os.environ)
        jh = mod["build"].get("java_home")
        if jh:
            env["JAVA_HOME"] = jh
        log(f"build pass: {' '.join(mod['build']['cmd'])}" + (f"  (JAVA_HOME={jh})" if jh else ""))
        code, out = build_run(mod["build"]["cmd"], cwd=repo, env=env)
        b_ok = code == 0
        if not b_ok:
            for ln in out.splitlines()[-8:]:
                print(f"      {ln}")
        rl.gate("build", b_ok, "green + jar" if b_ok else f"gradle exit {code}")
        log("build " + ("OK" if b_ok else "FAILED"))
        ok = ok and b_ok

    # Locate the jar (best-effort — boot-verify needs it; a missing jar is a note, not a hard fail).
    jar = None
    try:
        jar = find_jar(repo, mod)
        log(f"jar: {os.path.basename(jar)}")
    except RuntimeError as e:
        log(f"[verify] note: {e}")

    # Boot-verify — actually boots (execute=True) unless --skip-boot / no boot_test. NEVER publishes.
    if skip_boot:
        boot_verify(mod, jar, True, True, log)  # logs the SKIPPED line
        rl.gate("boot-verify", None, "--skip-boot")
    elif not mod.get("boot_test"):
        boot_verify(mod, jar, True, False, log)  # logs the no-boot_test SKIP reason
        rl.gate("boot-verify", None, "no boot_test block")
    else:
        bv_ok = boot_verify(mod, jar, True, False, log)
        rl.gate("boot-verify", bv_ok, "booted + scanned")
        ok = ok and bv_ok

    outcome = "VERIFY PASS" if ok else "VERIFY FAIL"
    rl.finish(outcome, "local build/test/audit only — no tag/push/upload (--verify)")
    log(outcome + " — stopped before any publish (--verify never uploads)")
    return ok


def release_one(mod, execute, skip_build, changelog_override=None, force_cf=False,
                force_modrinth=False, cf_api_key=None, skip_boot=False):
    name = mod["name"]
    repo = os.path.expanduser(mod["repo"])
    log = lambda m: print(f"  [{name}] {m}")
    mode = "UPLOAD" if execute else "DRY-RUN"  # release-log mode for THIS run

    if not os.path.isdir(os.path.join(repo, ".git")):
        print(f"=== {name} ===")
        log(f"SKIP: not a git repo: {repo}")
        rl = release_log.open_run(name, mod.get("version") or "?", mode, repo=repo)
        rl.gate("git-repo", False, "not a git repo")
        rl.finish("SKIP", f"not a git repo: {repo}")
        return False

    # Auto-version (CONCERN A): derive the NEXT version from git tags, not the stale manifest literal.
    version, skip_reason = resolve_version(mod, repo, log)
    rl = release_log.open_run(name, version or (mod.get("version") or "?"), mode, repo=repo)
    if skip_reason:
        print(f"=== {name}  ->  (up to date) ===")
        log(skip_reason + " — SKIP")
        rl.gate("resolve-version", None, skip_reason)
        rl.finish("SKIP", "up to date — nothing to release")
        return True
    print(f"=== {name}  ->  {version} ===")

    # Gate 1: clean tree (ignoring build-touched noise)
    dirty = dirty_paths(repo, mod.get("ignore_dirty", []))
    if dirty:
        log(f"SKIP: working tree not clean: {', '.join(dirty[:5])}")
        rl.gate("clean-tree", False, ", ".join(dirty[:5]))
        rl.finish("FAIL", "working tree not clean")
        return False

    # Gate 2: expected branch
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if cur != mod["branch"]:
        log(f"SKIP: on branch '{cur}', expected '{mod['branch']}'")
        rl.gate("branch", False, f"on '{cur}', expected '{mod['branch']}'")
        rl.finish("FAIL", "wrong branch")
        return False

    # Gate 3: push pass
    up, code = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "@{u}"], check=False)
    if code != 0:
        log("SKIP: no upstream configured (push -u once first)")
        rl.gate("upstream", False, "no upstream configured")
        rl.finish("FAIL", "no upstream")
        return False
    rl.gate("preflight (tree/branch/upstream)", True)
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
    # tag_created = this run created a NEW local tag (execute + not pre-existing). ANY later failure
    # (build, boot-verify, or a publish leg) must roll it back — else the tag stays at HEAD, next run's
    # resolve_version sees 0 commits since it and SILENTLY skips the mod (never shipped), and the
    # partial-publish retry never runs. A pre-existing tag (re-run / bump=none) is NEVER deleted.
    tag_created = False
    if existing:
        log(f"tag {version} already exists (re-using)")
    else:
        if execute:
            git(repo, "tag", version)
            tag_created = True
            log(f"tagged {version}")
        else:
            log(f"[dry-run] would tag {version}")

    # Gate 5: compile/build pass
    if skip_build:
        log("build skipped (--skip-build)")
        rl.gate("build", None, "--skip-build")
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
            if tag_created:
                git(repo, "tag", "-d", version, check=False)
            rl.gate("build", False, f"gradle exit {code}")
            rl.finish("FAIL", "build failed")
            return False
        log("build OK")
        rl.gate("build", True, "green + jar")

    # Locate jar (best-effort in dry-run: build may not have run with a clean tag name)
    try:
        jar = find_jar(repo, mod)
        log(f"jar: {os.path.basename(jar)}")
    except RuntimeError as e:
        log(f"{'SKIP' if execute else '[dry-run] note'}: {e}")
        if execute:
            rl.gate("jar", False, str(e))
            rl.finish("FAIL", "no jar located")
            return False
        jar = None

    # Gate 6: BOOT-VERIFY — the mod must actually BOOT (mixins applied, no crash) before publish.
    # Compile-green is not enough: a mixin can compile yet throw InvalidInjectionException at
    # apply-time. On failure the release for THIS mod is skipped (tag/build stay; publish does not).
    if not boot_verify(mod, jar, execute, skip_boot, log):
        log("BOOT-VERIFY FAILED -> not publishing this mod")
        if tag_created:  # FIX 1: don't let a boot-verify fail leave a tag that masks the mod as shipped
            git(repo, "tag", "-d", version, check=False)
            log(f"rolled back tag {version} (boot-verify failed — re-run re-derives + retries)")
        rl.gate("boot-verify", False, "mixin-apply / attributed fatal")
        rl.finish("FAIL", "boot-verify failed")
        return False
    _bv_skipped = skip_boot or not mod.get("boot_test")
    rl.gate("boot-verify", None if _bv_skipped else True,
            "--skip-boot" if skip_boot else ("no boot_test block" if not mod.get("boot_test")
                                             else "booted + scanned"))

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
    plan = publish_plan(mod, version, slug, cf_api_key, force_cf, force_modrinth)
    plan_summary = "; ".join(f"{t}={a}" for t, (a, _) in plan.items())
    rl.gate("publish-plan (idempotency)", True, plan_summary)

    if not execute:
        for tgt, (action, note) in plan.items():
            if action == "publish":
                log(f"[dry-run] would publish {tgt}" + (f" ({note})" if note else ""))
            elif action == "clobber":
                log(f"[dry-run] would re-upload {tgt} asset" + (f" ({note})" if note else ""))
            else:
                log(f"[dry-run] {tgt}: already released — would skip" + (f" ({note})" if note else ""))
        rl.finish("DRY-RUN", f"plan: {plan_summary}")
        return True

    if all(action == "skip" for action, _ in plan.values()):
        log(f"already fully released — nothing to publish ({version})")
        rl.finish("SKIP", f"already fully released ({version})")
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
        elif gh_action == "clobber":  # FIX 2: bump=none re-release — replace the asset on the same tag
            gh_cmd = ["gh", "release", "upload", version, jar, "--clobber"]
            if slug:
                gh_cmd += ["-R", slug]
            run(gh_cmd, cwd=repo)
            log(f"GitHub release {version} asset re-uploaded (--clobber, bump=none re-release)")
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
                if force_cf:
                    cf_cmd.append("--force")  # per-target: let cf_upload's own duplicate guard through
                run(cf_cmd, capture=False)
                log("CurseForge upload done")
            else:
                log(f"CurseForge: {cf_note} — skip")

        if mod.get("modrinth"):
            mr = mod["modrinth"]
            mr_action, mr_note = plan["Modrinth"]
            if mr_action == "publish":
                # Pass the normalized version + the format; modrinth_upload re-applies the same shared
                # helper (idempotent), so the uploaded version_number matches the dedup probe exactly.
                mr_fmt = mr.get("version_format", "strip-v")
                run(["python3", os.path.join(SCRIPT_DIR, "modrinth_upload.py"),
                     "--project", mr["project"], "--file", jar,
                     "--version", normalize_modrinth_version(version, mr_fmt),
                     "--version-format", mr_fmt,
                     "--game-version", mr.get("game_version", "1.7.10"),
                     "--release-type", "release", "--changelog-file", notes], capture=False)
                log("Modrinth upload done")
            else:
                log(f"Modrinth: {mr_note} — skip")
    except Exception:
        # FIX 1: a publish leg threw (CF/Modrinth/GitHub). Roll back a NEWLY-created tag so the next
        # run re-derives the SAME version and RETRIES — publish_plan's idempotency probes then skip
        # the legs that already went through and only re-attempt the one that failed. A pre-existing
        # tag (re-run / bump=none) is left intact.
        if tag_created:
            git(repo, "tag", "-d", version, check=False)
            log(f"rolled back tag {version} (a publish leg failed — re-run retries the missing leg)")
        rl.finish("ERROR", "a publish leg raised — tag rolled back if newly created")
        raise
    finally:
        os.unlink(notes)
    rl.finish("UPLOADED", f"published {version}: {plan_summary}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Bulk-release the pending mods.")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--only", help="release only this mod name")
    ap.add_argument("--execute", action="store_true", help="actually tag/push/publish (default: dry-run)")
    ap.add_argument("--verify", action="store_true",
                    help="LOCAL BUILD/TEST/AUDIT ONLY: run stage1 + build + boot-verify per mod, log "
                         "each gate to release-log.md (mode=VERIFY), then STOP. NEVER tags/pushes/"
                         "uploads. Wins over --execute if both are passed (no upload can happen).")
    ap.add_argument("--skip-build", action="store_true", help="trust an existing build")
    ap.add_argument("--skip-boot", action="store_true",
                    help="bypass the boot-verify gate (mixin apply-time check). Default: gate ON "
                         "for any mod with a boot_test manifest block")
    ap.add_argument("--changelog-file", help="curated release notes (markdown); only with --only")
    # Force is PER-TARGET (no bare --force blast-all) so 'retry the ONE leg that failed' can't
    # accidentally duplicate the others. GitHub is never forced (gh refuses a duplicate tag; a
    # bump=none re-release clobbers its asset automatically).
    ap.add_argument("--force-cf", action="store_true",
                    help="upload to CurseForge even if it already has this version (DUPLICATE) OR "
                         "can't be verified (no CF_API_KEY read key); default is to SKIP for safety")
    ap.add_argument("--force-modrinth", action="store_true",
                    help="upload to Modrinth even if it already has this version (duplicate attempt; "
                         "Modrinth rejects true dupes itself); default is to SKIP an existing version")
    args = ap.parse_args()

    mods = json.load(open(args.manifest))["mods"]
    # Skip pack-delivery-only entries (no build config) — those exist purely to drive pack_sync
    # (e.g. a fork already released upstream-by-me, tracked only for the pack bundle version).
    mods = [m for m in mods if m.get("build")]
    if args.only:
        mods = [m for m in mods if m["name"] == args.only]
        if not mods:
            sys.exit(f"no releasable mod named {args.only!r} in manifest (pack-only entries are skipped)")
        # --only names a mod explicitly -> honor even a no_release blacklist (force path if CF ever
        # approves a newer file), but warn loudly so it's a deliberate override.
        for m in mods:
            if m.get("no_release"):
                print(f"⚠ {m['name']}: no_release set ({m['no_release']}) but named via --only — "
                      f"proceeding as an explicit override.")
    else:
        # Blacklist: mods prohibited from auto-release (e.g. FileDirector — CF rejects new forks per
        # BUG-023; the pack pins the grandfathered Approved file). Still tracked in the manifest for
        # pack_sync delivery, but never swept into a bulk release. Force one with --only if needed.
        blacklisted = [m for m in mods if m.get("no_release")]
        for m in blacklisted:
            print(f"  [BLACKLIST] {m['name']}: no_release — {m['no_release']} (skipped; --only to force)")
        mods = [m for m in mods if not m.get("no_release")]
    override = None
    if args.changelog_file:
        if not args.only:
            sys.exit("--changelog-file requires --only (one curated text for one mod)")
        with open(args.changelog_file, encoding="utf-8") as f:
            override = f.read().strip()
        if not override:
            sys.exit(f"--changelog-file {args.changelog_file} is empty")

    cf_api_key = E.cf_api_key()  # read key (never printed) — for the CF existence probe

    # --verify: local build/test/audit ONLY. Dispatched BEFORE Stage 1/2 and returns here, so
    # release_one() (the only path that tags/pushes/uploads) is NEVER reached. --verify wins over
    # --execute by construction — there is no code path from this branch to a publish.
    if args.verify:
        if args.execute:
            print("note: --verify wins over --execute — running a LOCAL AUDIT ONLY, no upload.\n")
        print(f"===== release_mods [VERIFY] — {len(mods)} mod(s) (local audit; NEVER uploads) =====\n")
        allok = True
        for m in mods:
            try:
                if not verify_one(m, args.skip_build, args.skip_boot):
                    allok = False
            except Exception as e:
                print(f"  [{m['name']}] ERROR: {e}")
                allok = False
            print()
        print("===== VERIFY complete (release-log.md updated) — NOTHING was tagged/pushed/uploaded =====")
        sys.exit(0 if allok else 1)

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
                           force_cf=args.force_cf, force_modrinth=args.force_modrinth,
                           cf_api_key=cf_api_key, skip_boot=args.skip_boot):
                done += 1
        except Exception as e:
            print(f"  [{m['name']}] ERROR: {e}")
        print()
    print(f"===== {done}/{len(ready)} {'released' if args.execute else 'ready'} =====")
    if done < len(ready) or len(ready) < len(mods):
        sys.exit(1)  # surface partial failure with a non-zero exit code


if __name__ == "__main__":
    main()
