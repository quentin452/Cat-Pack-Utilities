#!/usr/bin/env python3
"""
changelog_from_git.py — DERIVE each mod's pending changelog from its git commits since the last
release tag, so a changelog entry can never be stale or missing: the commit IS the source of truth.
Same principle as changelog_from_bundles.py (pack changelog from the bundle git-diff), for mods.

  changelog_from_git.py                  # per owned mod (is_mod): commits since its last release tag
  changelog_from_git.py --repo OptimizationsAndTweaks
  changelog_from_git.py --all            # every owned repo (is_fork), not just is_mod
  changelog_from_git.py --raw            # include chore/test/docs/etc (dropped by default)
  changelog_from_git.py --markdown       # emit release-notes-style sections (curate at release)

Model: write good commit messages (English, conventional feat/fix/perf/security) and the changelog
falls out of git. This report is the COMPLETE pending set; curate the wording into the PUBLISHED
changelog at release time. Supersedes the manual per-mod tracking + the reactive track_audit check.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPOS = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory/repos.json")
# noise types dropped from the player-facing view by default
SKIP = re.compile(r"^(chore|tests?|docs?|style|ci|build|refactor|wip|merge|bump)\b|\bwip\b", re.I)
CO = re.compile(r"^Co-Authored-By", re.I)


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=25)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def last_release_tag(repo):
    """Most recent tag reachable from HEAD (the last release). None if the mod was never tagged."""
    return git(repo, "describe", "--tags", "--abbrev=0") or None


def pending(repo, tag):
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out = git(repo, "log", rng, "--format=%s")
    return [c for c in (out or "").splitlines() if c.strip() and not CO.match(c)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--raw", action="store_true", help="include chore/test/docs/etc")
    ap.add_argument("--markdown", action="store_true", help="emit release-notes-style bullets")
    args = ap.parse_args()

    if not os.path.exists(REPOS):
        sys.exit("repos.json not found (run scan_repos.py)")
    repos = json.load(open(REPOS)).get("repos", [])
    if args.repo:
        targets = [r for r in repos if r.get("name") == args.repo]
    elif args.all:
        targets = [r for r in repos if r.get("is_fork") or r.get("is_mod")]
    else:
        targets = [r for r in repos if r.get("is_mod")]

    any_pending = False
    for r in targets:
        repo = os.path.expanduser(r.get("path", ""))
        if not os.path.isdir(repo):
            continue
        tag = last_release_tag(repo)
        commits = pending(repo, tag)
        if not commits:
            continue
        any_pending = True
        kept = commits if args.raw else [c for c in commits if not SKIP.match(c)]
        since = tag or "REPO START (never tagged)"
        if args.markdown:
            print(f"\n## Mod {r['name']} — pending since {since}")
            for c in kept:
                print(f"* {c}")
        else:
            print(f"=== {r['name']}  (since {since}; {len(commits)} commit(s)) ===")
            for c in kept:
                print(f"  - {c}")
            drop = len(commits) - len(kept)
            if drop:
                print(f"  ({drop} chore/test/docs/etc hidden; --raw to show)")
    if not any_pending:
        print("No unreleased commits across the targeted mods — changelog is up to date with git.")


if __name__ == "__main__":
    main()
