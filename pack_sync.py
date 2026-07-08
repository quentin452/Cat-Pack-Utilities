#!/usr/bin/env python3
"""Sync freshly-released mod fileIDs into the modpack config, then verify — the step that actually
delivers a mod release to pack players. Driven by the `pack` blocks in release-manifest.json.

Three delivery paths (a mod uses whichever its `pack.delivery` lists):
  - curse_bundle   : mod-director downloads it -> update fileId (+fileName) in curse.bundle.json
  - client_manifest: CF launcher ships it with the pack -> update fileID in src/client/manifest.json
  - server_jar     : a direct jar in src/server/mods -> copy the fresh build in, drop the old one

FileDirector is the tricky one (user flag): it delivers ITSELF (client via the CF pack manifest,
server via a direct jar), NOT via its own bundle. So its client fileID and its server jar must agree
on the fork — this script asserts that.

JSON edits are byte-level substring replacements (never a json.dump round-trip) so the huge
curse.bundle/manifest files keep their exact formatting — only the changed value moves.

Usage:
  python3 pack_sync.py            # DRY-RUN: show every change + run verification
  python3 pack_sync.py --apply    # write the changes
"""
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox")
MANIFEST = f"{HUB}/memory/release-manifest.json"
PACK = os.path.expanduser("~/Documents/GitHub/privates-minecraft-modpack/MODPACKS/Biggess Pack Cat Edition")
CURSE_BUNDLE = f"{PACK}/src/common/config/mod-director/curse.bundle.json"
URL_BUNDLE = f"{PACK}/src/common/config/mod-director/url.bundle.json"
CLIENT_MANIFEST = f"{PACK}/src/client/manifest.json"
SERVER_MODS = f"{PACK}/src/server/mods"
CF_API = "https://api.curseforge.com"

APPLY = "--apply" in sys.argv
# --check: strict mode — exit non-zero when there are PENDING (would-change but unapplied) fileID
# edits. GATE 2 of release_pack uses this so a manifest fileID bumped-but-not-`--apply`'d can't sail
# through green and package zips with the OLD (stale) fileIDs.
CHECK = "--check" in sys.argv
problems = []
changes = []


def log(m):
    print(m)


def load_api_key():
    for name in (".env.local", ".env"):
        p = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(p):
            for line in open(p):
                line = line.strip()
                if line.startswith("CF_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get("CF_API_KEY")


def cf_files(file_ids, key):
    """{fileId: {fileName, downloadUrl, ...}} via the read API (needs CF_API_KEY)."""
    if not key or not file_ids:
        return {}
    req = urllib.request.Request(f"{CF_API}/v1/mods/files", method="POST",
                                 data=json.dumps({"fileIds": file_ids}).encode())
    req.add_header("x-api-key", key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)["data"]
    return {f["id"]: f for f in data}


def cdn_ok(file_id, file_name):
    url = f"https://mediafilez.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/{urllib.parse.quote(file_name)}"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200, url
    except Exception as e:
        return False, f"{url} ({e})"


def replace_value(path, old_str, new_str, label):
    """Format-preserving single substring replace. Operates on BYTES so CRLF line endings (curse.bundle
    is CRLF) survive — a text-mode read/write would normalize CRLF->LF and rewrite the whole file."""
    data = open(path, "rb").read()
    old_b, new_b = old_str.encode("utf-8"), new_str.encode("utf-8")
    if old_str == new_str:
        log(f"    = {label}: already up to date ({new_str})")
        return
    n = data.count(old_b)
    if n == 0:
        problems.append(f"{label}: {old_str!r} not found in {os.path.basename(path)}")
        return
    if n > 1:
        problems.append(f"{label}: {old_str!r} ambiguous ({n}x) in {os.path.basename(path)}")
        return
    changes.append((path, old_str, new_str))
    if APPLY:
        open(path, "wb").write(data.replace(old_b, new_b))
    log(f"    {'APPLIED' if APPLY else 'would change'} {label}: {old_str} -> {new_str}")


def find_curse_entry(project_id):
    d = json.load(open(CURSE_BUNDLE))
    for e in _iter_dicts(d):
        if e.get("addonId") == project_id:
            return e
    return None


def find_manifest_entry(project_id):
    d = json.load(open(CLIENT_MANIFEST))
    for e in d.get("files", []):
        if e.get("projectID") == project_id:
            return e
    return None


def find_url_entry(match):
    for e in _iter_dicts(json.load(open(URL_BUNDLE))):
        if isinstance(e.get("url"), str) and match in e["url"]:
            return e
    return None


def url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status == 200, url
    except Exception as e:
        return False, f"{url} ({e})"


def resolve_gh_asset_url(old_url, new_ver):
    """Resolve the REAL download URL of a GitHub release's primary jar so a version bump survives an
    artifact RENAME. Naive substitution (old_url.replace(old_ver, new_ver)) 404s when the build's jar
    name drifts across versions — e.g. IE fork4 dropped the '-mc1.7.10-' infix
    (ImmersiveEngineering-mc1.7.10-0.7.11-fork3.jar -> ImmersiveEngineering-0.7.11-fork4.jar). Query
    the release by tag=new_ver (public GitHub API), pick the .jar asset that is NOT
    -sources/-dev/-javadoc, preferring one whose name carries the version. Returns None on any failure
    so the caller falls back to pattern substitution."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/releases/download/", old_url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(new_ver)}"
    try:
        req = urllib.request.Request(
            api, headers={"User-Agent": "pack_sync", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            assets = json.load(r).get("assets", [])
    except Exception:
        return None
    jars = [a for a in assets if a.get("name", "").endswith(".jar")
            and not re.search(r"-(sources|dev|javadoc)\.jar$", a["name"])]
    if not jars:
        return None
    jars.sort(key=lambda a: (new_ver not in a["name"], len(a["name"])))  # prefer version-carrying, shortest
    return jars[0].get("browser_download_url")


def _iter_dicts(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _iter_dicts(v)
    elif isinstance(x, list):
        for v in x:
            yield from _iter_dicts(v)


def audit():
    """Read-only freshness check: every quentin452 GitHub-delivered mod in url.bundle vs its latest
    GitHub release tag. Flags bundle-older-than-latest. NOTE: multi-asset repos (one repo, many mods,
    each its own tag — e.g. Familiars-API, Biggess-Pack-Cat-Edition-Mods) produce false positives
    because `gh release view` returns only the newest tag; treat those as signal, not gospel."""
    import re as _re
    import subprocess
    data = open(URL_BUNDLE, encoding="utf-8", errors="replace").read()
    seen = {}
    for m in _re.finditer(r"quentin452/([A-Za-z0-9._-]+)/releases/download/([^/\"]+)", data):
        repo, tag = m.group(1), m.group(2)
        if repo == "Biggess-Pack-Cat-Edition-Mods":
            continue  # content mega-repo: per-asset tags, not comparable
        seen.setdefault(repo, set()).add(tag)
    print(f"=== pack freshness audit — {len(seen)} quentin452 fork repos in url.bundle ===")
    stale = []
    for repo in sorted(seen):
        r = subprocess.run(["gh", "release", "view", "-R", f"quentin452/{repo}",
                            "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True)
        latest = r.stdout.strip()
        if not latest:
            continue
        tags = seen[repo]
        if latest not in tags:
            multi = len(tags) > 1
            stale.append((repo, sorted(tags), latest, multi))
    if not stale:
        print("  all bundle versions match latest release.")
        return
    for repo, tags, latest, multi in stale:
        note = "  (multi-tag repo — likely FALSE POSITIVE)" if multi else ""
        print(f"  STALE  {repo}: bundle={','.join(tags)}  latest={latest}{note}")
    print("\nReview each: single-tag repos = real; multi-tag = verify (per-sub-mod tags are intentional).")


def main():
    if "--audit" in sys.argv:
        audit()
        return
    key = load_api_key()
    mods = json.load(open(MANIFEST))["mods"]
    pack_mods = [m for m in mods if m.get("pack")]
    if not pack_mods:
        sys.exit("no mods have a `pack` block in release-manifest.json")

    ids = [m["pack"]["cf_file_id"] for m in pack_mods if m["pack"].get("cf_file_id")]
    cf = cf_files(ids, key)
    if not cf:
        log("WARN: no CF_API_KEY (read) — can't fetch authoritative fileNames/verify CDN; edits by id only")

    log(f"=== pack_sync {'[APPLY]' if APPLY else '[DRY-RUN]'} — {len(pack_mods)} pack mod(s) ===\n")
    for m in pack_mods:
        pk = m["pack"]
        pid = m["curseforge"]["project_id"] if m.get("curseforge") else None
        new_fid = pk.get("cf_file_id")
        delivery = pk.get("delivery", "")
        info = cf.get(new_fid, {})
        new_fn = info.get("fileName")
        new_disp = info.get("displayName")  # carries the -forkN (the CF fileName often doesn't)
        log(f"[{m['name']}] delivery={delivery} cf_file={new_fid} fileName={new_fn} display={new_disp}")

        # BUG-023 guard: NEVER write a non-Approved fileID into the pack. fileStatus 4 = Approved is
        # the only value safe to ship — a Rejected/pending file in the client manifest gets the whole
        # modpack rejected at CF review (fork9 8386799 did exactly that). Only enforceable with the
        # read API; without a key we already warned above.
        if new_fid and info and ("curse_bundle" in delivery or "client_manifest" in delivery):
            if info.get("fileStatus") != 4:
                problems.append(f"{m['name']}: cf_file_id {new_fid} fileStatus="
                                f"{info.get('fileStatus')} (NOT Approved) — refusing to sync it")
                log("    ⛔ SKIP: target file is not Approved on CF (see release-manifest _pin note)")
                continue

        if "curse_bundle" in delivery:
            e = find_curse_entry(pid)
            if not e:
                problems.append(f"{m['name']}: addonId {pid} not in curse.bundle.json")
            else:
                replace_value(CURSE_BUNDLE, f'"fileId": {e["fileId"]}', f'"fileId": {new_fid}',
                              f"{m['name']} curse.bundle fileId")
                if new_fn and e.get("fileName"):
                    replace_value(CURSE_BUNDLE, f'"fileName": "{e["fileName"]}"',
                                  f'"fileName": "{new_fn}"', f"{m['name']} curse.bundle fileName")

        if "client_manifest" in delivery:
            e = find_manifest_entry(pid)
            if not e:
                problems.append(f"{m['name']}: projectID {pid} not in client manifest.json")
            else:
                replace_value(CLIENT_MANIFEST, f'"fileID": {e["fileID"]}', f'"fileID": {new_fid}',
                              f"{m['name']} client manifest fileID")

        if "url_bundle" in delivery:
            e = find_url_entry(pk["url_match"])
            if not e:
                problems.append(f"{m['name']}: no url.bundle entry matching {pk['url_match']!r}")
            else:
                old_url = e["url"]
                mo = re.search(r"/releases/download/([^/]+)/", old_url)
                old_ver, new_ver = (mo.group(1) if mo else None), m["version"]
                if not old_ver:
                    problems.append(f"{m['name']}: can't parse version from url {old_url}")
                elif old_ver == new_ver:
                    log(f"    = {m['name']} url.bundle already {new_ver}")
                else:
                    # Resolve the real release asset (survives artifact renames, cf. IE fork4);
                    # fall back to naive version substitution if the GitHub API is unreachable.
                    new_url = resolve_gh_asset_url(old_url, new_ver) or old_url.replace(old_ver, new_ver)
                    replace_value(URL_BUNDLE, f'"url": "{old_url}"', f'"url": "{new_url}"',
                                  f"{m['name']} url.bundle {old_ver}->{new_ver}")
                    ok, u = url_ok(new_url)
                    log(f"    {'OK' if ok else 'DEAD'} url.bundle target reachable: {new_url}")
                    if not ok:
                        problems.append(f"{m['name']}: new url.bundle URL not reachable: {u}")
                # url.bundle is shared common config -> both client AND server fetch the same jar,
                # so this single edit keeps client==server (the mismatch the smoke caught).

        if "server_jar" in delivery:
            src = os.path.expanduser(pk["server_jar_src"])
            dst = os.path.join(SERVER_MODS, pk["server_jar_name"])
            if not os.path.isfile(src):
                problems.append(f"{m['name']}: server jar src missing: {src}")
            else:
                old = [f for f in os.listdir(SERVER_MODS)
                       if re.match(r"!?mod-director-launchwrapper", f) and f != pk["server_jar_name"]]
                log(f"    server jar: copy {os.path.basename(src)} -> {pk['server_jar_name']}; drop {old}")
                changes.append(("<server-jar>", src, dst))
                if APPLY:
                    for f in old:
                        os.remove(os.path.join(SERVER_MODS, f))
                    if not (os.path.exists(dst) and os.path.samefile(src, dst)):
                        shutil.copy2(src, dst)
                # FileDirector consistency: client fileID's fork must match the server jar fork
                fork = pk.get("fork")
                cf_fork = re.search(r"fork\d+", new_disp or new_fn or "")
                jar_fork = re.search(r"fork\d+", pk["server_jar_name"])
                if fork and jar_fork and jar_fork.group() != fork:
                    problems.append(f"{m['name']}: server jar name fork {jar_fork.group()} != manifest fork {fork}")
                if cf_fork and fork and cf_fork.group() != fork:
                    problems.append(f"{m['name']}: CF CLIENT file is {cf_fork.group()} but pack expects {fork} "
                                    f"(client/server FORK MISMATCH — players get {cf_fork.group()} client, "
                                    f"{fork} server)")
                elif cf_fork:
                    log(f"    OK FileDirector fork consistency: CF client {cf_fork.group()} == server jar {fork}")
                else:
                    problems.append(f"{m['name']}: could not read a forkN from CF displayName {new_disp!r} "
                                    f"— cannot confirm client==server fork")
        log("")

    # verify CDN reachability of every new file
    log("--- verify: CDN fetchable ---")
    for m in pack_mods:
        fid = m["pack"].get("cf_file_id")
        fn = cf.get(fid, {}).get("fileName")
        if fn:
            ok, url = cdn_ok(fid, fn)
            log(f"  [{'OK ' if ok else 'DEAD'}] {m['name']} {fid} {fn}")
            if not ok:
                problems.append(f"{m['name']}: CDN not reachable: {url}")
        else:
            log(f"  [ ? ] {m['name']} {fid} — no fileName (no CF key), skipped CDN check")

    log("")
    if problems:
        log(f"=== {len(problems)} PROBLEM(S) — NOT safe to release ===")
        for p in problems:
            log(f"  ✗ {p}")
        sys.exit(1)
    log(f"=== OK — {len(changes)} change(s) {'applied' if APPLY else 'pending (run --apply)'} ===")
    if not APPLY and changes:
        log("    then run bundle_check + build zips + upload (see release skill).")
        if CHECK:
            log(f"⛔ --check: {len(changes)} PENDING fileID edit(s) NOT applied — run "
                f"pack_sync --apply first (zips would ship the OLD fileIDs).")
            sys.exit(2)


if __name__ == "__main__":
    main()
