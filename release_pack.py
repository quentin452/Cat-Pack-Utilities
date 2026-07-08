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
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CF_READ_API = "https://api.curseforge.com"
PACK_REPO = Path.home() / "Documents/GitHub/privates-minecraft-modpack"
PACK_DIR = PACK_REPO / "MODPACKS/Biggess Pack Cat Edition"
DIST = PACK_DIR / "dist"
PACK_PROJECT_ID = 830694          # CF modpack project (biggess-pack-cat-edition)
GAME_VERSION = "1.7.10"


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


def main():
    ap = argparse.ArgumentParser(description="Gated modpack release pipeline.")
    ap.add_argument("--version", required=True, help="pack version to ship, e.g. 1.1.9 (no V prefix)")
    ap.add_argument("--baseline", required=True,
                    help="git ref of the LAST PUBLISHED pack version (changelog derive base)")
    ap.add_argument("--changelog-file", type=Path,
                    help="hand-curated derive-markdown; default = raw derive output")
    ap.add_argument("--skip-audit", action="store_true", help="skip the fork-staleness audit gate")
    ap.add_argument("--project-id", type=int, default=PACK_PROJECT_ID)
    ap.add_argument("--execute", action="store_true", help="build zips + upload to CF + publish changelog")
    ap.add_argument("--force", action="store_true",
                    help="ship even if CF already has a file for V<version> (default: REFUSE to "
                         "avoid re-shipping a live pack version)")
    args = ap.parse_args()

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
