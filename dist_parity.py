#!/usr/bin/env python3
"""
dist_parity.py — GitHub vs Modrinth/CurseForge release-parity checker for the personal 1.7.10 mods.

THE POINT: find PERSONAL mods where GitHub has a release but a Modrinth and/or CurseForge project
ALREADY EXISTS yet is BEHIND (players there get a stale/no version) — the cross-platform distribution
gap — OR the platform isn't tracked in the release manifest (the pipeline can't ship it). Read-only:
it PUBLISHES NOTHING, only reports.

Data sources (read first, nothing hardcoded):
  - Mod-Sandbox/memory/repos.json          — the personal mod universe (is_mod repos, GitHub slugs)
  - Mod-Sandbox/memory/release-manifest.json — declared curseforge.project_id / modrinth.project
  - ModsandModpackMinecraftHub/**/1.7.10.MD  — the user's hand-maintained hub list of which platforms
    each mod is published on (widens coverage to mods not cloned locally; TRUST THE URL HOST, not the
    bracket label, since links are sometimes mislabeled and per-platform incomplete).

Live APIs (batched + cached per run, polite User-Agent):
  - Modrinth : GET /v2/user/quentin452/projects, then /v2/project/<slug>/version (latest published)
  - CurseForge: /v1/mods/search?gameId=432&authorId=<id> (authorId pulled from a known mod), reused
                across all owned projects (latest file per project)
  - GitHub   : `gh release view -R <slug>` (latest release tag), one per unique repo (threaded)

Flags per mod:
  GAP        — GH has a release AND a Modrinth/CF project EXISTS but its latest < GH (or zero versions).
               The headline; names WHICH platform is behind.
  UNMANAGED  — a Modrinth/CF project EXISTS but the mod is NOT in release-manifest.json (pipeline gap).
  GH_ONLY    — GH release but no Modrinth AND no CF project (informational; maybe intentional).
  OK         — every platform that exists is at parity with GH.
Plus a separate HUB_LIST_DRIFT signal (maintenance aid for keeping 1.7.10.MD honest):
  a mod is live on a platform the .MD does NOT list, or a .MD link is dead/mislabeled.

Usage:
  dist_parity.py [--json] [--gh-workers N] [--no-gh]
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Reuse the SINGLE source of truth for a version's Modrinth form (strip a leading V) so V1.2.3 /
# 1.2.3 / 1.2.3-fork4 all compare on equal footing — do not re-invent version parsing.
sys.path.insert(0, SCRIPT_DIR)
from release_mods import normalize_modrinth_version  # noqa: E402

HUB = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory")
REPOS_JSON = os.path.join(HUB, "repos.json")
MANIFEST_JSON = os.path.join(HUB, "release-manifest.json")
HUB_MD_DIR = os.path.expanduser("~/Documents/GitHub/ModsandModpackMinecraftHub")
HUB_MD_FILES = [
    os.path.join(HUB_MD_DIR, "Mods", "1.7.10.MD"),
    os.path.join(HUB_MD_DIR, "Modpacks", "1.7.10.MD"),
    os.path.join(HUB_MD_DIR, "Resourcepack", "1.7.10.MD"),
]

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_USER = "quentin452"
CF_API = "https://api.curseforge.com"
CF_GAME_ID = 432               # Minecraft
UA = "dist-parity/1.0 (personal-mod release-parity checker)"


# ---------------------------------------------------------------------------- env / http helpers

def load_cf_key():
    """CF read key (CF_API_KEY) from .env/.env.local — same convention as bundle_check/cf_upload."""
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
    return os.environ.get("CF_API_KEY") or conf.get("CF_API_KEY")


def http_json(url, headers=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# ---------------------------------------------------------------------------- version comparison

_VER_TOKEN = re.compile(r"[Vv]?\d+(?:\.\d+)+[A-Za-z0-9._-]*")
_EXTRA_RE = re.compile(r"(?:fork|hotfix|hf|beta|b|rc|alpha|a)\s*[-_.]?\s*(\d+)", re.I)


def ver_key(raw):
    """Best-effort comparable key for a version string, or None if unparseable (caller then treats
    the comparison as UNKNOWN rather than falsely flagging a gap). Strips the MC game version noise
    ('1.7.10' embedded in CF displayNames like 'Tropicraft 1.7.10 V6.1.3'), reuses the shared
    leading-V strip, then keys on the dotted-number core + a fork/hotfix ordinal tiebreak."""
    s = re.sub(r"1\.7\.10", " ", str(raw))
    toks = _VER_TOKEN.findall(s)
    if toks:
        tok = toks[-1]                      # last dotted token — the name may carry earlier numbers
    else:
        m = re.search(r"[Vv]?\d+[A-Za-z0-9._-]*", s)   # single-number fallback ('V0.2')
        if not m:
            return None
        tok = m.group(0)
    core = normalize_modrinth_version(tok)
    m = re.match(r"(\d+(?:\.\d+)*)(.*)", core)
    if not m:
        return None
    nums = tuple(int(x) for x in m.group(1).split("."))
    em = _EXTRA_RE.search(m.group(2))
    extra = int(em.group(1)) if em else 0
    return (nums, extra)


def ver_cmp(a, b):
    """-1/0/1 for a<b / a==b / a>b, or None if either side is unparseable (UNKNOWN)."""
    ka, kb = ver_key(a), ver_key(b)
    if ka is None or kb is None:
        return None
    na, nb = list(ka[0]), list(kb[0])
    n = max(len(na), len(nb))
    na += [0] * (n - len(na))
    nb += [0] * (n - len(nb))
    ta, tb = (tuple(na), ka[1]), (tuple(nb), kb[1])
    return (ta > tb) - (ta < tb)


# ---------------------------------------------------------------------------- name normalization

def norm(s):
    """Identity key for fuzzy matching: lowercase, drop the MC version + separators/spaces. Keeps
    'continuation' (embedded in Modrinth slugs) since the dash is merely stripped, not the word."""
    s = str(s).lower()
    s = re.sub(r"1\.7\.10", "", s)
    s = re.sub(r"[\s_\-./]+", "", s)
    return s


def gh_basename(slug):
    return slug.rsplit("/", 1)[-1] if slug else None


# ---------------------------------------------------------------------------- hub .MD parsing

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_ENTRY_SPLIT = re.compile(r"\s*:\s*|\s+(?=\[)")


def _host_platform(url):
    u = url.lower()
    if "curseforge.com" in u:
        return "CurseForge"
    if "github.com" in u:
        return "GitHub"
    if "modrinth.com" in u:
        return "Modrinth"
    return None


def _slug_from_url(url, platform):
    path = re.sub(r"[#?].*$", "", url.rstrip("/"))
    parts = [p for p in path.split("/") if p]
    if platform == "GitHub":
        # github.com/<owner>/<repo>
        return "/".join(parts[-2:]) if len(parts) >= 2 else None
    # curseforge / modrinth: last path segment is the slug
    return parts[-1] if parts else None


def parse_hub_md():
    """Return list of {name, links:[{platform, label_platform, slug, url}]}. Trusts the URL HOST for
    the platform; keeps the bracket LABEL separately so a label/host mismatch = a drift signal."""
    entries = []
    for path in HUB_MD_FILES:
        if not os.path.isfile(path):
            continue
        for raw in open(path):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("**"):
                continue
            links = list(_LINK_RE.finditer(line))
            if not links:
                continue
            name = line[: links[0].start()].strip().rstrip(":").strip()
            # strip a trailing "[1.7.10]" tag some names carry before the link
            name = re.sub(r"\[[^\]]*\]\s*$", "", name).strip()
            if not name:
                continue
            parsed = []
            for m in links:
                label, url = m.group(1), m.group(2)
                platform = _host_platform(url)      # ground truth
                if not platform:
                    continue
                parsed.append({
                    "platform": platform,
                    "label_platform": _host_platform("x " + label) or label,
                    "slug": _slug_from_url(url, platform),
                    "url": url,
                })
            if parsed:
                entries.append({"name": name, "links": parsed})
    return entries


# ---------------------------------------------------------------------------- live data caches

def fetch_modrinth_projects():
    """{slug: {title, versions_count, latest_ver, latest_date}} for the user's owned Modrinth
    projects. version_number of the newest by date_published (one /version call per project)."""
    projects = http_json(f"{MODRINTH_API}/user/{MODRINTH_USER}/projects")
    out = {}
    for p in projects:
        slug = p.get("slug")
        rec = {"title": p.get("title"), "versions_count": len(p.get("versions", [])),
               "latest_ver": None, "latest_date": None}
        if rec["versions_count"]:
            try:
                vers = http_json(f"{MODRINTH_API}/project/{slug}/version")
                if vers:
                    newest = max(vers, key=lambda v: v.get("date_published", ""))
                    rec["latest_ver"] = newest.get("version_number")
                    rec["latest_date"] = newest.get("date_published")
            except Exception as e:
                rec["error"] = str(e)
        out[slug] = rec
    return out


def fetch_cf_author_projects(cf_key):
    """{slug: {id, latest_ver, latest_date}} for the CF member. authorId is pulled from a KNOWN
    project's authors[].id, then the search API is paged over. Returns (map, author_id, note)."""
    hdr = {"x-api-key": cf_key}
    author_id = None
    # find authorId via any manifest-declared CF project id (OaT 855466 is a safe seed)
    seed_ids = _manifest_cf_ids() or [855466]
    for pid in seed_ids:
        try:
            d = http_json(f"{CF_API}/v1/mods/{pid}", hdr)["data"]
            auths = d.get("authors", [])
            if auths:
                author_id = auths[0].get("id")
                break
        except Exception:
            continue
    out = {}
    if author_id is None:
        return out, None, "authorId lookup FAILED — falling back to declared CF ids only (TODO)"
    index = 0
    while True:
        url = (f"{CF_API}/v1/mods/search?gameId={CF_GAME_ID}&authorId={author_id}"
               f"&pageSize=50&index={index}")
        d = http_json(url, hdr)
        for m in d.get("data", []):
            out[m.get("slug")] = _cf_latest(m, m.get("id"))
        pg = d.get("pagination", {})
        got = pg.get("index", 0) + pg.get("resultCount", 0)
        if got >= pg.get("totalCount", 0) or not d.get("data"):
            break
        index = got
    return out, author_id, f"authorId={author_id}, {len(out)} owned CF projects"


def _cf_latest(mod, pid):
    """Latest file of a CF project = newest by fileDate among latestFiles (they are NOT sorted)."""
    files = mod.get("latestFiles") or []
    rec = {"id": pid, "slug": mod.get("slug"), "latest_ver": None, "latest_date": None}
    if files:
        newest = max(files, key=lambda f: f.get("fileDate", ""))
        rec["latest_ver"] = newest.get("displayName") or newest.get("fileName")
        rec["latest_date"] = newest.get("fileDate")
    return rec


def cf_fetch_one(pid, cf_key):
    try:
        d = http_json(f"{CF_API}/v1/mods/{pid}", {"x-api-key": cf_key})["data"]
        return _cf_latest(d, pid)
    except Exception:
        return None


_MANIFEST = None


def _load_manifest():
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.load(open(MANIFEST_JSON)).get("mods", [])
    return _MANIFEST


def _manifest_cf_ids():
    ids = []
    for m in _load_manifest():
        cf = m.get("curseforge") or {}
        if cf.get("project_id"):
            ids.append(cf["project_id"])
    return ids


def gh_latest(slug):
    """Latest GitHub release tag for a repo, or None if no release / repo missing."""
    try:
        r = subprocess.run(["gh", "release", "view", "-R", slug, "--json", "tagName,name"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        d = json.loads(r.stdout)
        return d.get("tagName") or d.get("name") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------- universe assembly

def build_universe():
    """Union repos.json (is_mod) + release-manifest + hub .MD into records keyed by a canonical id
    (the GitHub repo basename when known, else a platform slug / display name)."""
    records = {}

    def rec(key):
        return records.setdefault(key, {
            "key": key, "name": None, "gh_slug": None, "cf_id": None, "cf_slug": None,
            "mr_slug": None, "in_manifest": False, "sources": set(),
            "hub_platforms": {}, "hub_mislabel": [],
        })

    # 1) repos.json is_mod repos (GitHub is the release source)
    repos = json.load(open(REPOS_JSON)).get("repos", [])
    name_to_key = {}
    for r in repos:
        if not r.get("is_mod"):
            continue
        slug = r.get("slug")
        base = gh_basename(slug)
        key = norm(base)
        rr = rec(key)
        rr["name"] = rr["name"] or base
        rr["gh_slug"] = slug
        rr["in_manifest"] = rr["in_manifest"] or bool(r.get("in_release_manifest"))
        rr["sources"].add("repos")
        name_to_key[norm(base)] = key
        name_to_key[norm(r.get("name", ""))] = key

    # 2) release-manifest declarations (cf id / modrinth slug / in_manifest)
    for m in _load_manifest():
        name = m.get("name", "")
        base = gh_basename(m.get("repo", "").rstrip("/"))
        key = name_to_key.get(norm(name)) or name_to_key.get(norm(base)) or norm(name)
        rr = rec(key)
        rr["name"] = rr["name"] or name
        rr["in_manifest"] = True
        rr["sources"].add("manifest")
        cf = m.get("curseforge") or {}
        if cf.get("project_id"):
            rr["cf_id"] = cf["project_id"]
        mr = m.get("modrinth") or {}
        if mr.get("project"):
            rr["mr_slug"] = mr["project"]
        name_to_key.setdefault(norm(name), key)

    # 3) hub .MD (widens coverage; declares which platforms each mod is *listed* on)
    for e in parse_hub_md():
        gh_link = next((l for l in e["links"] if l["platform"] == "GitHub"), None)
        cf_link = next((l for l in e["links"] if l["platform"] == "CurseForge"), None)
        mr_link = next((l for l in e["links"] if l["platform"] == "Modrinth"), None)
        # choose the canonical key: prefer an existing repo record via github basename or name
        key = None
        if gh_link and gh_link["slug"]:
            key = name_to_key.get(norm(gh_basename(gh_link["slug"]))) or norm(gh_basename(gh_link["slug"]))
        if key is None:
            key = name_to_key.get(norm(e["name"]))
        if key is None and cf_link and cf_link["slug"]:
            key = norm(cf_link["slug"])
        if key is None:
            key = norm(e["name"])
        rr = rec(key)
        rr["name"] = rr["name"] or e["name"]
        rr["sources"].add("hub")
        if gh_link and gh_link["slug"] and not rr["gh_slug"]:
            rr["gh_slug"] = gh_link["slug"]
        if cf_link and cf_link["slug"] and not rr["cf_slug"]:
            rr["cf_slug"] = cf_link["slug"]
        if mr_link and mr_link["slug"] and not rr["mr_slug"]:
            rr["mr_slug"] = mr_link["slug"]
        for l in e["links"]:
            rr["hub_platforms"][l["platform"]] = l["slug"]
            if l["label_platform"] in ("CurseForge", "GitHub", "Modrinth") and l["label_platform"] != l["platform"]:
                rr["hub_mislabel"].append(f"{l['label_platform']}->{l['platform']}({l['slug']})")

    return records


# ---------------------------------------------------------------------------- resolution + flags

def resolve(records, mr_cache, cf_cache, cf_key, do_gh, gh_workers):
    # resolve GH versions concurrently (one per unique repo)
    gh_slugs = sorted({r["gh_slug"] for r in records.values() if r["gh_slug"]})
    gh_ver = {}
    if do_gh and gh_slugs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=gh_workers) as ex:
            for slug, v in zip(gh_slugs, ex.map(gh_latest, gh_slugs)):
                gh_ver[slug] = v

    cf_by_id = {v["id"]: v for v in cf_cache.values() if v.get("id")}
    rows = []
    for r in records.values():
        name = r["name"] or r["key"]
        gver = gh_ver.get(r["gh_slug"]) if r["gh_slug"] else None

        # --- Modrinth resolution
        mr_slug, mr_match = r["mr_slug"], None
        if mr_slug and mr_slug in mr_cache:
            mr_match = "declared"
        elif mr_slug is None:
            for slug in mr_cache:
                if norm(slug) in (norm(gh_basename(r["gh_slug"]) or ""), norm(name)):
                    mr_slug, mr_match = slug, "fuzzy"
                    break
        elif mr_slug not in mr_cache:
            mr_match = "declared-missing"
        mr_present = bool(mr_slug and mr_slug in mr_cache)
        mr_info = mr_cache.get(mr_slug) if mr_present else None
        mr_ver = mr_info["latest_ver"] if mr_info else None

        # --- CurseForge resolution
        cf_match, cf_info = None, None
        if r["cf_id"] and r["cf_id"] in cf_by_id:
            cf_info, cf_match = cf_by_id[r["cf_id"]], "declared-id"
        elif r["cf_id"]:
            cf_info = cf_fetch_one(r["cf_id"], cf_key)     # declared but outside author search
            cf_match = "declared-id-direct" if cf_info else "declared-id-missing"
        elif r["cf_slug"] and r["cf_slug"] in cf_cache:
            cf_info, cf_match = cf_cache[r["cf_slug"]], "declared-slug"
        elif r["cf_slug"]:
            cf_match = "declared-slug-missing"
        else:
            for slug, info in cf_cache.items():
                if norm(slug) in (norm(gh_basename(r["gh_slug"]) or ""), norm(name)):
                    cf_info, cf_match = info, "fuzzy"
                    break
        cf_present = bool(cf_info)
        cf_ver = cf_info["latest_ver"] if cf_info else None

        # --- parity flags
        gaps, unknown = [], []
        if gver:
            for plat, present, ver in (("Modrinth", mr_present, mr_ver), ("CurseForge", cf_present, cf_ver)):
                if not present:
                    continue
                if ver is None:
                    gaps.append(f"{plat}:no-versions")
                    continue
                c = ver_cmp(ver, gver)
                if c is None:
                    unknown.append(f"{plat}:{ver}?{gver}")
                elif c < 0:
                    gaps.append(f"{plat}:{ver}<{gver}")
        unmanaged = (mr_present or cf_present) and not r["in_manifest"]
        gh_only = bool(gver) and not mr_present and not cf_present
        if gaps:
            flag = "GAP"
        elif unmanaged:
            flag = "UNMANAGED"
        elif gh_only:
            flag = "GH_ONLY"
        else:
            flag = "OK"

        # --- hub list drift (maintenance aid)
        drift = list(r["hub_mislabel"])
        hub = r["hub_platforms"]
        if hub:                                            # only for mods present in the .MD
            live = {"GitHub": bool(gver), "Modrinth": mr_present, "CurseForge": cf_present}
            for plat, is_live in live.items():
                if is_live and plat not in hub:
                    drift.append(f"live-on-{plat}-not-listed")
            for plat, slug in hub.items():                 # declared slug not found among owned
                if plat == "Modrinth" and slug and slug not in mr_cache:
                    drift.append(f"MD-Modrinth-link-not-owned({slug})")
                if plat == "CurseForge" and slug and slug not in cf_cache and not r["cf_id"]:
                    drift.append(f"MD-CF-link-not-owned({slug})")

        rows.append({
            "name": name, "key": r["key"], "gh_slug": r["gh_slug"],
            "gh": gver, "mr": mr_ver, "cf": cf_ver,
            "mr_present": mr_present, "cf_present": cf_present,
            "mr_slug": mr_slug, "mr_match": mr_match,
            "cf_slug": (cf_info or {}).get("slug") if cf_info else r["cf_slug"], "cf_match": cf_match,
            "in_manifest": r["in_manifest"], "sources": sorted(r["sources"]),
            "flag": flag, "gaps": gaps, "unknown": unknown,
            "unmanaged": unmanaged, "hub_drift": drift,
        })
    return rows


# ---------------------------------------------------------------------------- output

def cell(present, ver, has_gh):
    if ver:
        return ver
    if present:
        return "—(0 ver)"
    return "absent"


FLAG_ORDER = {"GAP": 0, "UNMANAGED": 1, "GH_ONLY": 2, "OK": 3}


def print_table(rows):
    rows = sorted(rows, key=lambda r: (FLAG_ORDER.get(r["flag"], 9), r["name"].lower()))
    w = max((len(r["name"]) for r in rows), default=4)
    print(f"\n{'MOD'.ljust(w)}  {'FLAG'.ljust(9)}  {'GH'.ljust(14)}  {'MODRINTH'.ljust(14)}  {'CF'.ljust(14)}  NOTES")
    print("-" * (w + 74))
    for r in rows:
        gh = r["gh"] or "—"
        mr = cell(r["mr_present"], r["mr"], r["gh"])
        cf = cell(r["cf_present"], r["cf"], r["gh"])
        notes = []
        if r["gaps"]:
            notes.append("BEHIND " + ", ".join(r["gaps"]))
        if r["unmanaged"] and r["flag"] != "UNMANAGED":
            notes.append("also-unmanaged")
        if r["unknown"]:
            notes.append("cmp? " + ",".join(r["unknown"]))
        if r["mr_match"] == "fuzzy":
            notes.append(f"~mr={r['mr_slug']}")
        if r["cf_match"] == "fuzzy":
            notes.append(f"~cf={r['cf_slug']}")
        if r["hub_drift"]:
            notes.append("DRIFT: " + "; ".join(r["hub_drift"]))
        print(f"{r['name'].ljust(w)}  {r['flag'].ljust(9)}  {gh.ljust(14)}  {mr.ljust(14)}  {cf.ljust(14)}  {' | '.join(notes)}")


def print_summary(rows, meta):
    counts = {}
    for r in rows:
        counts[r["flag"]] = counts.get(r["flag"], 0) + 1
    drift_rows = [r for r in rows if r["hub_drift"]]
    print("\n=== SUMMARY ===")
    for f in ("GAP", "UNMANAGED", "GH_ONLY", "OK"):
        print(f"  {f:<10} {counts.get(f, 0)}")
    print(f"  HUB_DRIFT  {len(drift_rows)}  (separate signal, not a primary flag)")
    print(f"  total mods {len(rows)}")
    print(f"\n  hub .MD added {meta['hub_added']} mods beyond repos.json+manifest")
    print(f"  CF coverage: {meta['cf_note']}")
    print(f"  Modrinth: {meta['mr_count']} owned projects")
    gap = [r for r in rows if r["flag"] == "GAP"]
    unm = [r for r in rows if r["flag"] == "UNMANAGED"]
    if gap:
        print("\n  GAP mods (cross-post needed):")
        for r in gap:
            print(f"    - {r['name']}: {', '.join(r['gaps'])}")
    if unm:
        print("\n  UNMANAGED mods (project exists, not in manifest):")
        for r in unm:
            plats = []
            if r["mr_present"]:
                plats.append("Modrinth")
            if r["cf_present"]:
                plats.append("CF")
            print(f"    - {r['name']} [{'/'.join(plats)}]")
    if drift_rows:
        print("\n  HUB_LIST_DRIFT (keep 1.7.10.MD honest):")
        for r in drift_rows:
            print(f"    - {r['name']}: {'; '.join(r['hub_drift'])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable JSON instead of the table")
    ap.add_argument("--gh-workers", type=int, default=8, help="parallel `gh release view` workers (default 8)")
    ap.add_argument("--no-gh", action="store_true", help="skip GitHub queries (faster; disables GAP/GH_ONLY)")
    args = ap.parse_args()

    cf_key = load_cf_key()
    if not cf_key:
        print("[warn] no CF_API_KEY — CurseForge coverage disabled", file=sys.stderr)

    if not args.json:
        print("[dist_parity] fetching Modrinth user projects…", file=sys.stderr)
    mr_cache = fetch_modrinth_projects()
    cf_cache, cf_author, cf_note = ({}, None, "disabled (no CF_API_KEY)")
    if cf_key:
        if not args.json:
            print("[dist_parity] fetching CurseForge author projects…", file=sys.stderr)
        cf_cache, cf_author, cf_note = fetch_cf_author_projects(cf_key)

    records = build_universe()
    hub_only = sum(1 for r in records.values() if r["sources"] == {"hub"})
    if not args.json:
        print(f"[dist_parity] {len(records)} mods in universe; querying GitHub…", file=sys.stderr)
    rows = resolve(records, mr_cache, cf_cache, cf_key, not args.no_gh, args.gh_workers)

    meta = {"hub_added": hub_only, "cf_note": cf_note, "mr_count": len(mr_cache), "cf_author": cf_author}
    if args.json:
        print(json.dumps({"meta": meta, "mods": rows}, indent=2))
    else:
        print_table(rows)
        print_summary(rows, meta)


if __name__ == "__main__":
    main()
