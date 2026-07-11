#!/usr/bin/env python3
"""One-shot generator for a mod's `_legacy_hashes.json` (hub doc 60 §3).

The DefaultsExtractor mechanism migrates the installed base of an extracted-defaults dir by proving a file on disk is
PRISTINE (a default we shipped at some point) rather than a user edit. The proof set = the sha256 of the raw bytes of
EVERY historical git blob under a mod's `assets/<ns>/**`, keyed by asset relpath. A superset of the versions actually
released is safe (worst case = refreshing an intermediate default we authored, never clobbering user content).

Walks `git log --all` of the target repo, collects every blob object ever present under the asset root, hashes its raw
bytes, and writes the aggregate to `<repo>/src/main/resources/assets/<ns>/_legacy_hashes.json`. Frozen at ship time;
later versions are covered by the on-disk manifest.

Usage:
    python3 gen_legacy_hashes.py --repo gigafauna [--check]

`--check` re-generates in memory and exits non-zero if the committed file is stale (CI/preflight guard).
The repo checkout dir is resolved by name in memory/repos.json (E.REPOS_JSON, `path` field) — never hardcoded.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

import packenv as E

# repo name (repos.json) -> the assets namespace whose history is hashed.
REPO_NAMESPACES = {
    "gigafauna": "gigafauna",
}


def resolve_repo_dir(repo_name):
    with open(E.REPOS_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    for entry in data.get("repos", []):
        if entry.get("name") == repo_name:
            return os.path.expanduser(entry["path"])
    raise SystemExit(f"repo '{repo_name}' not found in {E.REPOS_JSON} (repos[].name).")


def git(repo_dir, *args):
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        check=True, capture_output=True, text=False,
    ).stdout


def git_text(repo_dir, *args):
    return git(repo_dir, *args).decode("utf-8", "replace")


def build(repo_dir, namespace):
    """relpath (under assets/<ns>/) -> sorted list of sha256 of every historical raw blob."""
    asset_root = f"src/main/resources/assets/{namespace}/"
    # Every commit across every ref (branches, tags, reflog-reachable via --all).
    commits = git_text(repo_dir, "log", "--all", "--format=%H").split()
    # relpath -> set of git blob object ids (dedup by object id first = cheap, then hash once per unique blob).
    blobs_by_rel = {}
    seen_trees = set()
    for c in commits:
        # `git ls-tree -r <commit> -- <asset_root>` lists <mode> <type> <objectid>\t<path>
        try:
            out = git_text(repo_dir, "ls-tree", "-r", c, "--", asset_root)
        except subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            if not line.strip():
                continue
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            obj = parts[2]
            if not path.startswith(asset_root):
                continue
            rel = path[len(asset_root):]
            # skip the generated index + the legacy file itself
            if rel in ("_legacy_hashes.json",) or rel.endswith("_index.txt"):
                continue
            blobs_by_rel.setdefault(rel, set()).add(obj)

    # Hash each unique blob object once (raw bytes).
    result = {}
    for rel, objs in blobs_by_rel.items():
        hashes = set()
        for obj in objs:
            raw = git(repo_dir, "cat-file", "-p", obj)
            hashes.add(hashlib.sha256(raw).hexdigest())
        result[rel] = sorted(hashes)
    return dict(sorted(result.items()))


def render(files, namespace):
    return json.dumps(
        {"format": 1, "root": f"assets/{namespace}/", "files": files},
        indent=1, ensure_ascii=False,
    ) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="gigafauna", choices=sorted(REPO_NAMESPACES))
    ap.add_argument("--check", action="store_true", help="exit 2 if the committed file is stale")
    args = ap.parse_args()

    namespace = REPO_NAMESPACES[args.repo]
    repo_dir = resolve_repo_dir(args.repo)
    out_path = os.path.join(
        repo_dir, "src", "main", "resources", "assets", namespace, "_legacy_hashes.json"
    )

    files = build(repo_dir, namespace)
    text = render(files, namespace)

    if args.check:
        current = ""
        if os.path.isfile(out_path):
            with open(out_path, encoding="utf-8") as fh:
                current = fh.read()
        if current != text:
            print(f"STALE: {out_path} is out of date — run gen_legacy_hashes.py --repo {args.repo}", file=sys.stderr)
            sys.exit(2)
        print(f"OK: {out_path} up to date ({len(files)} relpaths).")
        return

    # LF-normalised write (hub EOL rule).
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    total = sum(len(v) for v in files.values())
    print(f"Wrote {out_path}: {len(files)} relpaths, {total} historical hashes.")


if __name__ == "__main__":
    main()
