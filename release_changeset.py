#!/usr/bin/env python3
r"""
release_changeset.py — pre-release derivation of "what needs shipping in the next pack release",
by UNIONING two independent staleness signals so neither blind spot lets an update fall through.

Why a union (the real miss, 2026-07-09):
  Any SINGLE signal is stale-blind on its own axis:
    * bundle_drift.py compares the TEST instance <-> bundles. It catches a swapped/newer jar sitting
      in the instance, but is BLIND when instance == bundle yet the REPO is ahead of both.
    * pipeline_status.py does a repo->bundle held check, but only walks release-manifest.json mods.
  BoP fell through BOTH: instance == bundle == BiomesOPlenty-BOP-1.7.10-2.1.0.2309.jar, yet the BoP
  repo HEAD is 3 commits past tag BOP-1.7.10-2.1.0.2309 (incl. a deterministic-worldgen fix) AND BoP
  is not in the manifest. A real unshipped update, invisible to every existing tool.

What this derives, for EVERY bundle mod (curse + url) mappable to a local repo via memory/repos.json:
  (A) INSTANCE_DRIFT  — from bundle_drift's comparison (version drift / undeclared additions /
      removals): the jar you test on differs from what the bundles declare players get.
  (B) REPO_AHEAD      — the owned repo has commits past the shipped tag/version: work that is
      built/committed but not yet reflected in the bundle a player installs.
  Reported as one union table: INSTANCE_DRIFT / REPO_AHEAD / BOTH / clean, per mod.

Repo mapping (surfaced in the `note` column so a wrong map is eyeballable):
  * url.bundle  -> the GitHub URL carries owner/repo AND the shipped tag in /releases/download/<tag>/;
                   mapped by exact repos.json slug match (reliable). Tag verified to exist in the repo.
  * curse.bundle -> the fileName carries only a version, no repo; mapped fuzzily by base-name to an
                    owned repo (exact base-key equality, conservative) and the shipped tag is a
                    best-effort search for a tag containing the fileName's mod-version token. If the
                    shipped tag cannot be found, the row says so (never silently treated as clean).

COVERAGE / LIMITATION (honest — do not read a clean run as "nothing to ship"):
  This catches (a) instance-drift and (b) an owned fork being AHEAD of the shipped tag. It does NOT
  catch an UPSTREAM-version bump where the user's fork is BEHIND upstream and we want to move up to a
  newer upstream release (e.g. lwjgl3ify 2.1.15 -> 3.0.26 — the fork is behind upstream, not ahead).
  That axis lives in mod_update_checker.py / a manual decision. A clean release_changeset means
  "no instance drift and no owned-repo-ahead", NOT "no pending upstream bumps".

Usage:
  release_changeset.py                 # union report for instance TEST vs canonical bundles
  release_changeset.py --instance server
  release_changeset.py --json          # machine-readable
  release_changeset.py --only-flagged  # hide clean rows
Exit: non-zero if ANY non-clean row (so it can gate a release); 0 only if everything is clean.
"""

import argparse
import os
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle_drift  # reuse: env/paths, bundle readers, instance scan, classify, base_key
import changelog_from_git  # reuse: commit_type (the shared conventional-commit classifier)
import packenv as E  # shared path/id/secret source of truth

REPOS_JSON = E.REPOS_JSON
# MC-version / packaging tokens that are NOT a mod's own version (skip when guessing a curse version).
_MC_TOKENS = {"1.7.10", "1.7.2", "mc1.7.10", "mc1.7.2", "1.12.2", "1.11.1", "1.11", "1.10.2"}
_VER_TOK = re.compile(r"^v?\d")


def git(repo, *args):
    """Run git in repo; return stripped stdout on success, else None (mirrors the other tools)."""
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=25)
    return p.stdout.strip() if p.returncode == 0 else None


# --- repos.json index --------------------------------------------------------
def load_repos():
    import json
    if not os.path.exists(REPOS_JSON):
        sys.exit("repos.json not found (run scan_repos.py)")
    repos = json.load(open(REPOS_JSON, encoding="utf-8")).get("repos", [])
    by_slug = {}
    by_namekey = {}
    for r in repos:
        p = os.path.expanduser(r.get("path", ""))
        r["_abspath"] = p
        r["_is_git"] = os.path.isdir(os.path.join(p, ".git"))
        if r.get("slug"):
            by_slug[r["slug"].lower()] = r
        # curse-fuzzy targets are name-keyed. Restrict to actual mods/forks so a curse fileName does
        # not collide with an unrelated personal repo (e.g. 'Notes-1.7.10-1.1.1.jar' fuzzy-matching a
        # scratch 'Notes' repo that is neither is_mod nor is_fork — a real false map, dropped here).
        if r.get("is_mod") or r.get("is_fork"):
            nk = re.sub(r"[^a-z0-9]", "", (r.get("name") or "").lower())
            if nk:
                by_namekey.setdefault(nk, r)
    return repos, by_slug, by_namekey


# --- bundle entries ----------------------------------------------------------
def bundle_entries(canonical_config):
    """Yield {source, fileName, tag, url, repo_slug, version_hint} for each curse + url bundle entry.
    url entries carry a real GitHub owner/repo + release tag; curse entries carry only a fileName."""
    md = os.path.join(canonical_config, "mod-director")
    out = []
    for e in bundle_drift.read_bundle(md, "curse.bundle.json", "curse"):
        fn = e.get("fileName")
        if not fn:
            continue
        out.append({"source": "curse", "fileName": fn, "tag": None, "url": None,
                    "repo_slug": None, "version_hint": _curse_version(fn)})
    for e in bundle_drift.read_bundle(md, "url.bundle.json", "url"):
        url = e.get("url")
        if not url:
            continue
        fn = e.get("fileName") or os.path.basename(urllib.parse.urlparse(url).path)
        slug = tag = None
        m = re.search(r"github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/", url)
        if m:
            slug = f"{m.group(1)}/{m.group(2)}"
            tag = urllib.parse.unquote(m.group(3))  # tags can be %-encoded (e.g. β -> %CE%B2)
        out.append({"source": "url", "fileName": fn, "tag": tag, "url": url,
                    "repo_slug": slug, "version_hint": None})
    return out


def _curse_version(fname):
    """Best-effort: the mod's own version token from a curse fileName, skipping MC-version noise.
    e.g. 'wizardry-1.7.10-1.1.6fork7.jar' -> '1.1.6fork7' (not the '1.7.10' MC token)."""
    n = fname.lower()
    for ext in (".jar", ".zip"):
        if n.endswith(ext):
            n = n[: -len(ext)]
            break
    toks = re.split(r"[\s\-_+()\[\]]+", n)
    cands = [t for t in toks if t and _VER_TOK.match(t) and t not in _MC_TOKENS]
    return cands[0] if cands else None


# --- repo mapping ------------------------------------------------------------
def map_repo(entry, by_slug, by_namekey):
    """Return (repo|None, how). url -> exact slug; curse -> exact base-key of the mod name (fuzzy)."""
    if entry["source"] == "url" and entry["repo_slug"]:
        r = by_slug.get(entry["repo_slug"].lower())
        return (r, "url-slug") if r else (None, "url-slug (not an owned/cloned repo)")
    if entry["source"] == "curse":
        key = bundle_drift.base_key(entry["fileName"])
        if len(key) >= 4 and key in by_namekey:
            return by_namekey[key], "curse-fuzzy"
        return None, "curse-fuzzy (no owned repo)"
    return None, "unmapped"


# --- shipped tag resolution --------------------------------------------------
def resolve_tag(entry, repo):
    """(tag|None, note). url: tag from the URL, verified to exist. curse: search a tag containing the
    version token. None + explanatory note when the shipped tag can't be located (never silent)."""
    p = repo["_abspath"]
    if entry["source"] == "url":
        t = entry["tag"]
        if not t:
            return None, "no tag in URL"
        if git(p, "rev-parse", "--verify", "--quiet", t + "^{}") is not None \
                or git(p, "rev-parse", "--verify", "--quiet", t) is not None:
            return t, ""
        return None, f"shipped tag '{t}' NOT found in repo"
    # curse: best-effort match by version token
    ver = entry["version_hint"]
    tags = (git(p, "tag") or "").splitlines()
    if not ver:
        return None, f"no version token in fileName (repo has {len(tags)} tag(s))"
    if not tags:
        return None, "repo has 0 tags — cannot locate shipped version"
    hits = [t for t in tags if ver in t.lower()]
    if not hits:
        return None, f"no tag matches version '{ver}' ({len(tags)} tag(s)) — verify manually"
    # prefer the shortest match (closest to an exact tag == the version) and note ambiguity
    hits.sort(key=len)
    note = "" if len(hits) == 1 else f"fuzzy tag (of {len(hits)}: {', '.join(hits[:3])})"
    return hits[0], note


CLASS_ORDER = ["feat", "fix", "perf", "security", "refactor", "build", "docs", "test", "chore", "other"]


def repo_ahead(repo, tag):
    """(count, {class: n}, [subjects]) for tag..HEAD; classified via changelog_from_git.commit_type."""
    p = repo["_abspath"]
    subjects = [s for s in (git(p, "log", f"{tag}..HEAD", "--format=%s") or "").splitlines()
                if s.strip() and not re.match(r"^Co-Authored-By", s, re.I)]
    counts = defaultdict(int)
    for s in subjects:
        t, _ = changelog_from_git.commit_type(s)
        counts[t if t in CLASS_ORDER else "other"] += 1
    return len(subjects), counts, subjects


# --- instance-drift side (reuse bundle_drift.classify) -----------------------
def instance_drift(canonical_config, mods_dir, cf_key):
    declared = bundle_drift.collect_declared(canonical_config, cf_key)
    installed, disabled = bundle_drift.scan_instance(mods_dir)
    res = bundle_drift.classify(declared, installed, disabled)
    drift_by_decl = {d: sibs for d, _src, sibs in res["version_drift"]}  # declared_fn_lower -> [sibs]
    removed = {d for d, _src in res["removals"]}
    return res, drift_by_decl, removed


def fmt_counts(counts):
    return " ".join(f"{k}:{counts[k]}" for k in CLASS_ORDER if counts.get(k))


def main():
    env = bundle_drift.load_env()
    instances = bundle_drift.resolve_instances(env)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", default=None, help="INSTANCE_<NAME>_CONFIG (default TEST)")
    ap.add_argument("--mods-dir", help="explicit instance mods/ dir (overrides --instance)")
    ap.add_argument("--canonical-config", help="explicit canonical src/common/config (default env)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only-flagged", action="store_true", help="hide clean rows")
    args = ap.parse_args()

    canonical = args.canonical_config or env.get("CANONICAL_CONFIG")
    if not canonical or not os.path.isdir(canonical):
        sys.exit(f"canonical config dir not found: {canonical!r} — set CANONICAL_CONFIG (.env).")

    if args.mods_dir:
        mods_dir = args.mods_dir
    else:
        name = args.instance or ("TEST" if "TEST" in instances else next(iter(instances), None))
        if not name or name not in instances:
            sys.exit("no instance configured — set INSTANCE_<NAME>_CONFIG (.env) or pass --mods-dir.")
        mods_dir = os.path.join(os.path.dirname(instances[name].rstrip("/")), "mods")
    if not os.path.isdir(mods_dir):
        sys.exit(f"instance mods dir not found: {mods_dir}")

    cf_key = env.get("CF_API_KEY")
    _repos, by_slug, by_namekey = load_repos()
    res, drift_by_decl, removed = instance_drift(canonical, mods_dir, cf_key)

    rows = []
    for e in bundle_entries(canonical):
        fn_l = e["fileName"].lower()
        # ---- instance signal (from bundle_drift.classify) ----
        inst_jars = drift_by_decl.get(fn_l)
        if inst_jars:
            inst_state, inst_txt = "drift", ", ".join(inst_jars)
        elif fn_l in removed:
            inst_state, inst_txt = "missing", "MISSING from instance"
        else:
            inst_state, inst_txt = "clean", "="
        # ---- repo-ahead signal ----
        repo, how = map_repo(e, by_slug, by_namekey)
        repo_state = "unmapped"
        head_txt, note = "-", how
        if repo and repo["_is_git"]:
            tag, tnote = resolve_tag(e, repo)
            if tag is None:
                repo_state, head_txt = "tagmiss", "?"
                note = f"{how}: {tnote}"
            else:
                cnt, counts, _subj = repo_ahead(repo, tag)
                if cnt > 0:
                    repo_state = "ahead"
                    head_txt = f"{repo['name']} +{cnt} past {tag}"
                    note = f"{how} [{fmt_counts(counts)}]"
                else:
                    repo_state = "clean"
                    head_txt = f"{repo['name']} == {tag}"
                    note = how
        elif repo and not repo["_is_git"]:
            note = f"{how}: repo not cloned locally"

        # ---- union signal ----
        inst_flag = inst_state in ("drift", "missing")
        repo_flag = repo_state == "ahead"
        if inst_flag and repo_flag:
            signal = "BOTH"
        elif inst_flag:
            signal = "INSTANCE_DRIFT"
        elif repo_flag:
            signal = "REPO_AHEAD"
        elif repo_state == "tagmiss":
            signal = "TAG?"
        else:
            signal = "clean"
        rows.append({"mod": (repo["name"] if repo else bundle_drift.base_key(e["fileName"]) or e["fileName"]),
                     "source": e["source"], "shipped": e["fileName"], "instance": inst_txt,
                     "repo_head": head_txt, "signal": signal, "note": note})

    # ---- undeclared additions (instance-only jars, no bundle entry / no repo) ----
    for j in res["undeclared"]:
        rows.append({"mod": bundle_drift.base_key(j) or j, "source": "instance", "shipped": "(none)",
                     "instance": j, "repo_head": "-", "signal": "INSTANCE_DRIFT",
                     "note": "undeclared addition (in mods/, no bundle declares it)"})

    non_clean = [r for r in rows if r["signal"] != "clean"]

    if args.json:
        import json
        print(json.dumps({
            "mods_dir": mods_dir, "canonical_config": canonical,
            "rows": rows,
            "summary": {"total": len(rows), "flagged": len(non_clean),
                        "instance_drift": sum(r["signal"] in ("INSTANCE_DRIFT", "BOTH") for r in rows),
                        "repo_ahead": sum(r["signal"] in ("REPO_AHEAD", "BOTH") for r in rows),
                        "both": sum(r["signal"] == "BOTH" for r in rows),
                        "tag_unresolved": sum(r["signal"] == "TAG?" for r in rows)},
            "limitation": "does NOT catch upstream-version bumps where the fork is BEHIND upstream "
                          "(e.g. lwjgl3ify 2.1.15->3.0.26); that is mod_update_checker.py / manual.",
        }, indent=2))
        sys.exit(1 if non_clean else 0)

    shown = non_clean if args.only_flagged else rows
    shown = sorted(shown, key=lambda r: ({"BOTH": 0, "REPO_AHEAD": 1, "INSTANCE_DRIFT": 2, "TAG?": 3,
                                          "clean": 9}.get(r["signal"], 4), r["mod"].lower()))

    print("=" * 108)
    print("RELEASE CHANGESET — union of instance-drift (bundle_drift) + repo-ahead (repos.json), "
          "per bundle mod")
    print(f"  instance mods/ : {mods_dir}")
    print(f"  canonical      : {canonical}")
    print("=" * 108)
    hdr = f"{'MOD':<26} {'SRC':<5} {'SIGNAL':<15} {'SHIPPED (bundle)':<40} {'INSTANCE / REPO-HEAD'}"
    print(hdr)
    print("-" * 108)
    for r in shown:
        rh = r["repo_head"] if r["repo_head"] != "-" else ""
        inst = r["instance"] if r["instance"] != "=" else ""
        detail = " | ".join(x for x in (inst if inst else "", rh) if x) or "(clean)"
        print(f"{r['mod'][:25]:<26} {r['source']:<5} {r['signal']:<15} {r['shipped'][:39]:<40} {detail}")
        if r["note"] and r["signal"] != "clean":
            print(f"{'':<26} {'':<5} {'':<15} note: {r['note']}")

    print("-" * 108)
    s = {"instance": sum(r["signal"] in ("INSTANCE_DRIFT", "BOTH") for r in rows),
         "repo": sum(r["signal"] in ("REPO_AHEAD", "BOTH") for r in rows),
         "both": sum(r["signal"] == "BOTH" for r in rows),
         "tag": sum(r["signal"] == "TAG?" for r in rows)}
    print(f"SUMMARY: {len(rows)} bundle mod(s) checked | {len(non_clean)} flagged "
          f"(INSTANCE_DRIFT {s['instance']}, REPO_AHEAD {s['repo']}, BOTH {s['both']}, "
          f"TAG-unresolved {s['tag']}).")
    print("LIMITATION: catches instance-drift + owned-fork-ahead-of-shipped-tag ONLY. It does NOT")
    print("  catch an upstream-version bump where the fork is BEHIND upstream (e.g. lwjgl3ify")
    print("  2.1.15->3.0.26) — that axis is mod_update_checker.py / a manual decision. A clean run")
    print("  here does NOT mean 'nothing to ship' if an upstream bump is pending.")
    sys.exit(1 if non_clean else 0)


if __name__ == "__main__":
    main()
