#!/usr/bin/env python3
"""pipeline_status.py — end-to-end delivery dashboard: is the work we validated actually SHIPPED?

The blind spot it closes (2026-07-08): we in-game-validated 6 OaT commits in the pack, then shipped
pack V1.1.9 — which only carried a config change, because OaT V1.17.4 was never released and nothing
SAID so. Each tool (release_mods, pack_sync, bundle_check, update_local) checks its own stage; none
shows the WHOLE chain. This one aggregates them, per mod of the release set (release-manifest.json):

    repo HEAD ──held?──▶ git tag ──released?──▶ CF file ──bundled?──▶ pack bundles ──synced?──▶ instances

and for the pack: last cut vs HEAD (pending derive), CF latest file status.

Read-only. Uses: release-manifest.json, git (tags/held/unpushed), CF read API (latest file + status),
pack_sync's bundle parsers (imported), changelog_from_bundles.py + update_local.py (subprocess).

Usage:  python3 pipeline_status.py            # full dashboard + NEXT ACTIONS
        python3 pipeline_status.py --no-net   # skip CF API / network bits
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pack_sync  # reuse: find_curse_entry / find_manifest_entry / find_url_entry / load_api_key

HERE = Path(__file__).resolve().parent
MANIFEST = Path.home() / "Documents/GitHub/Mod-Sandbox/memory/release-manifest.json"
PACK_REPO = Path.home() / "Documents/GitHub/privates-minecraft-modpack"
PACK_DIR = PACK_REPO / "MODPACKS/Biggess Pack Cat Edition"
PACK_PROJECT_ID = 830694
NOISE = re.compile(r"^(chore|docs|test|style|ci)(\(|:)", re.IGNORECASE)


def git(repo, *args):
    p = subprocess.run(["git", "-C", os.path.expanduser(str(repo))] + list(args),
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def cf_get(path, key):
    if not key:
        return None
    req = urllib.request.Request(f"https://api.curseforge.com/v1/{path}")
    req.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)["data"]
    except Exception:
        return None


FILESTATUS = {1: "Processing", 2: "ChangesRequired", 3: "UnderReview", 4: "Approved",
              5: "Rejected", 10: "Released"}


def instance_jars():
    """{mod name: {instance: current jar}} parsed from update_local.py dry-run output."""
    p = subprocess.run([sys.executable, str(HERE / "update_local.py")], capture_output=True, text=True)
    out = {}
    mod = None
    for line in p.stdout.splitlines():
        m = re.match(r"^(\S[^:]*): \S+\.jar$", line)
        if m:
            mod = m.group(1)
            out[mod] = {}
            continue
        m = re.match(r"^\s+([^:]+): (?:-1 old \((.*?)\) )?cp (\S+)", line)
        if m and mod:
            out[mod][m.group(1).strip()] = {"current": m.group(2) or "(none)", "built": m.group(3)}
        elif mod and re.match(r"^\s+[^:]+: ok ", line):
            inst = line.strip().split(":")[0]
            out[mod][inst] = {"current": "(up to date)", "built": None}
    return out


def main():
    ap = argparse.ArgumentParser(description="End-to-end delivery status of the release set + pack.")
    ap.add_argument("--no-net", action="store_true", help="skip CF API checks")
    args = ap.parse_args()

    key = None if args.no_net else pack_sync.load_api_key()
    mods = json.load(open(MANIFEST))["mods"]
    inst = instance_jars()
    actions = []

    print("=" * 100)
    print("PIPELINE STATUS — repo ▶ release ▶ CF ▶ pack bundle ▶ instances")
    print("=" * 100)

    for m in mods:
        name, repo = m["name"], m["repo"]
        tag = git(repo, "describe", "--tags", "--abbrev=0")
        held_all = (git(repo, "log", "--oneline", f"{tag}..HEAD") or "").splitlines() if tag else []
        held = [l for l in held_all if not NOISE.search(l.split(" ", 1)[1] if " " in l else l)]
        unpushed = (git(repo, "log", "--oneline", "@{u}..") or "").splitlines()

        cf_latest = None
        pid = (m.get("curseforge") or {}).get("project_id")
        if pid and key:
            data = cf_get(f"mods/{pid}", key)
            if data and data.get("latestFiles"):
                f = max(data["latestFiles"], key=lambda x: x["id"])
                cf_latest = {"id": f["id"], "name": f.get("displayName") or f.get("fileName"),
                             "status": FILESTATUS.get(f.get("fileStatus"), f.get("fileStatus"))}

        pk = m.get("pack") or {}
        bundle_ref = None
        if "curse_bundle" in pk.get("delivery", ""):
            e = pack_sync.find_curse_entry(pid)
            bundle_ref = f"curse.bundle fileId {e['fileId']}" if e else "NOT IN BUNDLE"
            if e and cf_latest and e["fileId"] != cf_latest["id"]:
                actions.append(f"{name}: pack bundle ships file {e['fileId']} but CF latest is "
                               f"{cf_latest['id']} ({cf_latest['name']}) -> pack_sync + pack release")
        elif "client_manifest" in pk.get("delivery", ""):
            e = pack_sync.find_manifest_entry(pid)
            bundle_ref = f"client manifest fileID {e['fileID']}" if e else "NOT IN MANIFEST"
        elif "url_bundle" in pk.get("delivery", ""):
            e = pack_sync.find_url_entry(pk.get("url_match", name))
            ver = None
            if e:
                mo = re.search(r"/releases/download/([^/]+)/", e["url"])
                ver = mo.group(1) if mo else e["url"]
            bundle_ref = f"url.bundle {ver}" if e else "NOT IN URL BUNDLE"
            if ver and m.get("version") and ver != m["version"]:
                actions.append(f"{name}: url.bundle ships {ver} but manifest version is {m['version']}")

        print(f"\n[{name}]  tag {tag or '(none)'}  version {m.get('version')}")
        if held:
            print(f"  ⚠ HELD since {tag}: {len(held)} commit(s) not in any release:")
            for l in held[:6]:
                print(f"      {l}")
            actions.append(f"{name}: {len(held)} unreleased commit(s) since {tag} -> release next version"
                           + (" (gate: rust natives rebuild)" if name == "OptimizationsAndTweaks" else ""))
        else:
            print("  ✓ no unreleased work (HEAD == last tag"
                  + (", modulo noise commits)" if held_all else ")"))
        if unpushed:
            print(f"  ✋ {len(unpushed)} commit(s) held locally (unpushed — user pushes)")
        if cf_latest:
            flag = "" if cf_latest["status"] in ("Approved", "Released") else "  ⚠ NOT Approved"
            print(f"  CF latest: {cf_latest['id']} {cf_latest['name']} [{cf_latest['status']}]{flag}")
        if bundle_ref:
            print(f"  pack: {bundle_ref}" + (f" (pinned: {pk['_pin'][:60]}…)" if pk.get("_pin") else ""))
        for iname, j in (inst.get(name) or {}).items():
            stale = j["built"] and j["current"] not in ("(up to date)",)
            mark = "⚠ STALE" if stale else "✓"
            print(f"  {mark} instance {iname}: {j['current']}" + (f" -> {j['built']}" if stale else ""))
            if stale:
                actions.append(f"{name}: instance '{iname}' runs {j['current']} -> update_local --apply")

    # ---- pack ----
    print("\n" + "=" * 100)
    cut = git(PACK_REPO, "log", "--grep", "release: cut V", "--oneline", "-1")
    cut_ref, cut_msg = (cut.split(" ", 1) + [""])[:2] if cut else (None, "")
    manifest_ver = None
    try:
        manifest_ver = json.load(open(PACK_DIR / "src/client/manifest.json"))["version"]
    except Exception:
        pass
    print(f"[PACK]  manifest version {manifest_ver}  |  last cut: {cut}")
    if cut_ref:
        pending = subprocess.run([sys.executable, str(HERE / "changelog_from_bundles.py"),
                                  f"{cut_ref}..HEAD", "--markdown"], capture_output=True, text=True)
        body = "\n".join(l for l in pending.stdout.splitlines()[1:]
                         if l.strip() and "no bundle or config changes" not in l)
        if body.strip():
            print("  ⚠ UNSHIPPED pack changes since the last cut:")
            print("    " + body.replace("\n", "\n    "))
            actions.append("PACK: unshipped bundle/config changes since last cut -> next pack release")
        else:
            print("  ✓ no pack-level change since the last cut")
    if key:
        data = cf_get(f"mods/{PACK_PROJECT_ID}", key)
        if data:
            for f in sorted(data.get("latestFiles", []), key=lambda x: -x["id"])[:2]:
                st = FILESTATUS.get(f.get("fileStatus"), f.get("fileStatus"))
                print(f"  CF: {f['id']} {f.get('displayName')} [{st}]"
                      + ("" if st in ("Approved", "Released") else "  ⏳ review pending"))

    print("\n" + "=" * 100)
    if actions:
        print("NEXT ACTIONS (the gap between what exists and what players have):")
        for a in dict.fromkeys(actions):
            print(f"  • {a}")
    else:
        print("NEXT ACTIONS: none — everything validated is shipped everywhere. 🎉")


if __name__ == "__main__":
    main()
