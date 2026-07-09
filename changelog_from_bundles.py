#!/usr/bin/env python3
"""Generate the pack changelog's added/updated/removed sections from the GIT DIFF of the mod-director
bundles between two refs — the ground truth of what a pack release actually changed. Replaces the
hand-maintained changelog-pending drift (entries that were planned-but-never-applied or
already-published slipped into a release; caught 2026-07-07: a bogus 'Baubles added 2.2.21').

Respects the FileDirector convention (the trap): each bundle entry may carry metadata.side = CLIENT
(client-only) — else it is BOTH (client + server). A CLIENT-only mod change is annotated so it is not
misread as affecting the server. url.bundle nests entries under arrays; curse identifies mods by
addonId, github urls by owner/repo.

Also derives CONFIG changes (src/common/config, with mod-director/ excluded — that subtree IS the
bundle diff above) for the SAME ref pair, so the whole pack changelog comes from one tool / one
baseline and the hand-maintained pending file can be retired. One concise line per changed config
file; a directory with many changed files is summarized to a single line; comment/blank-only edits
are filtered as noise. Config is on by default.

Usage:
  python3 changelog_from_bundles.py <old_ref> [new_ref]     # default new_ref = HEAD
  python3 changelog_from_bundles.py 0dffdb8e HEAD
  python3 changelog_from_bundles.py <old_ref> --no-config   # bundles/manifest only (legacy output)
  python3 changelog_from_bundles.py <old_ref> --config-only # config section only
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

import packenv as E

REPO_DEFAULT = E.PACK_REPO
MODPACK_SUB = os.path.relpath(E.PACK_DIR, E.PACK_REPO)  # git paths are relative to the REPO ROOT
BUNDLES = [os.path.relpath(E.CURSE_BUNDLE, E.PACK_REPO),
           os.path.relpath(E.URL_BUNDLE, E.PACK_REPO),
           E.MODRINTH_BUNDLE]
# FileDirector (and any CF-pack-manifest mod) is delivered OUTSIDE the mod-director bundles — via the
# CurseForge pack manifest (client) + a direct server jar. Diff the manifest too, else FileDirector's
# own version bumps are invisible in the changelog (the trap: it's not in any bundle).
CLIENT_MANIFEST = os.path.relpath(E.CLIENT_MANIFEST, E.PACK_REPO)

# --- config-change derivation (game config, NOT the mod-director bundles) -------------------------
# The shipped game config lives here. mod-director/ is a subdir of it but is already the bundle diff
# above, so it is excluded to avoid double-reporting the same mod add/update/remove.
CONFIG_SUB = os.path.relpath(E.CANONICAL_CONFIG, E.PACK_REPO)
CONFIG_PREFIX = CONFIG_SUB + "/"
CONFIG_EXCLUDE = ("mod-director/",)   # covered by the bundle/manifest diff
GROUP_THRESHOLD = 4                   # a config subdir with >= this many changed files -> one summary line
COMMENT_MARKERS = ("#", "//", ";")    # a changed line that is blank or starts with one of these = noise


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


def _git(repo, *args):
    # errors="replace": Minecraft configs are often latin-1 (e.g. § color codes = 0xa7), which is
    # invalid UTF-8 — a strict decode of the diff output would crash the whole changelog derivation.
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, encoding="utf-8",
                       errors="replace")
    return r.stdout if r.returncode == 0 else ""


def _rel_config(path):
    """Repo-relative path -> path under config/ (e.g. 'cofh/world/Ores.json'), or None if it is
    outside the config tree or in an excluded subdir (mod-director/)."""
    if not path.startswith(CONFIG_PREFIX):
        return None
    rel = path[len(CONFIG_PREFIX):]
    if any(rel.startswith(x) for x in CONFIG_EXCLUDE):
        return None
    return rel


def _diff_is_noise(repo, old_ref, new_ref, rel):
    """True if every changed line of this file is blank or a comment (# // ;). Cheap heuristic: it
    catches header/timestamp/comment churn but NOT reordering, JSON key shuffles, or value-equivalent
    reformatting — those still surface as real changes (favoring false-negative over hiding a change)."""
    out = _git(repo, "diff", "--ignore-cr-at-eol", "-U0", old_ref, new_ref, "--", CONFIG_PREFIX + rel)
    saw = False
    for line in out.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line and line[0] in "+-":
            saw = True
            body = line[1:].strip()
            if body and not body.startswith(COMMENT_MARKERS):
                return False          # a substantive changed line -> not noise
    return saw                        # all changed lines trivial (and there was at least one)


def config_changes(repo, old_ref, new_ref):
    """(changes, skipped) for src/common/config between the two refs. changes = list of dicts
    {rel, status(A/M/D), add, dele, binary}; skipped = count of comment/blank-only files filtered out.
    Renames are reported as delete+add (no -M) to keep parsing robust against spaces in the pack path."""
    status = {}
    for line in _git(repo, "diff", "--ignore-cr-at-eol", "--name-status", old_ref, new_ref, "--", CONFIG_SUB).splitlines():
        parts = line.split("\t")      # tab-separated: paths may contain spaces but never tabs
        if len(parts) < 2:
            continue
        rel = _rel_config(parts[-1])
        if rel is not None:
            status[rel] = parts[0][:1]
    counts = {}
    for line in _git(repo, "diff", "--ignore-cr-at-eol", "--numstat", old_ref, new_ref, "--", CONFIG_SUB).splitlines():
        parts = line.split("\t")      # '<added>\t<deleted>\t<path>'; '-' counts for binary files
        if len(parts) < 3:
            continue
        rel = _rel_config(parts[-1])
        if rel is not None:
            counts[rel] = (parts[0], parts[1])
    changes, skipped = [], 0
    for rel in sorted(status):
        st = status[rel]
        add, dele = counts.get(rel, ("0", "0"))
        binary = (add == "-" or dele == "-")
        if st == "M" and not binary and _diff_is_noise(repo, old_ref, new_ref, rel):
            skipped += 1
            continue
        changes.append({"rel": rel, "status": st, "add": add, "dele": dele, "binary": binary})
    return changes, skipped


def _file_line(c):
    st = c["status"]
    if c["binary"]:
        mag = "(binary)"
    elif st == "A":
        mag = f"(+{c['add']} lines)"
    elif st == "D":
        mag = f"(-{c['dele']} lines)"
    else:
        mag = f"(+{c['add']}/-{c['dele']} lines)"
    verb = {"A": "added", "D": "deleted", "M": "changed"}.get(st, "changed")
    return f"* config: {c['rel']} {verb} {mag}"


def _group_line(seg, items):
    adds = sum(1 for c in items if c["status"] == "A")
    dels = sum(1 for c in items if c["status"] == "D")
    ta = sum(int(c["add"]) for c in items if not c["binary"])
    td = sum(int(c["dele"]) for c in items if not c["binary"])
    extra = [x for x in (f"{adds} added" if adds else "", f"{dels} deleted" if dels else "") if x]
    tag = (", " + ", ".join(extra)) if extra else ""
    return f"* config: {seg}/ — {len(items)} files changed (+{ta}/-{td} lines{tag})"


def render_config(changes, skipped):
    """Print the **config changed** section: group a directory with many changed files into one line,
    else one line per file; append a noise-skip note. No output if nothing changed."""
    if not changes and not skipped:
        return
    print("**config changed**")
    groups, singles = {}, []
    for c in changes:
        seg = c["rel"].split("/", 1)[0] if "/" in c["rel"] else None  # None = top-level file
        if seg is None:
            singles.append(c)
        else:
            groups.setdefault(seg, []).append(c)
    lines = []
    for seg, items in groups.items():
        if len(items) >= GROUP_THRESHOLD:
            lines.append((seg + "/", _group_line(seg, items)))
        else:
            singles.extend(items)
    for c in singles:
        lines.append((c["rel"], _file_line(c)))
    for _, text in sorted(lines):
        print(text)
    if skipped:
        print(f"* _({skipped} config file(s) skipped: comment/whitespace-only changes)_")
    print()


def main():
    # positionals = argv minus --flags and the value consumed by --pack
    pack, args, skip_next = REPO_DEFAULT, [], False
    for i, a in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if a == "--pack":
            pack = os.path.expanduser(sys.argv[i + 2]) if i + 2 < len(sys.argv) else pack
            skip_next = True
        elif not a.startswith("--"):
            args.append(a)
    want_config = "--no-config" not in sys.argv
    config_only = "--config-only" in sys.argv
    if not args:
        sys.exit("usage: changelog_from_bundles.py <old_ref> [new_ref] "
                 "[--no-config|--config-only] [--pack <dir>]")
    old_ref, new_ref = args[0], (args[1] if len(args) > 1 else "HEAD")
    # accept git range syntax "old..new" as the single positional
    if ".." in old_ref and len(args) == 1:
        old_ref, _, new_ref = old_ref.partition("..")
        new_ref = new_ref.lstrip(".") or "HEAD"
    # fail LOUD on unresolvable refs: a silently-empty old side reads as "everything added"
    for ref in (old_ref, new_ref):
        if subprocess.run(["git", "-C", pack, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                          stdout=subprocess.DEVNULL).returncode != 0:
            sys.exit(f"error: ref {ref!r} does not resolve in {pack} — refusing to diff "
                     "(a bad ref would report the whole bundle as added)")

    added, updated, removed = [], [], []
    if not config_only:
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

    cfg_changes, cfg_skipped = (config_changes(pack, old_ref, new_ref) if want_config else ([], 0))

    scope = "config" if config_only else ("bundles" if not want_config else "bundles + config")
    print(f"# pack changelog ({scope})  {old_ref}..{new_ref}\n")
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
    render_config(cfg_changes, cfg_skipped)
    if not (added or updated or removed or cfg_changes or cfg_skipped):
        print("(no bundle or config changes between these refs)")


if __name__ == "__main__":
    main()
