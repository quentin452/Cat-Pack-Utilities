#!/usr/bin/env python3
"""packenv.py — single source of truth for every path/id the pack tooling needs.

Why (2026-07-09): ~23 scripts each re-hardcoded the same handful of paths (pack repo, MODPACK dir,
TEST client instance, server instance, hub/repos.json) AND re-implemented .env parsing. A machine move
or a renamed instance meant editing N files; one missed = a tool silently reads/writes a stale path.
Now every script does `import packenv as E` and reads `E.PACK_DIR`, `E.INSTANCE_TEST`, … — one place to
change, overridable per-machine via .env / .env.local, no external dependency (tiny dotenv parser).

Precedence for every value: process env  >  .env.local  >  .env  >  built-in default.

Back-compat with the pre-existing .env keys (do NOT rename them):
  PACK                    = pack NAME (e.g. "Biggess Pack Cat Edition")   [NOT a path — see PACK_DIR]
  INSTANCE_PATH           = TEST client instance ROOT
  CANONICAL_CONFIG        = <PACK_DIR>/src/common/config
  INSTANCE_TEST_CONFIG    = <INSTANCE_TEST>/config
  INSTANCE_SERVER_CONFIG  = <INSTANCE_SERVER>/config
New optional override keys: GITHUB_ROOT, HUB, PACK_REPO, PACK_DIR, INSTANCES_ROOT, INSTANCE_TEST,
INSTANCE_SERVER, PACK_PROJECT_ID. Each defaults to the historical hardcoded value, so an empty .env
reproduces today's behavior exactly (verified by `python3 packenv.py --check`).
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = None


def _parse(path):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def _dotenv():
    """.env.local overrides .env; both live next to this module. Cached (files read once)."""
    global _CACHE
    if _CACHE is None:
        env = _parse(os.path.join(_HERE, ".env"))
        env.update(_parse(os.path.join(_HERE, ".env.local")))  # .local wins
        _CACHE = env
    return _CACHE


def get(key, default=None):
    """Resolve one key: process env > .env.local > .env > default."""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    v = _dotenv().get(key)
    return v if v not in (None, "") else default


def _exp(p):
    return os.path.expanduser(p)


# ── roots ─────────────────────────────────────────────────────────────────────────────────────
HOME = _exp("~")
GITHUB_ROOT = _exp(get("GITHUB_ROOT", os.path.join(HOME, "Documents/GitHub")))
HUB = _exp(get("HUB", os.path.join(GITHUB_ROOT, "Mod-Sandbox")))
REPOS_JSON = os.path.join(HUB, "memory", "repos.json")
RELEASE_MANIFEST = os.path.join(HUB, "memory", "release-manifest.json")

# ── the pack ──────────────────────────────────────────────────────────────────────────────────
PACK_NAME = get("PACK", "Biggess Pack Cat Edition")            # historical key PACK = the NAME
PACK_REPO = _exp(get("PACK_REPO", os.path.join(GITHUB_ROOT, "privates-minecraft-modpack")))
PACK_DIR = _exp(get("PACK_DIR", os.path.join(PACK_REPO, "MODPACKS", PACK_NAME)))
PACK_PROJECT_ID = int(get("PACK_PROJECT_ID", "830694"))        # CF modpack project id

# derived pack sub-paths (always relative to PACK_DIR — never hardcode these downstream)
SRC_COMMON = os.path.join(PACK_DIR, "src", "common")
CANONICAL_CONFIG = _exp(get("CANONICAL_CONFIG", os.path.join(SRC_COMMON, "config")))
MOD_DIRECTOR = os.path.join(CANONICAL_CONFIG, "mod-director")
CURSE_BUNDLE = os.path.join(MOD_DIRECTOR, "curse.bundle.json")
URL_BUNDLE = os.path.join(MOD_DIRECTOR, "url.bundle.json")
MODRINTH_BUNDLE = os.path.join(MOD_DIRECTOR, "modrinth.bundle.json")
SERVER_SRC = os.path.join(PACK_DIR, "src", "server")
SERVER_MODS = os.path.join(SERVER_SRC, "mods")
CLIENT_SRC = os.path.join(PACK_DIR, "src", "client")
CLIENT_MANIFEST = os.path.join(CLIENT_SRC, "manifest.json")

# ── instances ─────────────────────────────────────────────────────────────────────────────────
INSTANCES_ROOT = _exp(get("INSTANCES_ROOT",
                          os.path.join(HOME, "Documents/curseforge/minecraft/Instances")))
# INSTANCE_PATH is the historical key for the TEST client root; keep honoring it.
INSTANCE_TEST = _exp(get("INSTANCE_TEST", get("INSTANCE_PATH",
                         os.path.join(INSTANCES_ROOT, PACK_NAME + " V1 TEST"))))
INSTANCE_SERVER = _exp(get("INSTANCE_SERVER",
                          os.path.join(HOME, "Bureau/SERVERS", PACK_NAME + " V1 Server")))
# config dirs (historical *_CONFIG keys win if set, else derived from the instance roots)
INSTANCE_TEST_CONFIG = _exp(get("INSTANCE_TEST_CONFIG", os.path.join(INSTANCE_TEST, "config")))
INSTANCE_SERVER_CONFIG = _exp(get("INSTANCE_SERVER_CONFIG", os.path.join(INSTANCE_SERVER, "config")))
INSTANCE_TEST_MODS = os.path.join(INSTANCE_TEST, "mods")
INSTANCE_SERVER_MODS = os.path.join(INSTANCE_SERVER, "mods")

# ── secrets (.env.local; never printed) ─────────────────────────────────────────────────────────
def cf_api_key():
    return get("CF_API_KEY")


def cf_upload_token():
    return get("CF_UPLOAD_TOKEN")


def modrinth_token():
    return get("MODRINTH_TOKEN")


def _check():
    """Print every resolved path + exist-flag. Use to eyeball a machine's config."""
    rows = [
        ("GITHUB_ROOT", GITHUB_ROOT), ("HUB", HUB), ("REPOS_JSON", REPOS_JSON),
        ("PACK_NAME", PACK_NAME), ("PACK_REPO", PACK_REPO), ("PACK_DIR", PACK_DIR),
        ("PACK_PROJECT_ID", PACK_PROJECT_ID),
        ("CANONICAL_CONFIG", CANONICAL_CONFIG), ("CURSE_BUNDLE", CURSE_BUNDLE),
        ("URL_BUNDLE", URL_BUNDLE), ("SERVER_SRC", SERVER_SRC), ("SERVER_MODS", SERVER_MODS),
        ("CLIENT_MANIFEST", CLIENT_MANIFEST),
        ("INSTANCES_ROOT", INSTANCES_ROOT), ("INSTANCE_TEST", INSTANCE_TEST),
        ("INSTANCE_SERVER", INSTANCE_SERVER),
        ("INSTANCE_TEST_CONFIG", INSTANCE_TEST_CONFIG),
        ("INSTANCE_SERVER_CONFIG", INSTANCE_SERVER_CONFIG),
    ]
    print("packenv resolved config (process-env > .env.local > .env > default):\n")
    for k, v in rows:
        if isinstance(v, int) or k == "PACK_NAME":
            print(f"  {k:24} = {v}")
        else:
            print(f"  {'✓' if os.path.exists(v) else '✗':1} {k:22} = {v}")
    for k in ("CF_API_KEY", "CF_UPLOAD_TOKEN", "MODRINTH_TOKEN"):
        print(f"  {'set' if get(k) else '—':>3} {k}")


if __name__ == "__main__":
    _check()
