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
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://api.modrinth.com/v2"
DEFAULT_GAME_VERSION = "1.7.10"
DEFAULT_LOADER = "forge"


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


def verify(token, project):
    """Pre-flight: project/slug exists + token valid, no upload."""
    try:
        p = api_get(f"/project/{project}")
        print(f"[modrinth-verify] project '{project}' exists: {p.get('title')} (id {p.get('id')})")
    except Exception as ex:
        raise SystemExit(f"[modrinth-verify] project '{project}' not found (reserve the slug first?): {ex}")
    try:
        u = api_get("/user", token)
        print(f"[modrinth-verify] token OK (user {u.get('username')})")
    except Exception as ex:
        raise SystemExit(f"[modrinth-verify] token invalid: {ex}")


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
    ap.add_argument("--project", required=True, help="Modrinth project id or slug")
    ap.add_argument("--file", help="jar to upload (required unless --verify)")
    ap.add_argument("--version", help="version_number, e.g. V0.1.0 (required unless --verify)")
    ap.add_argument("--name", help="display name (default = version)")
    ap.add_argument("--game-version", default=DEFAULT_GAME_VERSION)
    ap.add_argument("--loader", default=DEFAULT_LOADER)
    ap.add_argument("--release-type", default="release", choices=["release", "beta", "alpha"])
    ap.add_argument("--changelog", default="")
    ap.add_argument("--changelog-file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="pre-flight: project exists + token, no upload")
    args = ap.parse_args()

    token = load_token()

    if args.verify:
        verify(token, args.project)
        return

    if not args.file or not args.version:
        sys.exit("--file and --version are required (or use --verify)")
    if not os.path.isfile(args.file):
        sys.exit(f"file not found: {args.file}")

    changelog = args.changelog
    if args.changelog_file:
        changelog = open(args.changelog_file, encoding="utf-8").read()

    data = {
        "name": args.name or args.version,
        "version_number": args.version,
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
          f"({len(file_bytes)} bytes)  version {args.version}")
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
