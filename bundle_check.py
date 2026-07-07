#!/usr/bin/env python3
"""
bundle_check.py — pre-release validator for a modpack's mod-director bundles.

Before shipping a pack update, every mod must actually be fetchable by players:
  - CurseForge mods (curse.bundle.json): the file must exist, be available, and be Approved.
    Note: the OFFICIAL CF API nulls downloadUrl when an author disables third-party API
    distribution, but mod-director downloads via api.curse.tools -> the forgecdn CDN URL, which
    still works. So a null downloadUrl is only a problem if the constructed CDN URL is ALSO dead
    (we verify that). Flagging null-downloadUrl alone was the BUG-010 false positive.
  - URL mods (url.bundle.json): the link must return 200 (dead GitHub release => broken pack).

Reads the bundle dir (default: the pack source's config/mod-director), checks everything,
prints a PASS/FAIL report and exits non-zero on any problem. CF checks need CF_API_KEY
(the free read key from console.curseforge.com — the same one mod_update_checker uses;
NOT the author upload token).

Usage:
  bundle_check.py                       # check the pack source bundles
  bundle_check.py --dir PATH            # a specific config/mod-director dir (e.g. an instance)
  bundle_check.py --urls-only           # skip CF (no key needed)
"""

import argparse
import concurrent.futures
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CF_API = "https://api.curseforge.com"
DEFAULT_DIR = os.path.expanduser(
    "~/Documents/GitHub/privates-minecraft-modpack/MODPACKS/Biggess Pack Cat Edition/src/common/config/mod-director"
)
# CurseForge fileStatus: 4 = Approved (the only value safe to ship).
CF_APPROVED = 4


def load_dotenv():
    conf = {}
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip("'\"")
    conf.update({k: v for k, v in os.environ.items() if k in ("CF_API_KEY",)})
    return conf


def http_json(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_bundle(directory, name, key):
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, [])


# --- CurseForge ---------------------------------------------------------------
def check_curse(entries, api_key):
    """Return list of (label, problem) for CF entries that would fail for players."""
    problems = []
    by_file = {}
    for e in entries:
        fid = e.get("fileId")
        if fid is not None:
            by_file[fid] = e
    file_ids = list(by_file)
    if not file_ids:
        return problems

    headers = {"x-api-key": api_key}
    seen = {}
    for i in range(0, len(file_ids), 50):
        batch = file_ids[i:i + 50]
        try:
            data = http_json(f"{CF_API}/v1/mods/files", "POST", {"fileIds": batch}, headers)["data"]
        except Exception as ex:
            problems.append((f"CF batch {i//50}", f"API error: {ex}"))
            continue
        for f in data:
            seen[f.get("id")] = f

    # mod-director fetches CF mods via api.curse.tools -> the forgecdn CDN URL. The OFFICIAL API's
    # flags (isAvailable, fileStatus, even downloadUrl) do NOT reflect that path — curse.tools serves
    # the CDN URL even for files the official API marks unavailable/null (verified on the whole
    # BUG-010 set). So the ONLY true fetchability test is: does the constructed CDN URL respond?
    # We only pay for that HEAD on entries the official API is unhappy about (keeps it fast).
    for fid, e in by_file.items():
        label = e.get("fileName") or f"addon {e.get('addonId')} file {fid}"
        f = seen.get(fid)
        reasons = []
        if f is None:
            reasons.append("not returned by the official CF API")
        else:
            if not f.get("isAvailable", False):
                reasons.append("isAvailable=false")
            if f.get("fileStatus") != CF_APPROVED:
                reasons.append(f"fileStatus={f.get('fileStatus')}")
            if not f.get("downloadUrl"):
                reasons.append("API downloadUrl=null")
        if not reasons:
            continue   # approved + available + has a downloadUrl -> fetchable, no need to probe
        fn = (f or {}).get("fileName") or e.get("fileName") or ""
        cdn = f"https://mediafilez.forgecdn.net/files/{fid // 1000}/{fid % 1000}/{urllib.parse.quote(fn)}"
        if check_one_url(cdn) is None:
            continue   # CDN serves it -> mod-director can install it despite the official flags
        problems.append((label, f"NOT fetchable — official API: {', '.join(reasons)}; CDN dead: {cdn}"))
    return problems


# --- CurseForge manifest (the modpack's own files[]) --------------------------
def check_manifest(bundle_dir, api_key):
    """Validate src/client/manifest.json files[] — each must be Approved on CF.

    Unlike bundle mods (mod-director fetches those via the CDN, tolerating odd API flags), the
    manifest files[] are installed by the CurseForge LAUNCHER directly: a rejected/pending file
    makes the whole modpack un-publishable/un-installable. This is what caught FileDirector fork9
    (rejected: installs external files) after pack_sync bumped the manifest to it.
    """
    manifest = os.path.normpath(os.path.join(bundle_dir, "..", "..", "..", "client", "manifest.json"))
    if not os.path.isfile(manifest):
        return []   # e.g. checking an instance dir, not the pack source
    with open(manifest, encoding="utf-8") as f:
        files = json.load(f).get("files", [])
    problems = []
    headers = {"x-api-key": api_key}
    for e in files:
        pid, fid = e.get("projectID"), e.get("fileID")
        if fid is None:
            continue
        try:
            data = http_json(f"{CF_API}/v1/mods/{pid}/files/{fid}", headers=headers).get("data")
        except Exception as ex:
            # Rejected/deleted files often 404 on the read API.
            problems.append((f"manifest file {pid}/{fid}", f"not retrievable on CF (rejected/deleted?): {ex}"))
            continue
        st = (data or {}).get("fileStatus")
        if st != CF_APPROVED:
            problems.append((f"manifest '{(data or {}).get('displayName')}' ({pid}/{fid})",
                             f"NOT Approved on CF (fileStatus={st}) — the modpack would be rejected"))
    return problems


# --- URL ----------------------------------------------------------------------
def check_one_url(url):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method)
            req.add_header("User-Agent", "bundle-check/1.0")
            if method == "GET":
                req.add_header("Range", "bytes=0-0")
            with urllib.request.urlopen(req, timeout=20) as r:
                code = r.getcode()
                if 200 <= code < 300:
                    return None
                return f"HTTP {code}"
        except urllib.error.HTTPError as ex:
            if method == "HEAD":
                continue  # HEAD is best-effort; GET below is authoritative (matches mod-director's
                          # download). GitHub release assets 500 on HEAD but 200 on GET, e.g.
            return f"HTTP {ex.code}"
        except Exception as ex:
            if method == "HEAD":
                continue
            return str(ex)
    return "unreachable"


def check_urls(entries):
    urls = [e.get("url") for e in entries if e.get("url")]
    problems = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for url, err in zip(urls, pool.map(check_one_url, urls)):
            if err:
                problems.append((url, err))
    return problems


# --- Orphans (a jar with no distribution source) ------------------------------
# Bootstrapper: shipped to the client via the CF manifest and directly to the server, never a
# bundle (it can't download itself). Any other jar not in a bundle would be missing for players.
ORPHAN_WHITELIST = ("mod-director-launchwrapper",)


def collect_expected(directory):
    """All filenames a bundle promises to deliver (curse/modrinth fileName, url basename)."""
    exp = set()
    for e in read_bundle(directory, "curse.bundle.json", "curse"):
        if e.get("fileName"):
            exp.add(e["fileName"].lower())
    for e in read_bundle(directory, "modrinth.bundle.json", "modrinth"):
        if e.get("fileName"):
            exp.add(e["fileName"].lower())
    for e in read_bundle(directory, "url.bundle.json", "url"):
        # mod-director saves the download as "fileName" if given (often != the URL basename).
        fn = e.get("fileName")
        if fn:
            exp.add(fn.lower())
        elif e.get("url"):
            exp.add(os.path.basename(urllib.parse.urlparse(e["url"]).path).lower())
    return exp


def check_orphans(mods_dir, expected):
    """Jars present in mods_dir that no bundle delivers (and aren't the whitelisted bootstrapper)."""
    orphans = []
    for p in sorted(glob.glob(os.path.join(mods_dir, "*.jar"))):
        name = os.path.basename(p)
        low = name.lower()
        if low in expected or any(w in low for w in ORPHAN_WHITELIST):
            continue
        orphans.append(name)
    return orphans


def main():
    ap = argparse.ArgumentParser(description="Validate modpack bundles before release.")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="config/mod-director directory")
    ap.add_argument("--urls-only", action="store_true", help="skip CurseForge checks")
    ap.add_argument("--orphans", metavar="MODS_DIR",
                    help="instead: list jars in MODS_DIR that no bundle delivers (would be missing for players)")
    args = ap.parse_args()

    if args.orphans:
        if not os.path.isdir(args.orphans):
            sys.exit(f"mods dir not found: {args.orphans}")
        expected = collect_expected(args.dir)
        orphans = check_orphans(args.orphans, expected)
        jars = len(glob.glob(os.path.join(args.orphans, "*.jar")))
        print(f"[orphan-check] {jars} jars in {args.orphans} vs {len(expected)} bundle-delivered filenames")
        print("=" * 60)
        if orphans:
            print(f"FAIL — {len(orphans)} orphan jar(s) with no bundle source (players won't get them):")
            for o in orphans:
                print(f"  ✗ {o}")
            sys.exit(1)
        print("PASS — every jar is delivered by a bundle (or is the bootstrapper).")
        return

    if not os.path.isdir(args.dir):
        sys.exit(f"bundle dir not found: {args.dir}")

    curse = read_bundle(args.dir, "curse.bundle.json", "curse")
    url = read_bundle(args.dir, "url.bundle.json", "url")
    print(f"[bundle-check] {len(curse)} CurseForge + {len(url)} URL entries in {args.dir}")

    problems = []
    if url:
        print("[bundle-check] checking URLs...")
        problems += check_urls(url)
    if not args.urls_only:
        env = load_dotenv()
        key = env.get("CF_API_KEY")
        if not key:
            print("[bundle-check] WARN: CF_API_KEY absent — CurseForge checks skipped "
                  "(set it in .env.local or use --urls-only)")
        else:
            if curse:
                print("[bundle-check] checking CurseForge files...")
                problems += check_curse(curse, key)
            print("[bundle-check] checking CF manifest files[]...")
            problems += check_manifest(args.dir, key)

    print("=" * 60)
    if problems:
        print(f"FAIL — {len(problems)} problem(s):")
        for label, why in problems:
            print(f"  ✗ {label}: {why}")
        sys.exit(1)
    print("PASS — all bundle mods approved/available and all URLs reachable.")


if __name__ == "__main__":
    main()
