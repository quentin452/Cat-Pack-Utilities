#!/usr/bin/env python3
"""
security_audit.py — pull GitHub security + quality alerts across the OWNED MODS and surface them.
Covers Dependabot (dependency vulns, e.g. the OaT Rust bytes/lru advisories) + code scanning
(CodeQL / quality). Needs `gh` authenticated with security-events access.

  security_audit.py                 # owned mods (repos.json is_mod), OPEN alerts
  security_audit.py --all           # every owned repo, not just is_mod
  security_audit.py --repo OptimizationsAndTweaks
Exit 1 if any open alert (so it can gate a release / a session wrap).

Fixes are per-ecosystem, NOT here: Rust = cargo update -p <crate> (bump past the semver cap in
Cargo.toml if needed); Java/gradle = bump the dep; code scanning = triage/dismiss in the code.
"""
import argparse
import json
import os
import subprocess
import sys

REPOS = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory/repos.json")


def gh_json(path):
    """gh api -> parsed JSON, or None if the endpoint 404s (feature off / no access)."""
    try:
        r = subprocess.run(["gh", "api", path, "--paginate"], capture_output=True, text=True, timeout=45)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "[]")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every owned repo, not just is_mod")
    ap.add_argument("--repo", default=None, help="one repo by name")
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
    if not targets:
        sys.exit("no target repos")

    total, scanned, no_access = 0, 0, 0
    for r in targets:
        slug = r.get("slug")
        if not slug:
            continue
        scanned += 1
        dep = gh_json(f"repos/{slug}/dependabot/alerts?state=open&per_page=100")
        scan = gh_json(f"repos/{slug}/code-scanning/alerts?state=open&per_page=100")
        if dep is None and scan is None:
            no_access += 1
            continue
        dep, scan = dep or [], scan or []
        if not dep and not scan:
            continue
        print(f"=== {r['name']}  ({slug}) ===")
        for a in dep:
            adv = a.get("security_advisory", {})
            pkg = a.get("dependency", {}).get("package", {}).get("name", "?")
            loc = a.get("dependency", {}).get("manifest_path", "")
            print(f"  [dependabot/{adv.get('severity','?'):8}] {pkg}: {adv.get('summary','')[:64]}  ({loc})")
            total += 1
        for a in scan:
            rule = a.get("rule", {})
            print(f"  [codeql/{rule.get('security_severity_level') or rule.get('severity','?'):8}] "
                  f"{rule.get('description','')[:70]}")
            total += 1

    print(f"\nscanned {scanned} mod(s); {no_access} without alert access (Dependabot off / private).")
    if total:
        print(f"{total} OPEN alert(s) — fix at the ecosystem level (cargo/gradle bump) or triage code scanning.")
        sys.exit(1)
    print("OK: no open security/quality alerts.")


if __name__ == "__main__":
    main()
