#!/usr/bin/env python3
r"""
bundle_drift.py — bidirectional drift between a TEST instance's mods/ and the CANONICAL bundles.

The mod-based tools each cover one edge and leave a hole: pack_sync=fileIDs, bundle_check=are the
DECLARED mods fetchable, config_sync=game config. NONE answer "does the instance I test on actually
match what the bundles DECLARE players will get?". A stale/hand-edited test instance can carry a mod
players will NOT get (undeclared addition) or be MISSING a mod players WILL get (removal) — either
way the smoke/boot test validates a binary set that never ships. This closes that hole, BOTH ways.

What it compares:
  DECLARED  = curse.bundle.json + url.bundle.json + modrinth.bundle.json fileName/url-basename
              (canonical config/mod-director) + src/client/manifest.json files[] (resolved to a
              fileName via the CF read API when CF_API_KEY is available).
  INSTALLED = *.jar and *.zip at the top level of the instance's mods/ dir.

Three buckets (fuzzy by design — see HEURISTICS below; assumptions are LOGGED at run):
  UNDECLARED ADDITIONS  jar in mods/ that NO bundle/manifest declares -> testing a mod players
                        won't get. HARD drift (exit 1).
  REMOVALS / MISSING    a declared jar absent from mods/ -> testing WITHOUT a mod players get.
                        HARD drift (exit 1). A `<jar>.disabled-by-mod-director` marker =
                        intentionally disabled, reported separately, NOT a removal.
  VERSION DRIFT         an instance jar whose mod base-name matches a declared entry at a DIFFERENT
                        version (a version/fork swap, incl. a *-test build). SOFT (exit 0) — the
                        common, intentional TEST state (a locally-built jar swapped in).

HEURISTICS (conservative — tuned to NOT cry wolf on the known TEST state):
  - EXCLUDED from the undeclared alarm (dev/test/RPC jars, expected only in a test instance):
      *-test.jar, *-dev.jar, *-sources.jar, *-dev-*/*-test-*, gigafauna* (matoulib RPC, TEST-only
      per CLAUDE.md), geckolib-unofficial* (its hard dep).
  - WHITELIST (never an addition nor a removal): the mod-director bootstrapper
    (!mod-director-launchwrapper-*) — it self-installs / is delivered via the CF manifest, exactly
    as bundle_check's ORPHAN_WHITELIST treats it.
  - base-name key: lowercase filename, drop .jar/.zip, split on -_+()[] and whitespace, skip any
    LEADING version/noise tokens, then keep alpha tokens until the next version token (^v?\d or
    ^mc\d) or a noise word (universal/dev/test/forge/1.7.10/...). Two files with the SAME non-empty
    key (len>=4) but different filenames = a version swap. Imperfect on exotic names; a wrong pairing
    only DOWNGRADES a hard drift to a soft one (it never invents a mod), so it errs safe + is logged.

Paths come from config_sync.py's .env convention (CANONICAL_CONFIG, INSTANCE_<NAME>_CONFIG); the
instance's mods/ is the sibling of its config/ dir. Preview/report ONLY — never mutates anything.

Usage:
  bundle_drift.py                       # instance TEST vs canonical (default)
  bundle_drift.py --instance server     # another INSTANCE_<NAME>_CONFIG
  bundle_drift.py --mods-dir PATH --canonical-config PATH   # explicit override
  bundle_drift.py --json                # machine-readable summary
Exit: 1 on any HARD drift (undeclared addition OR removal); 0 if clean or only version drift.
"""

import argparse
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
CF_API = "https://api.curseforge.com"

# --- exclusions / whitelist --------------------------------------------------
# Bootstrapper: self-installs / CF-manifest delivered — never an addition or a removal (mirrors
# bundle_check.ORPHAN_WHITELIST). Substring match on the "!"-prefixed launchwrapper name.
BOOTSTRAP_WHITELIST = ("mod-director-launchwrapper",)
# Dev/test/RPC jars that legitimately live ONLY in a test instance (never shipped).
EXCLUDE_PREFIXES = ("gigafauna", "geckolib-unofficial")
DISABLED_MARKER = ".disabled-by-mod-director"

# base-name tokenizer: version tokens stop the key; noise words are non-identifying separators.
_NOISE = {
    "universal", "dev", "test", "sources", "api", "forge", "fabric", "client", "server",
    "mc1.7.10", "1.7.10", "1.7.2", "gtnh", "cat", "fork", "all", "release", "build",
    "final", "beta", "alpha", "pre", "rc", "snapshot", "dirty", "mod", "unofficial",
}
_VERSIONISH = re.compile(r"^(v?\d|mc\d)")
_SPLIT = re.compile(r"[\s\-_+()\[\]]+")


def log(msg):
    print(f"[bundle-drift] {msg}", flush=True)


# --- env / paths -------------------------------------------------------------
def load_env():
    conf = {}
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip("'\"")
    for k, v in os.environ.items():
        if k in ("CF_API_KEY", "CANONICAL_CONFIG") or (k.startswith("INSTANCE_") and k.endswith("_CONFIG")):
            conf[k] = v
    return conf


def resolve_instances(env):
    """{name: config_dir} from INSTANCE_<NAME>_CONFIG (config_sync convention)."""
    out = {}
    for k, v in env.items():
        if k.startswith("INSTANCE_") and k.endswith("_CONFIG") and v:
            out[k[len("INSTANCE_"):-len("_CONFIG")]] = v
    return out


# --- declared set (bundles + manifest) ---------------------------------------
def read_bundle(md_dir, name, key):
    path = os.path.join(md_dir, name)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, [])


def collect_declared(canonical_config, cf_key):
    """{filename_lower: source}. Sources: curse / url / modrinth / manifest.
    url entries usually carry no fileName -> mod-director saves the URL basename (matches bundle_check).
    manifest files[] carry only projectID/fileID -> resolved to fileName via the CF read API (skipped,
    with a note, when there is no CF_API_KEY)."""
    md = os.path.join(canonical_config, "mod-director")
    declared = {}
    for e in read_bundle(md, "curse.bundle.json", "curse"):
        if e.get("fileName"):
            declared[e["fileName"].lower()] = "curse"
    for e in read_bundle(md, "modrinth.bundle.json", "modrinth"):
        if e.get("fileName"):
            declared[e["fileName"].lower()] = "modrinth"
    for e in read_bundle(md, "url.bundle.json", "url"):
        fn = e.get("fileName")
        if not fn and e.get("url"):
            fn = os.path.basename(urllib.parse.urlparse(e["url"]).path)
        if fn:
            declared[fn.lower()] = "url"
    # manifest files[] — installed by the CF launcher directly; resolve fileID -> fileName via CF.
    manifest = os.path.normpath(os.path.join(md, "..", "..", "..", "client", "manifest.json"))
    if os.path.isfile(manifest):
        with open(manifest, encoding="utf-8") as f:
            files = json.load(f).get("files", [])
        if files and not cf_key:
            log(f"NOTE: {len(files)} manifest file(s) NOT resolved (no CF_API_KEY) — manifest-declared "
                f"jars are unverified this run (set CF_API_KEY in .env.local to include them).")
        for e in files:
            pid, fid = e.get("projectID"), e.get("fileID")
            if fid is None or not cf_key:
                continue
            try:
                req = urllib.request.Request(f"{CF_API}/v1/mods/{pid}/files/{fid}")
                req.add_header("x-api-key", cf_key)
                req.add_header("Accept", "application/json")
                with urllib.request.urlopen(req, timeout=30) as r:
                    fn = (json.load(r).get("data") or {}).get("fileName")
            except Exception as ex:
                log(f"NOTE: manifest file {pid}/{fid} unresolved on CF ({ex}) — skipped.")
                continue
            if fn:
                declared[fn.lower()] = "manifest"
    return declared


# --- instance scan -----------------------------------------------------------
def scan_instance(mods_dir):
    """(installed_lower_set, disabled_lower_set). Top-level *.jar/*.zip only (subdirs = mod configs)."""
    installed = set()
    for ext in ("*.jar", "*.zip"):
        for p in glob.glob(os.path.join(mods_dir, ext)):
            installed.add(os.path.basename(p).lower())
    disabled = set()
    for p in glob.glob(os.path.join(mods_dir, "*" + DISABLED_MARKER)):
        disabled.add(os.path.basename(p)[: -len(DISABLED_MARKER)].lower())
    return installed, disabled


# --- classification ----------------------------------------------------------
def base_key(fname):
    n = fname.lower()
    for ext in (".jar", ".zip"):
        if n.endswith(ext):
            n = n[: -len(ext)]
            break
    out = []
    for t in _SPLIT.split(n):
        if not t:
            continue
        stop = _VERSIONISH.match(t) or t in _NOISE
        if stop:
            if out:          # a version/noise token AFTER the name ends the key
                break
            continue         # ...but LEADING version/noise junk ([1.7.10], +, mc1.7.10) is skipped
        out.append(t)
    return "".join(out)


def is_whitelisted(fname):
    low = fname.lower()
    return any(w in low for w in BOOTSTRAP_WHITELIST)


def is_excluded(fname):
    n = fname.lower()
    if any(n.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    return (n.endswith("-test.jar") or n.endswith("-dev.jar") or n.endswith("-sources.jar")
            or "-dev-" in n or "-test-" in n)


def classify(declared, installed, disabled):
    inst_by_key = defaultdict(set)
    for j in installed:
        inst_by_key[base_key(j)].add(j)

    # Version drift, keyed on the DECLARED-missing side so a swap to an EXCLUDED build (e.g.
    # optimizationsandtweaks-test) is still recognized and does NOT read as a removal.
    drift, drift_inst, drift_decl = [], set(), set()
    for d in sorted(declared):
        if d in installed or is_whitelisted(d):
            continue
        k = base_key(d)
        if len(k) < 4:
            continue
        sibs = sorted(j for j in inst_by_key.get(k, ()) if j not in declared)
        if sibs:
            drift.append((d, declared[d], sibs))
            drift_decl.add(d)
            drift_inst.update(sibs)

    removals, disabled_hits = [], []
    for d in sorted(declared):
        if d in installed or d in drift_decl or is_whitelisted(d):
            continue
        if d in disabled:
            disabled_hits.append((d, declared[d]))
        else:
            removals.append((d, declared[d]))

    undeclared, excluded = [], []
    for j in sorted(installed):
        if j in declared or j in drift_inst or is_whitelisted(j):
            continue
        (excluded if is_excluded(j) else undeclared).append(j)

    return {
        "version_drift": drift, "removals": removals, "disabled": disabled_hits,
        "undeclared": undeclared, "excluded": excluded,
    }


# --- report ------------------------------------------------------------------
def report(res, declared, installed, mods_dir, canonical_config, as_json):
    if as_json:
        print(json.dumps({
            "mods_dir": mods_dir, "canonical_config": canonical_config,
            "declared": len(declared), "installed": len(installed),
            "undeclared_additions": res["undeclared"],
            "removals": [d for d, _ in res["removals"]],
            "version_drift": [{"declared": d, "instance": s} for d, _, s in res["version_drift"]],
            "excluded": res["excluded"],
            "declared_but_disabled": [d for d, _ in res["disabled"]],
        }, indent=2))
        return bool(res["undeclared"] or res["removals"])

    print("=" * 72)
    print(f"[bundle-drift] {len(installed)} installed jar/zip  vs  {len(declared)} declared "
          f"(bundles+manifest)")
    print(f"  instance mods/ : {mods_dir}")
    print(f"  canonical      : {canonical_config}")
    if res["excluded"]:
        print(f"\n  excluded from the undeclared alarm ({len(res['excluded'])} dev/test/RPC jar(s)): "
              + ", ".join(res["excluded"]))

    print("\n" + "-" * 72)
    print(f"UNDECLARED ADDITIONS  ({len(res['undeclared'])}) — in mods/, no bundle/manifest declares it "
          "(players won't get it):")
    for j in res["undeclared"]:
        print(f"  + {j}")
    if not res["undeclared"]:
        print("  (none)")

    print("\n" + "-" * 72)
    print(f"REMOVALS / MISSING  ({len(res['removals'])}) — declared but absent from mods/ "
          "(players get it, you don't test it):")
    for d, src in res["removals"]:
        print(f"  - {d}  ({src})")
    if not res["removals"]:
        print("  (none)")

    print("\n" + "-" * 72)
    print(f"VERSION DRIFT  ({len(res['version_drift'])}) — declared vs a different version/build in "
          "mods/ (usually intentional test state):")
    for d, src, sibs in res["version_drift"]:
        print(f"  ~ {src}: declared {d}  ->  instance {', '.join(sibs)}")
    if not res["version_drift"]:
        print("  (none)")

    if res["disabled"]:
        print("\n" + "-" * 72)
        print(f"DECLARED-BUT-DISABLED  ({len(res['disabled'])}) — {DISABLED_MARKER} marker present "
              "(intentionally disabled, not a removal):")
        for d, src in res["disabled"]:
            print(f"  . {d}  ({src})")

    hard = len(res["undeclared"]) + len(res["removals"])
    print("\n" + "=" * 72)
    if hard:
        print(f"DRIFT — {len(res['undeclared'])} undeclared addition(s) + {len(res['removals'])} "
              f"removal(s). The test instance does NOT match what the bundles declare.")
        return True
    print("IN SYNC — no undeclared additions or removals"
          + (f" ({len(res['version_drift'])} version-swap(s) noted)" if res["version_drift"] else "")
          + ". The test instance matches the declared mod set.")
    return False


def main():
    env = load_env()
    instances = resolve_instances(env)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", default=None,
                    help="instance name from INSTANCE_<NAME>_CONFIG (default: TEST, else the first)")
    ap.add_argument("--mods-dir", help="explicit instance mods/ dir (overrides --instance)")
    ap.add_argument("--canonical-config", help="explicit canonical src/common/config dir "
                    "(default: CANONICAL_CONFIG env)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    canonical = args.canonical_config or env.get("CANONICAL_CONFIG")
    if not canonical or not os.path.isdir(canonical):
        sys.exit(f"canonical config dir not found: {canonical!r} — set CANONICAL_CONFIG (.env) or "
                 "pass --canonical-config.")

    if args.mods_dir:
        mods_dir = args.mods_dir
    else:
        name = args.instance or ("TEST" if "TEST" in instances else next(iter(instances), None))
        if not name:
            sys.exit("no instance configured — set INSTANCE_<NAME>_CONFIG (.env) or pass --mods-dir.")
        if name not in instances:
            sys.exit(f"unknown instance {name!r} — configured: {', '.join(instances) or '(none)'}")
        # instance config dir's sibling is mods/
        mods_dir = os.path.join(os.path.dirname(instances[name].rstrip("/")), "mods")
        if not args.json:
            log(f"instance {name}: mods/ = {mods_dir}")
    if not os.path.isdir(mods_dir):
        sys.exit(f"instance mods dir not found: {mods_dir}")

    cf_key = env.get("CF_API_KEY")
    declared = collect_declared(canonical, cf_key)
    installed, disabled = scan_instance(mods_dir)
    res = classify(declared, installed, disabled)
    drift = report(res, declared, installed, mods_dir, canonical, args.json)
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
