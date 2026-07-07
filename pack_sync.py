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
CLIENT_MANIFEST = f"{PACK}/src/client/manifest.json"
SERVER_MODS = f"{PACK}/src/server/mods"
CF_API = "https://api.curseforge.com"

APPLY = "--apply" in sys.argv
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
    """Format-preserving single substring replace; records a change or a problem."""
    text = open(path, encoding="utf-8").read()
    n = text.count(old_str)
    if old_str == new_str:
        log(f"    = {label}: already up to date ({new_str})")
        return
    if n == 0:
        problems.append(f"{label}: {old_str!r} not found in {os.path.basename(path)}")
        return
    if n > 1:
        problems.append(f"{label}: {old_str!r} ambiguous ({n}x) in {os.path.basename(path)}")
        return
    changes.append((path, old_str, new_str))
    if APPLY:
        open(path, "w", encoding="utf-8").write(text.replace(old_str, new_str))
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


def _iter_dicts(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _iter_dicts(v)
    elif isinstance(x, list):
        for v in x:
            yield from _iter_dicts(v)


def main():
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


if __name__ == "__main__":
    main()
