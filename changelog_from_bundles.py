#!/usr/bin/env python3
"""Generate the pack changelog's added/updated/removed sections from the GIT DIFF of the mod-director
bundles between two refs — the ground truth of what a pack release actually changed. Replaces the
hand-maintained changelog-pending drift (entries that were planned-but-never-applied or
already-published slipped into a release; caught 2026-07-07: a bogus 'Baubles added 2.2.21').

Respects the FileDirector convention (the trap): each bundle entry may carry metadata.side = CLIENT
(client-only) — else it is BOTH (client + server). A CLIENT-only mod change is annotated so it is not
misread as affecting the server. url.bundle nests entries under arrays; curse identifies mods by
addonId, github urls by owner/repo.

Usage:
  python3 changelog_from_bundles.py <old_ref> [new_ref]     # default new_ref = HEAD
  python3 changelog_from_bundles.py 0dffdb8e HEAD
Run inside the pack repo, or pass --pack <dir>.

NOTE 2 pièges (audit 2026-07-07, user):
  1. BASELINE = la dernière version PUBLIÉE (tag/CHANGELOG), PAS le dernier commit. Ex: V1.1.7=6109ff44,
     pas 0dffdb8e — sinon on rate les changements unpublished (HEGM removal). Passer le bon <old_ref>.
  2. Un re-upload SAME-URL (eotg V1.5.6 re-tag, contenu changé, URL identique) = INVISIBLE ici (diff par
     URL/version string). Ces mods = à ajouter À LA MAIN (le tool ne voit pas le contenu).
"""
import json
import os
import re
import subprocess
import sys

REPO_DEFAULT = os.path.expanduser("~/Documents/GitHub/privates-minecraft-modpack")
MODPACK_SUB = "MODPACKS/Biggess Pack Cat Edition"  # git paths are relative to the REPO ROOT
BUNDLES = [f"{MODPACK_SUB}/src/common/config/mod-director/curse.bundle.json",
           f"{MODPACK_SUB}/src/common/config/mod-director/url.bundle.json",
           f"{MODPACK_SUB}/src/common/config/mod-director/modrinth.bundle.json"]
# FileDirector (and any CF-pack-manifest mod) is delivered OUTSIDE the mod-director bundles — via the
# CurseForge pack manifest (client) + a direct server jar. Diff the manifest too, else FileDirector's
# own version bumps are invisible in the changelog (the trap: it's not in any bundle).
CLIENT_MANIFEST = f"{MODPACK_SUB}/src/client/manifest.json"


def git_show(repo, ref, path):
    r = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def side_of(e):
    return ((e.get("metadata") or {}).get("side") or "BOTH").upper()


def _walk(x):
    if isinstance(x, dict):
        if "addonId" in x or isinstance(x.get("url"), str) or "projectId" in x or "versionId" in x:
            yield x
        for v in x.values():
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)


def _github_stem(url):
    m = re.search(r"github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/(.+)$", url)
    if m:
        return f"gh:{m.group(1)}", m.group(2), m.group(3)  # key, version(tag), filename
    # non-github url: key by dirname, version by the last path segment before filename
    m = re.match(r"(.*)/([^/]+)$", url)
    return (f"url:{m.group(1)}" if m else url), "?", (m.group(2) if m else url)


def parse(text):
    """{key: {'label','version','side'}} — key stable across versions (addonId / gh owner-repo)."""
    if not text:
        return {}
    d = json.loads(text)
    out = {}
    for e in _walk(d):
        side = side_of(e)
        if "addonId" in e:
            key = f"cf:{e['addonId']}"
            out[key] = {"label": e.get("fileName") or f"CF {e['addonId']}",
                        "version": str(e.get("fileId")), "side": side}
        elif "projectId" in e or "versionId" in e:  # modrinth
            key = f"mr:{e.get('projectId')}"
            out[key] = {"label": e.get("fileName") or f"MR {e.get('projectId')}",
                        "version": str(e.get("versionId")), "side": side}
        elif isinstance(e.get("url"), str):
            key, ver, fn = _github_stem(e["url"])
            out[key] = {"label": fn, "version": ver, "side": side}
    return out


def parse_manifest(text):
    """CurseForge pack manifest (src/client/manifest.json): files[] = {projectID, fileID}. Key by
    projectID; label = 'CF <pid>' (no filename here). FileDirector lives here, not in a bundle."""
    if not text:
        return {}
    d = json.loads(text)
    out = {}
    known = {1359998: "FileDirector (mod-director bootstrapper)"}
    for e in d.get("files", []):
        pid = e.get("projectID")
        if pid is None:
            continue
        out[f"cf:{pid}"] = {"label": known.get(pid, f"CF project {pid}"),
                            "version": str(e.get("fileID")), "side": "BOTH"}
    return out


def sidetag(side):
    return "" if side == "BOTH" else f" ({side.lower()})"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pack = REPO_DEFAULT
    if "--pack" in sys.argv:
        pack = os.path.expanduser(sys.argv[sys.argv.index("--pack") + 1])
    if not args:
        sys.exit("usage: changelog_from_bundles.py <old_ref> [new_ref]")
    old_ref, new_ref = args[0], (args[1] if len(args) > 1 else "HEAD")

    added, updated, removed = [], [], []
    for path in BUNDLES + [CLIENT_MANIFEST]:
        pfn = parse_manifest if path == CLIENT_MANIFEST else parse
        old = pfn(git_show(pack, old_ref, path))
        new = pfn(git_show(pack, new_ref, path))
        for k, v in new.items():
            if k not in old:
                added.append((v["label"], v["side"]))
            elif old[k]["version"] != v["version"]:
                updated.append((old[k]["label"], v["label"], v["side"],
                                old[k]["version"], v["version"]))
        for k, v in old.items():
            if k not in new:
                removed.append((v["label"], v["side"]))

    print(f"# pack changelog from bundle diff  {old_ref}..{new_ref}\n")
    if updated:
        print("**mods updated**")
        for oldl, newl, side, oldv, newv in sorted(updated):
            # when the label carries no version (e.g. CF manifest = a stable name), show the id delta
            body = f"{oldl} -> {newl}" if oldl != newl else f"{oldl} (fileID {oldv} -> {newv})"
            print(f"* {body}{sidetag(side)}")
        print()
    if added:
        print("**mods added**")
        for label, side in sorted(added):
            print(f"* {label}{sidetag(side)}")
        print()
    if removed:
        print("**mods deleted**")
        for label, side in sorted(removed):
            print(f"* {label}{sidetag(side)}")
        print()
    if not (added or updated or removed):
        print("(no bundle changes between these refs)")


if __name__ == "__main__":
    main()
