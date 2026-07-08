#!/usr/bin/env python3
"""
modrinth_upload.py — upload a mod jar as a new version on Modrinth.

Uses MODRINTH_TOKEN (from .env.local — create at https://modrinth.com/settings/pats,
scope: create versions). Modrinth's API is simpler than CF: game versions are by NAME
("1.7.10"), loaders by name ("forge").

Endpoint: POST https://api.modrinth.com/v2/version  (multipart: "data" json + "file").

Usage:
  modrinth_upload.py --project <id-or-slug> --file build/libs/gigafauna-V0.1.0.jar \
                     --version V0.1.0 --name "V0.1.0" --changelog-file notes.md [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://api.modrinth.com/v2"
DEFAULT_GAME_VERSION = "1.7.10"
DEFAULT_LOADER = "forge"


def normalize_modrinth_version(version, version_format="strip-v"):
    """CONCERN B — the SINGLE source of truth for a version's Modrinth form, so the upload and the
    idempotency dedup (release_mods.publish_plan) always agree. Modrinth projects use a bare number
    ('1.17.1') while GitHub/CF use 'V1.17.5'; uploading 'V…' would break the Modrinth convention AND
    defeat the version_number dedup. Default strips a leading V/v; 'keep' leaves it (for a project
    that wants the V)."""
    if version_format == "keep":
        return version
    return re.sub(r"^[Vv]", "", str(version))


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
            if k.strip() == "MODRINTH_TOKEN":
                os.environ.setdefault("MODRINTH_TOKEN", v.strip().strip("'\""))
    tok = os.environ.get("MODRINTH_TOKEN")
    if not tok:
        sys.exit("MODRINTH_TOKEN absent — add it to .env.local "
                 "(create at https://modrinth.com/settings/pats, scope: create versions).")
    return tok


def api_get(path, token=None):
    req = urllib.request.Request(f"{API}{path}", method="GET")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def verify(token, project=None):
    """Pre-flight: token valid (+ project/slug exists if given), no upload."""
    try:
        u = api_get("/user", token)
        print(f"[modrinth-verify] token OK (user {u.get('username')}, id {u.get('id')})")
    except Exception as ex:
        raise SystemExit(f"[modrinth-verify] token invalid (need scope 'Read user data'): {ex}")
    if project:
        try:
            p = api_get(f"/project/{project}")
            print(f"[modrinth-verify] project '{project}' exists: {p.get('title')} (id {p.get('id')})")
        except Exception as ex:
            raise SystemExit(f"[modrinth-verify] project '{project}' not found (reserve the slug first?): {ex}")


def encode_multipart(data_json, filename, file_bytes):
    boundary = "----modrinth7MA4YWxkTrZu0gW"
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="data"\r\n')
    parts.append(b"Content-Type: application/json\r\n\r\n")
    parts.append(data_json.encode() + b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    parts.append(b"Content-Type: application/java-archive\r\n\r\n")
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def main():
    ap = argparse.ArgumentParser(description="Upload a jar version to Modrinth.")
    ap.add_argument("--project", help="Modrinth project id or slug (required for upload; optional for --verify)")
    ap.add_argument("--file", help="jar to upload (required unless --verify)")
    ap.add_argument("--version", help="version_number, e.g. V0.1.0 (required unless --verify)")
    ap.add_argument("--name", help="display name (default = version)")
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--loader", default=DEFAULT_LOADER)
    ap.add_argument("--release-type", default="release", choices=["release", "beta", "alpha"])
    ap.add_argument("--version-format", default="strip-v", choices=["strip-v", "keep"],
                    help="Modrinth version_number form: strip-v (default, drop a leading V) or keep")
    ap.add_argument("--changelog", default="")
    ap.add_argument("--changelog-file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="pre-flight: project exists + token, no upload")
    args = ap.parse_args()

    token = load_token()

    if args.verify:
        verify(token, args.project)
        return

    if not (args.project and args.file and args.version):
        sys.exit("--project, --file and --version are required for upload (or use --verify)")
    if not os.path.isfile(args.file):
        sys.exit(f"file not found: {args.file}")

    changelog = args.changelog
    if args.changelog_file:
        changelog = open(args.changelog_file, encoding="utf-8").read()

    # Normalize to the Modrinth version form (strip a leading V by default) — shared helper so this
    # matches the dedup in release_mods.publish_plan.
    mr_version = normalize_modrinth_version(args.version, args.version_format)
    if mr_version != args.version:
        print(f"[modrinth] version {args.version} -> {mr_version} (version-format={args.version_format})")

    data = {
        "name": args.name or mr_version,
        "version_number": mr_version,
        "changelog": changelog,
        "dependencies": [],
        "game_versions": [args.game_version],
        "version_type": args.release_type,
        "loaders": [args.loader],
        "featured": False,
        "project_id": args.project,
        "file_parts": ["file"],
        "primary_file": "file",
    }

    with open(args.file, "rb") as f:
        file_bytes = f.read()
    body, boundary = encode_multipart(json.dumps(data), os.path.basename(args.file), file_bytes)

    print(f"[modrinth] project {args.project}  file {os.path.basename(args.file)} "
          f"({len(file_bytes)} bytes)  version {mr_version}")
    if args.dry_run:
        print(f"[modrinth] data: {json.dumps(data)}")
        print("[modrinth] --dry-run: not POSTing.")
        return

    req = urllib.request.Request(f"{API}/version", data=body, method="POST")
    req.add_header("Authorization", token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.load(r)
    print(f"[modrinth] OK — version id: {resp.get('id')}")


if __name__ == "__main__":
    main()
