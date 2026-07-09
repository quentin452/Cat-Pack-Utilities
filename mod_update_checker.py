#!/usr/bin/env python3
"""Mod update checker for FileDirector-managed 1.7.10 packs.

Inventory sources (config/mod-director/ in the instance):
  - curse.bundle.json    -> CurseForge addonId/fileId (needs CF_API_KEY for lookups)
  - url.bundle.json      -> direct GitHub release URLs (checked via `gh api`)
  - modrinth.bundle.json -> Modrinth project/version (public API, no key)

Age ranking fallback that needs NO network/API: the newest zip-entry
timestamp inside each jar approximates its build date — good enough to
rank the oldest mods across the whole mods/ folder.

Configuration (.env committed defaults, .env.local for secrets, gitignored;
real environment variables win over both):
  INSTANCE_PATH  path to the CurseForge instance (or use --instance)
  CF_API_KEY     enables the CurseForge lookups (console.curseforge.com, free)

Usage:
  mod_update_checker.py [--instance PATH] [--top N] [--json OUT]
                        [--skip-github] [--skip-curse] [--skip-zipdates]
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

import packenv as E  # single source for secrets/paths

CF_API = "https://api.curseforge.com"
MODRINTH_API = "https://api.modrinth.com/v2"
GH_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?]+)")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv():
    """Merge .env then .env.local into the config; real env vars win."""
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
    # CF_API_KEY + INSTANCE_PATH via packenv (single source: process env > .env.local > .env),
    # so this checker resolves them the SAME way as the rest of the tooling.
    if E.cf_api_key():
        conf["CF_API_KEY"] = E.cf_api_key()
    conf["INSTANCE_PATH"] = E.INSTANCE_TEST
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


def load_bundles(instance):
    base = os.path.join(instance, "config", "mod-director")
    out = {}
    for name, key in (("curse.bundle.json", "curse"), ("url.bundle.json", "url"),
                      ("modrinth.bundle.json", "modrinth")):
        path = os.path.join(base, name)
        out[key] = json.load(open(path)).get(key, []) if os.path.isfile(path) else []
    return out


# --- offline: jar build dates from zip entry timestamps ---

def jar_build_date(path):
    try:
        with zipfile.ZipFile(path) as z:
            newest = max((i.date_time for i in z.infolist()), default=None)
        if not newest or newest[0] < 1990:
            return None
        return datetime.datetime(*newest)
    except Exception:
        return None


def rank_zip_dates(instance):
    mods_dir = os.path.join(instance, "mods")
    rows = []
    for f in sorted(os.listdir(mods_dir)):
        if not f.endswith(".jar"):
            continue
        d = jar_build_date(os.path.join(mods_dir, f))
        if d:
            rows.append({"jar": f, "builtAt": d.isoformat(sep=" ")})
    rows.sort(key=lambda r: r["builtAt"])
    return rows


# --- GitHub: latest release per repo referenced by url.bundle.json ---

def gh_latest(owner_repo):
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/releases/latest", "--jq",
             "{tag: .tag_name, date: .published_at}"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {"error": out.stderr.strip().splitlines()[0] if out.stderr else "gh error"}
        return json.loads(out.stdout)
    except Exception as e:
        return {"error": str(e)}


def check_github(urls):
    # One check per repo; a repo may ship several jars of the same release.
    repos = {}
    for entry in urls:
        m = GH_URL_RE.search(entry.get("url", ""))
        if not m:
            continue
        owner, repo, tag, jar = m.groups()
        repos.setdefault(f"{owner}/{repo}", {"installedTag": tag, "jars": []})["jars"].append(jar)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(gh_latest, r): r for r in repos}
        for fut in concurrent.futures.as_completed(futures):
            repo = futures[fut]
            latest = fut.result()
            row = {"repo": repo, **repos[repo], **{"latest": latest}}
            row["updateAvailable"] = (
                "error" not in latest and latest.get("tag")
                and latest["tag"].lstrip("v") != repos[repo]["installedTag"].lstrip("v"))
            results.append(row)
    results.sort(key=lambda r: (not r["updateAvailable"], r["repo"].lower()))
    return results


# --- CurseForge: installed file dates + latest 1.7.10 file per addon ---

def check_curse(entries, api_key):
    headers = {"x-api-key": api_key}
    mod_ids = sorted({e["addonId"] for e in entries})
    file_ids = sorted({e["fileId"] for e in entries})
    mods = {}
    for i in range(0, len(mod_ids), 50):
        for m in http_json(f"{CF_API}/v1/mods", "POST", {"modIds": mod_ids[i:i + 50]},
                           headers)["data"]:
            mods[m["id"]] = m
    installed = {}
    for i in range(0, len(file_ids), 50):
        for f in http_json(f"{CF_API}/v1/mods/files", "POST", {"fileIds": file_ids[i:i + 50]},
                           headers)["data"]:
            installed[f["id"]] = f
    rows = []
    for e in entries:
        mod = mods.get(e["addonId"])
        inst = installed.get(e["fileId"])
        latest = None
        if mod:
            for idx in mod.get("latestFilesIndexes", []):
                if idx.get("gameVersion") == "1.7.10":
                    latest = idx
                    break
        row = {
            "addonId": e["addonId"],
            "name": mod["name"] if mod else e.get("fileName", "?"),
            "installedFileId": e["fileId"],
            "installedFile": e.get("fileName"),
            "installedDate": inst.get("fileDate") if inst else None,
            "latestFile": latest.get("filename") if latest else None,
            "latestFileId": latest.get("fileId") if latest else None,
        }
        row["updateAvailable"] = bool(latest) and latest.get("fileId") != e["fileId"]
        rows.append(row)
    rows.sort(key=lambda r: r["installedDate"] or "9999")
    return rows


# --- apply: bump third-party curse.bundle fileIds (Approved-only, format-preserving) ---

def _byte_replace(data, old, new, label, errors):
    """Format-preserving byte substring replace (mirrors pack_sync.replace_value): operates on BYTES
    so CRLF line endings survive — a text read/write would normalize CRLF->LF and rewrite the whole
    file. Requires the old value to occur EXACTLY once. Returns (new_data, changed?)."""
    ob, nb = old.encode("utf-8"), new.encode("utf-8")
    if ob == nb:
        return data, False
    n = data.count(ob)
    if n != 1:
        errors.append(f"{label}: {old!r} found {n}x (need exactly 1) — skipped")
        return data, False
    return data.replace(ob, nb), True


def apply_curse_updates(bundle_path, rows, api_key, do_apply):
    """For each curse.bundle entry with an update, byte-edit fileId (+fileName) to the latest.
    GUARD (BUG-023): only write a fileId whose CF fileStatus == 4 (Approved) — a pending/rejected
    file must never enter the bundle. Preview by default; writes only with do_apply."""
    updates = [r for r in rows if r.get("updateAvailable") and r.get("latestFileId")]
    print(f"\n== curse.bundle apply {'[WRITE]' if do_apply else '[PREVIEW]'} "
          f"({len(updates)} candidate update(s)) ==")
    if not updates:
        print("  nothing to bump.")
        return
    # Approved-only guard: fetch the actual status of each latest file (latestFilesIndexes lacks it).
    latest_ids = sorted({r["latestFileId"] for r in updates})
    details = {}
    headers = {"x-api-key": api_key}
    for i in range(0, len(latest_ids), 50):
        for f in http_json(f"{CF_API}/v1/mods/files", "POST",
                           {"fileIds": latest_ids[i:i + 50]}, headers)["data"]:
            details[f["id"]] = f

    data = open(bundle_path, "rb").read()
    errors, applied = [], 0
    for r in updates:
        addon, old_fid, new_fid = r["addonId"], r["installedFileId"], r["latestFileId"]
        det = details.get(new_fid)
        status = det.get("fileStatus") if det else None
        if status != 4:  # 4 = Approved; anything else must not ship (BUG-023)
            print(f"  SKIP addonId {addon}: latest file {new_fid} fileStatus={status} "
                  f"(not Approved) — refusing")
            continue
        new_fn = det.get("fileName") or r.get("latestFile")
        old_fn = r.get("installedFile")
        data, c1 = _byte_replace(data, f'"fileId": {old_fid}', f'"fileId": {new_fid}',
                                 f"addonId {addon} fileId", errors)
        c2 = False
        if old_fn and new_fn and old_fn != new_fn:
            data, c2 = _byte_replace(data, f'"fileName": "{old_fn}"', f'"fileName": "{new_fn}"',
                                     f"addonId {addon} fileName", errors)
        if c1 or c2:
            print(f"  addonId {addon}: {old_fid} -> {new_fid} ({new_fn})")
            applied += 1
    for e in errors:
        print(f"  ! {e}")
    if do_apply and applied:
        open(bundle_path, "wb").write(data)
        print(f"  WROTE {applied} bump(s) to {bundle_path}")
    elif applied:
        print(f"  {applied} bump(s) ready — re-run with --apply to write "
              "(canonical pack bundle = point --instance at the pack's src/common).")


# --- Modrinth ---

def check_modrinth(entries):
    rows = []
    for e in entries:
        slug = e["addonId"]
        try:
            versions = http_json(
                f"{MODRINTH_API}/project/{slug}/version"
                f"?game_versions=%5B%221.7.10%22%5D&loaders=%5B%22forge%22%5D")
            latest = versions[0] if versions else None
            rows.append({
                "project": slug,
                "installed": e.get("fileId"),
                "latest": latest["version_number"] if latest else None,
                "latestDate": latest["date_published"] if latest else None,
                "updateAvailable": bool(latest) and latest["version_number"] != e.get("fileId"),
            })
        except Exception as ex:
            rows.append({"project": slug, "error": str(ex)})
    return rows


def main():
    env = load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default=env.get("INSTANCE_PATH"))
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", help="write full JSON report here")
    ap.add_argument("--skip-github", action="store_true")
    ap.add_argument("--skip-curse", action="store_true")
    ap.add_argument("--skip-zipdates", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write detected third-party curse.bundle fileId bumps (Approved-only) into "
                         "the instance's curse.bundle.json (byte edit, CRLF-safe). Without it: preview.")
    args = ap.parse_args()
    if not args.instance:
        sys.exit("Instance non configurée : --instance PATH ou INSTANCE_PATH dans .env/.env.local")
    if not os.path.isdir(args.instance):
        sys.exit(f"Instance introuvable : {args.instance}")

    bundles = load_bundles(args.instance)
    report = {"instance": args.instance,
              "generatedAt": datetime.datetime.now().isoformat(sep=" ", timespec="seconds")}

    if not args.skip_zipdates:
        report["oldestJarsByBuildDate"] = rank_zip_dates(args.instance)
        print(f"== {args.top} plus vieux jars (date de build zip, offline) ==")
        for r in report["oldestJarsByBuildDate"][:args.top]:
            print(f"  {r['builtAt']}  {r['jar']}")

    if not args.skip_github and bundles["url"]:
        print(f"\n== GitHub ({len(bundles['url'])} URLs) ==")
        report["github"] = check_github(bundles["url"])
        for r in report["github"]:
            if r["updateAvailable"]:
                print(f"  UPDATE {r['repo']}: {r['installedTag']} -> {r['latest'].get('tag')}"
                      f" ({(r['latest'].get('date') or '')[:10]})")
        errs = [r for r in report["github"] if "error" in r.get("latest", {})]
        ups = [r for r in report["github"] if r["updateAvailable"]]
        print(f"  {len(ups)} updates, {len(errs)} repos sans release lisible")

    key = env.get("CF_API_KEY")
    if not args.skip_curse and bundles["curse"]:
        if key:
            print(f"\n== CurseForge ({len(bundles['curse'])} addons) ==")
            try:
                report["curse"] = check_curse(bundles["curse"], key)
                ups = [r for r in report["curse"] if r["updateAvailable"]]
                print(f"  {len(ups)} updates disponibles ; plus vieux installés :")
                for r in report["curse"][:args.top]:
                    print(f"  {(r['installedDate'] or '?')[:10]}  {r['name']}  ({r['installedFile']})")
                bundle_path = os.path.join(args.instance, "config", "mod-director", "curse.bundle.json")
                apply_curse_updates(bundle_path, report["curse"], key, args.apply)
            except urllib.error.HTTPError as e:
                print(f"  ERREUR API CurseForge: HTTP {e.code} — clé invalide/inactive ?"
                      " Vérifier sur console.curseforge.com (la clé doit être active,"
                      " copiée entière, sans guillemets).")
        else:
            print("\n== CurseForge: CF_API_KEY absent -> lookups sautés "
                  "(clé gratuite: console.curseforge.com) ==")

    if bundles["modrinth"]:
        report["modrinth"] = check_modrinth(bundles["modrinth"])

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1, default=str)
        print(f"\nJSON: {args.json}")


if __name__ == "__main__":
    main()
