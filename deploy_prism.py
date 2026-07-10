#!/usr/bin/env python3
"""deploy_prism.py — keep PrismLauncher test instances in sync with freshly built personal-mod jars.

The orchestration gap this closes: when Claude rebuilds a personal mod jar (matoulib/OaT/MatouMap…),
the `<mod>-test.jar` deployed in a Prism instance must be refreshed too, else the user relaunches the
native instance and tests a STALE binary (lived 2026-07-10: MatouMap rebuilt but never redeployed → the
instance kept the old rustmap-test.jar). This is the tooled guard for the CLAUDE.md proactive-deploy rule.

For every Prism instance under PRISM_ROOT, for each `*-test.jar` (the Claude-deployed personal jars,
told apart from third-party mods by the `-test.jar` suffix), it finds the matching local repo by
BUILD-ARTIFACT prefix — so the RustMap→MatouMap rebrand is handled by the JAR name, not the dir name —
takes the freshest reobf jar in that repo's build/libs, and flags the instance jar as STALE when the repo
has a newer build with different content. `--apply` copies the fresh jar over (same filename).

Read-only by default (dry-run). `--apply` mutates local test instances only (never outward). Paths come
from packenv (PRISM_ROOT, REPOS_JSON) — no hardcoded paths.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys

import packenv as E

# Non-deployable build variants. `-dev-preshadow.jar` is the pre-shadow intermediate (shadowImplementation
# deps like matou-exec NOT yet merged) — deploying it yields NoClassDefFoundError at runtime, so it MUST be
# excluded even though it doesn't end in "-dev.jar" (it ends in "-dev-preshadow.jar").
DEV_SUFFIXES = ("-dev.jar", "-sources.jar", "-preshadow.jar")
TEST_SUFFIX = "-test.jar"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_mod_repos():
    """is_mod repos from repos.json that have a build/libs dir → [(repo_name, build_libs_path)]."""
    with open(E.REPOS_JSON) as f:
        data = json.load(f)
    repos = data["repos"] if isinstance(data, dict) else data
    out = []
    for r in repos:
        if not r.get("is_mod"):
            continue
        libs = os.path.join(os.path.expanduser(r["path"]), "build", "libs")
        if os.path.isdir(libs):
            out.append((r["name"], libs))
    return out


def freshest_jar(libs, prefix):
    """Newest non-dev/-sources `<prefix>-*.jar` in libs, or None."""
    cands = [
        j for j in glob.glob(os.path.join(libs, prefix + "-*.jar"))
        if not os.path.basename(j).endswith(DEV_SUFFIXES)
    ]
    return max(cands, key=os.path.getmtime) if cands else None


def find_source_jars(mod_repos, prefix):
    """Repos whose build/libs hold a `<prefix>-*.jar` → [(repo_name, jar)] (>1 = ambiguous)."""
    hits = []
    for name, libs in mod_repos:
        j = freshest_jar(libs, prefix)
        if j:
            hits.append((name, j))
    return hits


def prism_instances():
    """Prism instances that have a minecraft/mods dir → [(instance_name, mods_dir)]."""
    root = E.PRISM_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        mods = os.path.join(root, name, "minecraft", "mods")
        if os.path.isdir(mods):
            out.append((name, mods))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="copy fresh jars over stale ones (default: dry-run report)")
    ap.add_argument("--instance", help="limit to one Prism instance by name")
    args = ap.parse_args()

    mod_repos = load_mod_repos()
    instances = prism_instances()
    if args.instance:
        instances = [(n, m) for n, m in instances if n == args.instance]
    if not instances:
        print("no Prism instances with minecraft/mods under", E.PRISM_ROOT)
        return 0

    stale = ok = orphan = ambig = diverged = applied = 0
    for inst_name, mods in instances:
        rows = []
        for jar in sorted(glob.glob(os.path.join(mods, "*" + TEST_SUFFIX))):
            fn = os.path.basename(jar)
            prefix = fn[:-len(TEST_SUFFIX)]
            hits = find_source_jars(mod_repos, prefix)
            if not hits:
                rows.append(("ORPHAN", fn, "no repo build/libs has %s-*.jar" % prefix))
                orphan += 1
                continue
            if len(hits) > 1:
                rows.append(("AMBIG", fn, "matches repos: " + ", ".join(n for n, _ in hits)))
                ambig += 1
                continue
            repo_name, src = hits[0]
            if sha256(jar) == sha256(src):
                rows.append(("ok", fn, "%s — up to date" % repo_name))
                ok += 1
            elif os.path.getmtime(src) > os.path.getmtime(jar):
                verb = "APPLIED" if args.apply else "run --apply"
                rows.append(("STALE", fn, "%s newer: %s → %s" % (repo_name, os.path.basename(src), verb)))
                stale += 1
                if args.apply:
                    shutil.copy2(src, jar)
                    applied += 1
            else:
                rows.append(("DIVERGED", fn,
                             "%s differs but instance jar is NEWER — not overwriting" % repo_name))
                diverged += 1
        print("\n== %s ==" % inst_name)
        if not rows:
            print("  (no *%s personal jars)" % TEST_SUFFIX)
        for tag, fn, msg in rows:
            print("  [%-8s] %-34s %s" % (tag, fn, msg))

    tail = "  (dry-run — use --apply)" if stale and not args.apply else ""
    print("\nsummary: %d stale, %d ok, %d diverged, %d orphan, %d ambiguous%s" % (
        stale, ok, diverged, orphan, ambig, tail))
    if args.apply and applied:
        print("applied: %d jar(s) refreshed" % applied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
