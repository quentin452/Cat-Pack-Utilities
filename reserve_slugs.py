#!/usr/bin/env python3
"""
reserve_slugs.py — reserve the clean-room project slugs (docs/26) on Modrinth + CurseForge.

Reserving a slug = creating the project so nobody else can take the name. This is an OUTWARD
action: dry-run is the default, --execute is required to actually create anything.

Platform reality:
  * Modrinth — SCRIPTABLE. POST /v2/project creates the project as a DRAFT (is_draft=true):
    unpublished, editable, the slug is claimed. Uses MODRINTH_TOKEN (packenv, scope: "Create
    projects" + "Read user data"). Idempotent: an existing slug is left untouched.
  * CurseForge — NOT SCRIPTABLE. CF has no create-project API (the upload API only adds files to
    an EXISTING project). So this script emits a manual CHECKLIST for the CF console and, once you
    create the projects there, you paste their numeric project ids back (--cf-id NAME=12345) to
    wire the manifest.

The canonical copy (title / summary / body / categories / license) lives in PROJECTS below — this
is the single source of truth reused by the wiki, dist_parity, and future releases. License = MIT
(docs/26: our mods MIT + permissive deps → clean redistribution).

Usage:
  reserve_slugs.py                       # dry-run: show what WOULD be created on each platform
  reserve_slugs.py --verify              # token valid? which slugs already exist on Modrinth?
  reserve_slugs.py --execute             # create the missing Modrinth drafts + set their icons (outward)
  reserve_slugs.py --icons               # (re)upload the Modrinth icon of EXISTING projects (outward)
  reserve_slugs.py --icon pack=logo.png  # override an icon file (default assets/brand/<slug>.png)
  reserve_slugs.py --cf-checklist        # print the manual CurseForge creation checklist (+ avatar path)
  reserve_slugs.py --cf-id gigafauna=123456 --cf-id matoulib=123457 --wire-manifest
                                         # after manual CF creation: record ids into release-manifest
  reserve_slugs.py --only gigafauna      # restrict to one project (repeatable)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
import packenv as E  # noqa: E402 — single source for secrets/paths

API = "https://api.modrinth.com/v2"

# ── Canonical project copy (source of truth — docs/26). Keyed by internal name. ──────────────────
# license_id: MIT (SPDX). client_side/server_side: both mods touch rendering + entities, so both
# "required". project_type: mod / modpack. categories: Modrinth's controlled vocabulary.
PROJECTS = {
    "gigafauna": {
        "slug": "gigafauna",
        "title": "Gigafauna",
        "project_type": "mod",
        "categories": ["mobs", "adventure"],
        "client_side": "required",
        "server_side": "required",
        "summary": "Clean-room 1.7.10 creature mod — data-driven, GeckoLib-animated mobs built on matoulib.",
        "body": (
            "# Gigafauna\n\n"
            "A clean-room creature mod for Minecraft 1.7.10. Mobs are **data-driven** and animated "
            "with GeckoLib, built on top of the [matoulib](https://modrinth.com/mod/matoulib) engine.\n\n"
            "Everything here is our own content — no third-party assets — so it ships freely on both "
            "Modrinth and CurseForge.\n\n"
            "## Requirements\n"
            "- Minecraft 1.7.10 (Forge)\n"
            "- matoulib\n"
            "- GeckoLib (1.7.10 backport)\n\n"
            "_More content and details coming as the mod grows._\n"
        ),
        # CurseForge-side hints for the manual checklist:
        "cf_category": "Mobs",
        "cf_type": "Mod",
    },
    "matoulib": {
        "slug": "matoulib",
        "title": "matoulib",
        "project_type": "mod",
        "categories": ["library"],
        "client_side": "required",
        "server_side": "required",
        "summary": "Data-driven content & rendering engine for 1.7.10 (entities, fluids, GPU skinning). Library for Gigafauna.",
        "body": (
            "# matoulib\n\n"
            "A **data-driven content and rendering engine** for Minecraft 1.7.10. Provides layered "
            "systems — entities, fluids, animated textures, GPU skinning — that let content mods "
            "declare features as data instead of code.\n\n"
            "This is a **library**: it is a dependency of "
            "[Gigafauna](https://modrinth.com/mod/gigafauna), not a standalone mod.\n\n"
            "## Requirements\n"
            "- Minecraft 1.7.10 (Forge)\n"
            "- GeckoLib (1.7.10 backport)\n"
        ),
        "cf_category": "Library / API",
        "cf_type": "Mod",
    },
    "pack": {
        "slug": "gigafauna-pack",
        "title": "Gigafauna-Pack",
        "project_type": "modpack",
        "categories": ["adventure"],
        "client_side": "required",
        "server_side": "required",
        "summary": "Clean-room 1.7.10 modpack built around the Gigafauna creature mod + matoulib. No third-party bloat.",
        "body": (
            "# Gigafauna (modpack)\n\n"
            "A **clean-room** Minecraft 1.7.10 modpack built around the "
            "[Gigafauna](https://modrinth.com/mod/gigafauna) creature mod and the "
            "[matoulib](https://modrinth.com/mod/matoulib) engine, plus a small set of permissive "
            "runtime dependencies. No third-party content bloat, no mod-director / FileDirector — a "
            "native manifest of our own mods.\n\n"
            "## Contents\n"
            "- Gigafauna + matoulib\n"
            "- Runtime deps (lwjgl3ify, UniMixins, FalsePatternLib, GeckoLib backport, "
            "OptimizationsAndTweaks)\n\n"
            "_Ships once it has a playable gameplay loop._\n"
        ),
        "cf_category": "Adventure and RPG",
        "cf_type": "Modpack",
    },
}

LICENSE_ID = "MIT"

# Placeholder brand logos live next to this script (assets/brand/<slug>.png, generated by
# gen_placeholder_logos.py). CurseForge requires an avatar at manual creation; Modrinth's icon is
# optional and set here via PATCH. Swap the file + re-run `--icons` to replace the placeholder.
BRAND_DIR = os.path.join(SCRIPT_DIR, "assets", "brand")

_ICON_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


def icon_path_for(name, overrides):
    """Resolved icon file for a project: --icon override wins, else assets/brand/<slug>.png."""
    if name in overrides:
        return overrides[name]
    return os.path.join(BRAND_DIR, f"{PROJECTS[name]['slug']}.png")


# ── Modrinth API helpers (mirror modrinth_upload.py) ─────────────────────────────────────────────
def load_token():
    tok = E.modrinth_token()
    if not tok:
        sys.exit("MODRINTH_TOKEN absent — add it to .env.local (create at "
                 "https://modrinth.com/settings/pats, scopes: 'Create projects' + 'Read user data').")
    return tok


def api_get(path, token=None):
    req = urllib.request.Request(f"{API}{path}", method="GET")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def modrinth_slug_exists(slug):
    """True if the project/slug is already taken (by anyone), False if free, None on transient error."""
    try:
        api_get(f"/project/{slug}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None
    except Exception:
        return None


def encode_multipart_data_only(data_json):
    """POST /v2/project is multipart; the icon is optional at creation, so we send only the
    'data' field. (Matches Modrinth's create-project contract: one JSON part named 'data'.)"""
    boundary = "----reserveSlugs7MA4YWxkTrZu0gW"
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="data"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        data_json.encode() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def create_modrinth_project(spec, token, dry_run):
    """Create the project as a DRAFT. Returns the created project dict, or None on dry-run."""
    data = {
        "slug": spec["slug"],
        "title": spec["title"],
        "description": spec["summary"],   # Modrinth "description" = the short summary (<=256 chars)
        "body": spec["body"],             # Modrinth "body" = the long markdown page
        "categories": spec["categories"],
        "additional_categories": [],
        "client_side": spec["client_side"],
        "server_side": spec["server_side"],
        "license_id": LICENSE_ID,
        "project_type": spec["project_type"],
        "is_draft": True,                 # DRAFT = slug claimed, not yet public/editable
        "initial_versions": [],
    }
    if dry_run:
        print(f"  [dry-run] would POST /project  slug={spec['slug']} "
              f"type={spec['project_type']} cats={spec['categories']} license={LICENSE_ID}")
        return None

    body, boundary = encode_multipart_data_only(json.dumps(data))
    req = urllib.request.Request(f"{API}/project", data=body, method="POST")
    req.add_header("Authorization", token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"  [modrinth] HTTP {e.code} creating '{spec['slug']}' — {detail}")
    print(f"  [modrinth] CREATED draft '{spec['slug']}' — id {resp.get('id')}")
    return resp


def upload_modrinth_icon(project_id, icon_file, token, dry_run):
    """PATCH /project/{id}/icon — the image is the RAW request body (not multipart), with the file
    extension passed as ?ext=. Returns True on success, False if the file is missing/unsupported."""
    if not os.path.isfile(icon_file):
        print(f"    [icon] file not found, skipping: {icon_file}")
        return False
    ext = os.path.splitext(icon_file)[1].lower()
    mime = _ICON_MIME.get(ext)
    if not mime:
        print(f"    [icon] unsupported extension '{ext}' ({icon_file}) — need png/jpg/gif/webp.")
        return False
    if dry_run:
        print(f"    [dry-run] would PATCH icon {os.path.basename(icon_file)} -> project {project_id}")
        return True
    with open(icon_file, "rb") as f:
        img = f.read()
    req = urllib.request.Request(f"{API}/project/{project_id}/icon?ext={ext.lstrip('.')}",
                                 data=img, method="PATCH")
    req.add_header("Authorization", token)
    req.add_header("Content-Type", mime)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        print(f"    [icon] HTTP {e.code} setting icon on {project_id} — {detail}")
        return False
    print(f"    [icon] set {os.path.basename(icon_file)} on project {project_id}")
    return True


# ── CurseForge (manual) ──────────────────────────────────────────────────────────────────────────
def print_cf_checklist(names):
    print("\n=== CurseForge — MANUAL creation (no create-project API) ===")
    print("Create each project at https://console.curseforge.com/ (Minecraft 1.7.10), then paste the")
    print("numeric project id back:  reserve_slugs.py --cf-id NAME=<id> ... --wire-manifest\n")
    for name in names:
        s = PROJECTS[name]
        print(f"- {name}:")
        print(f"    Name     : {s['title']}")
        print(f"    Slug/URL : {s['slug']}")
        print(f"    Type     : {s['cf_type']}")
        print(f"    Category : {s['cf_category']}")
        print(f"    License  : {LICENSE_ID}")
        print(f"    Avatar   : {os.path.join(BRAND_DIR, s['slug'] + '.png')}  (mandatory at CF creation)")
        print(f"    Summary  : {s['summary']}")


# ── release-manifest wiring ────────────────────────────────────────────────────────────────────
def wire_manifest(created_modrinth, cf_ids):
    """Record slugs + resolved ids into release-manifest.json. Adds a project entry keyed by name if
    absent; only fills the modrinth/curseforge blocks we have ids for (never clobbers existing)."""
    path = E.RELEASE_MANIFEST
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    mods = manifest.setdefault("mods", [])
    by_name = {m.get("name"): m for m in mods}

    display_name = {"gigafauna": "Gigafauna", "matoulib": "matoulib", "pack": "Gigafauna Pack"}
    changed = []
    for name, spec in PROJECTS.items():
        entry = by_name.get(display_name[name])
        if entry is None:
            entry = {"name": display_name[name]}
            mods.append(entry)
            by_name[display_name[name]] = entry
        if name in created_modrinth and created_modrinth[name]:
            entry.setdefault("modrinth", {})
            entry["modrinth"]["project_id"] = created_modrinth[name].get("id")
            entry["modrinth"]["slug"] = spec["slug"]
            changed.append(f"{name}.modrinth={created_modrinth[name].get('id')}")
        if name in cf_ids:
            entry.setdefault("curseforge", {})
            entry["curseforge"]["project_id"] = cf_ids[name]
            entry["curseforge"]["slug"] = spec["slug"]
            changed.append(f"{name}.curseforge={cf_ids[name]}")

    if not changed:
        print("[manifest] nothing to wire (no new ids).")
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[manifest] wired {', '.join(changed)} into {path}")


def parse_cf_ids(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"--cf-id must be NAME=<id>, got '{p}'")
        name, val = p.split("=", 1)
        if name not in PROJECTS:
            sys.exit(f"--cf-id unknown project '{name}' (known: {', '.join(PROJECTS)})")
        out[name] = val.strip()
    return out


def main():
    ap = argparse.ArgumentParser(description="Reserve clean-room slugs on Modrinth + CurseForge (docs/26).")
    ap.add_argument("--execute", action="store_true", help="actually create the Modrinth drafts (outward)")
    ap.add_argument("--verify", action="store_true", help="token valid + which slugs already exist, no creation")
    ap.add_argument("--cf-checklist", action="store_true", help="print the manual CurseForge creation checklist")
    ap.add_argument("--cf-id", action="append", metavar="NAME=ID",
                    help="record a manually-created CurseForge project id (repeatable)")
    ap.add_argument("--wire-manifest", action="store_true",
                    help="write resolved slugs/ids into release-manifest.json")
    ap.add_argument("--icons", action="store_true",
                    help="upload/refresh the Modrinth icon of EXISTING projects (outward, no create)")
    ap.add_argument("--icon", action="append", metavar="NAME=PATH", default=[],
                    help="override the icon file for a project (default assets/brand/<slug>.png)")
    ap.add_argument("--only", action="append", help="restrict to one project name (repeatable)")
    args = ap.parse_args()

    icon_overrides = parse_cf_ids(args.icon)  # same NAME=VALUE parser, validates the project name

    names = args.only or list(PROJECTS)
    for n in names:
        if n not in PROJECTS:
            sys.exit(f"unknown project '{n}' (known: {', '.join(PROJECTS)})")

    cf_ids = parse_cf_ids(args.cf_id)

    # Manifest-only path: recording manual CF ids (no Modrinth call needed).
    if cf_ids and args.wire_manifest and not (args.execute or args.verify or args.icons):
        wire_manifest({}, cf_ids)
        return

    token = load_token()

    if args.verify:
        try:
            u = api_get("/user", token)
            print(f"[modrinth-verify] token OK (user {u.get('username')}, id {u.get('id')})")
        except Exception as ex:
            sys.exit(f"[modrinth-verify] token invalid (need scopes 'Create projects' + 'Read user data'): {ex}")
        for n in names:
            slug = PROJECTS[n]["slug"]
            exists = modrinth_slug_exists(slug)
            state = {True: "TAKEN", False: "free", None: "unknown(error)"}[exists]
            print(f"[modrinth-verify] slug '{slug}' — {state}")
        if args.cf_checklist:
            print_cf_checklist(names)
        return

    # A run does something outward if it creates (--execute) or (re)sets icons (--icons); otherwise
    # it is a dry-run preview.
    icon_dry = not (args.execute or args.icons)
    mode = "CREATE (--execute)" if args.execute else ("ICONS (--icons)" if args.icons else "DRY-RUN")
    print(f"=== Modrinth {mode} ===")
    created = {}
    for n in names:
        spec = PROJECTS[n]
        exists = modrinth_slug_exists(spec["slug"])
        project_id = None
        if exists is None:
            print(f"  [warn] could not check '{spec['slug']}' (transient) — skipping to be safe.")
            continue
        if exists is True:
            print(f"  [skip] '{spec['slug']}' already exists on Modrinth (idempotent).")
            # Existing project: resolve its id so --icons can still refresh the avatar.
            if args.icons or icon_dry:
                try:
                    project_id = api_get(f"/project/{spec['slug']}").get("id")
                except Exception as ex:
                    print(f"    [icon] cannot resolve id for '{spec['slug']}': {ex}")
        else:
            resp = create_modrinth_project(spec, token, dry_run=not args.execute)
            created[n] = resp
            project_id = (resp or {}).get("id") if resp else None

        # Icon: after create OR when --icons refreshes an existing project. In a pure dry-run we still
        # preview it (project_id may be None if the project doesn't exist yet — skip the preview then).
        if args.execute or args.icons or icon_dry:
            if project_id:
                upload_modrinth_icon(project_id, icon_path_for(n, icon_overrides), token, dry_run=icon_dry)
            elif icon_dry and exists is False:
                print(f"    [dry-run] would set icon after creating '{spec['slug']}'.")

    if args.cf_checklist or not (args.execute or args.icons):
        print_cf_checklist(names)

    if args.wire_manifest:
        wire_manifest(created, cf_ids)
    elif args.execute and any(created.values()):
        print("\n[hint] re-run with --wire-manifest (and --cf-id NAME=<id> once CF projects exist) "
              "to record ids into release-manifest.json.")


if __name__ == "__main__":
    main()
