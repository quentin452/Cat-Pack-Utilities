#!/usr/bin/env python3
"""
cf_upload.py — upload a mod jar to CurseForge via the author Upload API.

Uses the AUTHOR token (CF_UPLOAD_TOKEN in .env.local — generate at
https://authors-old.curseforge.com/account/api-tokens). This is NOT the CF_API_KEY read key.

Endpoint (Minecraft): POST https://minecraft.curseforge.com/api/projects/<id>/upload-file
Auth header: X-Api-Token. Body: multipart/form-data { metadata (json), file }.

Usage:
  cf_upload.py --project-id 855466 --file build/libs/optimizationsandtweaks-V1.17.2.jar \
               --display-name V1.17.2 --changelog-file notes.md [--release-type release]
  cf_upload.py --project-id 855466 --file X.jar --changelog "line1" --dry-run   # validate + build, no POST
  cf_upload.py --list-versions                                                   # print 1.7.10 game version id
"""

import re
import argparse
import json
import os
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CF_HOST = "https://minecraft.curseforge.com"
DEFAULT_GAME_VERSION = "1.7.10"


def load_token():
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "CF_UPLOAD_TOKEN":
                os.environ.setdefault("CF_UPLOAD_TOKEN", v.strip().strip("'\""))
    tok = os.environ.get("CF_UPLOAD_TOKEN")
    if not tok:
        sys.exit("CF_UPLOAD_TOKEN absent — add it to Cat-Pack-Utilities/.env.local "
                 "(generate at https://authors-old.curseforge.com/account/api-tokens).")
    return tok


def api_get(path, token):
    req = urllib.request.Request(f"{CF_HOST}{path}", method="GET")
    req.add_header("X-Api-Token", token)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_api_key():
    """Optional CF_API_KEY (read API) for verifying a project exists — not the upload token."""
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "CF_API_KEY":
                return v.strip().strip("'\"")
    return os.environ.get("CF_API_KEY")


def verify(token, project_id, game_version):
    """Pre-flight: upload token valid + game version resolvable + (if possible) project exists.
    game_version may be a comma-separated list (bootstrapper mods span many MC versions);
    every name must resolve, mirroring the upload path."""
    names = [v.strip() for v in game_version.split(",") if v.strip()]
    vids = [resolve_game_version_id(token, n) for n in names]  # raises if token/any version bad
    print(f"[cf-verify] token OK, {len(vids)} version(s) resolved: " +
          ", ".join(f"{n}->{i}" for n, i in zip(names, vids)))
    if project_id:
        key = read_api_key()
        if key:
            try:
                req = urllib.request.Request(f"https://api.curseforge.com/v1/mods/{project_id}", method="GET")
                req.add_header("x-api-key", key)
                req.add_header("Accept", "application/json")
                with urllib.request.urlopen(req, timeout=20) as r:
                    mod = json.load(r)["data"]
                print(f"[cf-verify] project {project_id} exists: {mod.get('name')}")
            except Exception as ex:
                raise SystemExit(f"[cf-verify] project {project_id} not found via read API: {ex}")
        else:
            print(f"[cf-verify] project {project_id} existence not checked (no CF_API_KEY read key)")


_VERSIONS_CACHE = None
_MC_TYPE_IDS = None


def _mc_type_ids(token):
    """CF game-version type ids that are real Minecraft-java version groups ('Minecraft 1.7',
    'Minecraft 1.8', ...). CF reuses version names like '1.8' across OTHER types (e.g. 'Addons',
    Bukkit) whose ids the upload API rejects as 'invalid dependency' (error 1009)."""
    global _MC_TYPE_IDS
    if _MC_TYPE_IDS is None:
        vt = api_get("/api/game/version-types", token)
        _MC_TYPE_IDS = {t["id"] for t in vt if re.match(r"^Minecraft \d", str(t.get("name", "")))}
    return _MC_TYPE_IDS


def resolve_game_version_id(token, name):
    """Return the numeric CF game-version id for e.g. '1.7.10', preferring the real Minecraft-java
    version type (see _mc_type_ids)."""
    global _VERSIONS_CACHE
    if _VERSIONS_CACHE is None:  # the endpoint refetches are expensive; cache the flattened list
        _VERSIONS_CACHE = list(_flatten_versions(api_get("/api/game/versions", token)))
    mc = _mc_type_ids(token)
    hits = [v for v in _VERSIONS_CACHE if str(v.get("name")) == name]
    if not hits:
        raise SystemExit(f"game version {name!r} not found via /api/game/versions")
    for v in hits:
        if v.get("gameVersionTypeID") in mc:
            return v.get("id")
    # No Minecraft-java type matched (only foreign types like Addons): fall back to the first hit
    # but warn — better than silently sending an id the upload API will 400 on.
    print(f"[cf-upload] WARN: '{name}' has no Minecraft-java version type; using id {hits[0].get('id')}")
    return hits[0].get("id")


def _flatten_versions(obj):
    """The version list can be a flat list of {name,id} or grouped; yield all dicts with name+id."""
    if isinstance(obj, dict):
        if "name" in obj and "id" in obj:
            yield obj
        for v in obj.values():
            yield from _flatten_versions(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _flatten_versions(v)


def encode_multipart(fields, file_field, filename, file_bytes):
    """Minimal multipart/form-data encoder (stdlib only)."""
    boundary = "----cfupload7MA4YWxkTrZu0gW"
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(b"Content-Type: application/java-archive\r\n\r\n")
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def upload(project_id, file_path, metadata, token, dry_run):
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    fields = {"metadata": json.dumps(metadata)}
    body, boundary = encode_multipart(fields, "file", os.path.basename(file_path), file_bytes)
    url = f"{CF_HOST}/api/projects/{project_id}/upload-file"

    print(f"[cf-upload] project {project_id}  file {os.path.basename(file_path)} "
          f"({len(file_bytes)} bytes)")
    print(f"[cf-upload] metadata: {json.dumps(metadata)}")
    if dry_run:
        print("[cf-upload] --dry-run: not POSTing.")
        return

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("X-Api-Token", token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")
        print(f"[cf-upload] HTTP {e.code} {e.reason} — response body:\n{body_txt}")
        raise
    print(f"[cf-upload] OK — file id: {resp.get('id')}")


def main():
    ap = argparse.ArgumentParser(description="Upload a jar to CurseForge (author API).")
    ap.add_argument("--project-id", type=int)
    ap.add_argument("--file")
    ap.add_argument("--display-name")
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--release-type", default="release", choices=["alpha", "beta", "release"])
    ap.add_argument("--changelog", default="")
    ap.add_argument("--changelog-file")
    ap.add_argument("--changelog-type", default="markdown", choices=["text", "html", "markdown"])
    ap.add_argument("--parent-file-id", type=int,
                    help="attach as an additional file (e.g. a server pack) of this main file id; "
                         "game versions are inherited from the parent and must NOT be sent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-versions", action="store_true",
                    help="just print the game-version id and exit (token check)")
    ap.add_argument("--verify", action="store_true",
                    help="pre-flight: token valid + project exists, no upload")
    args = ap.parse_args()

    token = load_token()

    if args.list_versions:
        vid = resolve_game_version_id(token, args.game_version)
        print(f"{args.game_version} -> game version id {vid}")
        return

    if args.verify:
        verify(token, args.project_id, args.game_version)
        return

    if not (args.project_id and args.file):
        sys.exit("--project-id and --file are required (or use --list-versions).")
    if not os.path.isfile(args.file):
        sys.exit(f"file not found: {args.file}")

    changelog = args.changelog
    if args.changelog_file:
        changelog = open(args.changelog_file, encoding="utf-8").read()

    metadata = {
        "changelog": changelog,
        "changelogType": args.changelog_type,
        "releaseType": args.release_type,
    }
    if args.parent_file_id:
        # Additional file (server pack): CF derives its game versions from the parent file,
        # so gameVersions must be OMITTED (sending them is rejected).
        metadata["parentFileID"] = args.parent_file_id
    else:
        # --game-version accepts a comma-separated list. Version-agnostic files (e.g. the
        # mod-director bootstrapper) must carry ALL supported MC versions, else they're
        # undiscoverable on other versions and inconsistent with prior files.
        names = [v.strip() for v in args.game_version.split(",") if v.strip()]
        metadata["gameVersions"] = [resolve_game_version_id(token, n) for n in names]
    if args.display_name:
        metadata["displayName"] = args.display_name

    upload(args.project_id, args.file, metadata, token, args.dry_run)


if __name__ == "__main__":
    main()
