#!/usr/bin/env python3
"""
config_sync.py — sync a pack game-config between the CANONICAL source and the local test
instances (client + server). Fills the gap the mod-based tools leave: update_local=jars,
pack_sync=mod fileIDs, bundle_check=fetchability — NONE touch game config.

FORWARD (canonical -> instance) is the default source of truth. REVERSE (instance -> canonical)
is a filtered escape hatch: edit config live in an instance, then pull it back FILTERED so
machine-specific / generated junk (java paths, caches) never lands in canonical and ships to players.

Workflow for a config change (perf tune / fix, e.g. CoFH ore density, BoP feature):
  1. edit the CANONICAL (privates-minecraft-modpack/.../src/common/config) OR edit an instance then
     reverse-sync it back FILTERED (--from-instance ... --only <subpath>)
  2. this tool: mirror canonical -> instances, validate in-game
  3. commit the config change with a good message (config(scope): ...) — that commit IS the changelog
     entry (changelog_from_bundles.py derives it)  +  bump pack version
  4. ship: generate_modpack_zips packages src/common/config -> overrides/config (client AND serverpack)

FORWARD (canonical -> instances):
  config_sync.py                         # DIFF: files where an instance differs from canonical
  config_sync.py --only cofh/world       # limit to a subpath (file or dir)
  config_sync.py --apply                 # copy canonical -> instances (byte copy, CRLF preserved)
  config_sync.py --apply --only cofh/world/ThermalFoundation-Ores.json
  config_sync.py --instance TEST         # one instance only

REVERSE (instance -> canonical, FILTERED — machine-junk denylist applied on every copy):
  config_sync.py --from-instance TEST --only bloodmagic          # preview a selective pull-back
  config_sync.py --from-instance TEST --only bloodmagic --apply   # write it into canonical
  config_sync.py --from-instance TEST --all-confirm               # full reverse (DANGEROUS, explicit)
  # a bare --from-instance with no --only / --all-confirm is REFUSED (never mass-overwrite canonical).

Paths + pack name are read from .env / .env.local (see .env.example); load order is
process env > .env.local (gitignored) > .env (committed) > built-in fallback. No external deps.
Non-destructive: never deletes files on either side.
"""
import argparse
import filecmp
import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- built-in fallback (used only if nothing is set in env / .env / .env.local) ---
_DEF_PACK = "Biggess Pack Cat Edition"
_DEF_CANONICAL = os.path.join(HOME, "Documents/GitHub/privates-minecraft-modpack/MODPACKS", _DEF_PACK, "src/common/config")
_DEF_INSTANCES = {
    "TEST":   os.path.join(HOME, "Documents/curseforge/minecraft/Instances", _DEF_PACK + " V1 TEST", "config"),
    "server": os.path.join(HOME, "Bureau/SERVERS", _DEF_PACK + " V1 Server", "config"),
}

# --- machine-junk denylist (REVERSE only): never flows instance -> canonical ---
# relauncher.json is handled specially (MERGE, see merge_relauncher). The rest is an explicit,
# deliberately-small list of per-machine / generated paths — extend as new cases are found.
RELAUNCHER = "lwjgl3ify-relauncher.json"
# machine-generated java keys inside relauncher.json (absolute paths + an index into them):
RELAUNCHER_MACHINE_KEYS = ("javaInstallationsCache", "javaInstallation")
# exact config-relative paths (posix) that are machine-specific but NOT merge-able:
DENY_FILES = set()
# config-relative dir prefixes (posix) holding generated state, not authored config:
DENY_DIRS = ()


def load_env():
    """Merge .env then .env.local (later wins), then real env vars win over both.
    Bare KEY=VALUE lines, '#' comments — parsed here, no python-dotenv dependency."""
    conf = {}
    for name in (".env", ".env.local"):
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip("'\"")
    # process env wins for OUR keys: PACK, CANONICAL_CONFIG, and any INSTANCE_<NAME>_CONFIG
    for k, v in os.environ.items():
        if k in ("PACK", "CANONICAL_CONFIG") or (k.startswith("INSTANCE_") and k.endswith("_CONFIG")):
            conf[k] = v
    return conf


def resolve_paths(env):
    """Return (canonical_dir, {instance_name: config_dir}) from env, else built-in fallback."""
    pack = env.get("PACK", _DEF_PACK)
    canonical = env.get("CANONICAL_CONFIG") or _DEF_CANONICAL
    instances = {}
    for k, v in env.items():
        # convention: INSTANCE_<NAME>_CONFIG  ->  instance named <NAME>
        if k.startswith("INSTANCE_") and k.endswith("_CONFIG") and v:
            name = k[len("INSTANCE_"):-len("_CONFIG")]
            instances[name] = v
    if not instances:
        instances = dict(_DEF_INSTANCES)
    return pack, canonical, instances


def rel_files(root, only):
    base = os.path.join(root, only) if only else root
    if os.path.isfile(base):
        yield os.path.relpath(base, root)
        return
    if not os.path.isdir(base):
        return
    for dp, _, fns in os.walk(base):
        for fn in fns:
            yield os.path.relpath(os.path.join(dp, fn), root)


def copy_byte(src, dst):
    """Byte copy: preserves CRLF (no LF normalization) + mtime. Forward (canonical LF -> instance)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def _norm(path):
    """Read file bytes for CONTENT compare, ignoring CRLF vs LF. Binary (has NUL) kept raw."""
    b = open(path, "rb").read()
    return b if b"\x00" in b else b.replace(b"\r\n", b"\n")


def same_content(a, b):
    """True if two files are identical IGNORING line endings. The game rewrites instance .cfg as
    CRLF while canonical is LF-pinned (.gitattributes) — a byte compare would flag every file."""
    try:
        return _norm(a) == _norm(b)
    except FileNotFoundError:
        return False


def copy_lf(src, dst):
    """REVERSE copy (instance -> canonical): normalize CRLF -> LF so we never re-pollute the
    LF-pinned canonical (would resurrect the CRLF<->LF diff .gitattributes kills). Binary kept raw."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    b = open(src, "rb").read()
    if b"\x00" not in b:
        b = b.replace(b"\r\n", b"\n")
    open(dst, "wb").write(b)


def deny_kind(rel):
    """Classify a REVERSE candidate: 'relauncher' (merge), 'deny' (skip), or None (copy)."""
    posix = rel.replace(os.sep, "/")
    if os.path.basename(posix) == RELAUNCHER:
        return "relauncher"
    if posix in DENY_FILES:
        return "deny"
    if any(posix == d or posix.startswith(d.rstrip("/") + "/") for d in DENY_DIRS):
        return "deny"
    return None


def merge_relauncher(inst_path, canon_path):
    """MERGE relauncher.json for reverse: take the instance's AUTHORED keys (hideSettingsOnLaunch,
    garbageCollector, customOptions, min/maxMemoryMB, debug flags, ...) but PRESERVE canonical's
    existing javaInstallationsCache + javaInstallation (per-machine, load-bearing — an empty cache
    with a hidden dialog = dead launch). If canonical has no cache, DROP those two keys entirely
    rather than write machine java paths. Returns (merged_dict, note)."""
    inst = json.load(open(inst_path, encoding="utf-8"))
    canon = json.load(open(canon_path, encoding="utf-8")) if os.path.isfile(canon_path) else {}
    merged = dict(inst)  # authored keys flow from the instance
    if canon.get("javaInstallationsCache"):
        for k in RELAUNCHER_MACHINE_KEYS:
            if k in canon:
                merged[k] = canon[k]
        note = "preserved canonical java cache (%d entrie(s)) + javaInstallation index" % \
               len(canon.get("javaInstallationsCache", []))
    else:
        dropped = [k for k in RELAUNCHER_MACHINE_KEYS if k in merged]
        for k in RELAUNCHER_MACHINE_KEYS:
            merged.pop(k, None)
        note = "canonical has no java cache -> dropped machine key(s): " + (", ".join(dropped) or "none")
    return merged, note


def write_json_lf(path, obj):
    """Write JSON with LF newlines (canonical repo pins LF via .gitattributes), 2-space indent."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def forward(canonical, instances, only, apply, one_instance):
    """CANONICAL -> instances (unchanged behavior)."""
    rels = sorted(set(rel_files(canonical, only)))
    if not rels:
        sys.exit("no canonical files under: " + (only or "config"))
    insts = {one_instance: instances[one_instance]} if one_instance else instances
    grand = 0
    for name, idir in insts.items():
        print(f"=== {name}  ({idir}) ===")
        if not os.path.isdir(idir):
            print("  (instance config dir missing — skip)")
            continue
        n_diff = n_apply = 0
        for rel in rels:
            csrc = os.path.join(canonical, rel)
            idst = os.path.join(idir, rel)
            if not os.path.exists(idst):
                differ, tag = True, "NEW    "
            elif not same_content(csrc, idst):
                differ, tag = True, "DIFFERS"
            else:
                differ = False
            if differ:
                n_diff += 1
                print(f"  {tag}: {rel}")
                if apply:
                    copy_byte(csrc, idst)
                    n_apply += 1
        grand += n_diff
        print(f"  -> {n_diff} differing" + (f", {n_apply} copied" if apply else " (preview — pass --apply)"))
    if not apply and grand:
        print("\nRun with --apply to mirror canonical -> instances, then validate in-game.")
    if apply:
        print("\nDone. Next: commit the canonical config change (config(scope): ... message = the changelog "
              "entry) + bump version + generate_modpack_zips to ship.")


def reverse(canonical, instances, name, only, apply, all_confirm):
    """INSTANCE -> canonical, FILTERED. Requires --only or --all-confirm (never mass-overwrite)."""
    if not only and not all_confirm:
        sys.exit("REFUSED: reverse sync (--from-instance %s) needs --only <subpath> OR --all-confirm.\n"
                 "  Instance config holds machine-specific / generated state — a bare full pull-back "
                 "would ship junk to players. Scope it, or pass --all-confirm to override on purpose." % name)
    idir = instances[name]
    if not os.path.isdir(idir):
        sys.exit("instance config dir missing: " + idir)
    rels = sorted(set(rel_files(idir, only)))
    if not rels:
        sys.exit("no instance files under: " + (only or "config"))

    print(f"=== REVERSE  {name} -> canonical  ({idir}  ->  {canonical}) ===")
    n_diff = n_apply = n_skip = 0
    for rel in rels:
        isrc = os.path.join(idir, rel)
        cdst = os.path.join(canonical, rel)
        kind = deny_kind(rel)

        if kind == "deny":
            n_skip += 1
            print(f"  SKIP (machine-junk): {rel}")
            continue

        if kind == "relauncher":
            merged, note = merge_relauncher(isrc, cdst)
            changed = (not os.path.isfile(cdst)) or \
                      (json.load(open(cdst, encoding="utf-8")) != merged)
            tag = "MERGE  " if changed else "same   "
            print(f"  {tag}: {rel}   [{note}]")
            if changed:
                n_diff += 1
                if apply:
                    write_json_lf(cdst, merged)
                    n_apply += 1
            continue

        # plain file: compare IGNORING line endings, write LF-normalized instance -> canonical
        if not os.path.exists(cdst):
            differ, tag = True, "NEW->canon"
        elif not same_content(isrc, cdst):
            differ, tag = True, "DIFFERS   "
        else:
            differ = False
        if differ:
            n_diff += 1
            print(f"  {tag}: {rel}")
            if apply:
                copy_lf(isrc, cdst)
                n_apply += 1

    print(f"  -> {n_diff} to update, {n_skip} skipped (machine-junk)"
          + (f", {n_apply} written to canonical" if apply else " (preview — pass --apply)"))
    if not apply and n_diff:
        print("\nRun with --apply to write these into canonical, then commit (config(scope): ...) + bump version.")
    if apply:
        print("\nDone (instance -> canonical). Machine-junk was filtered. Next: forward-sync the OTHER "
              "instance(s), validate, commit the canonical change + bump version + generate_modpack_zips.")


def main():
    env = load_env()
    _pack, canonical, instances = resolve_paths(env)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="config subpath (file or dir) to limit to")
    ap.add_argument("--apply", action="store_true", help="write changes (else preview)")
    ap.add_argument("--instance", choices=list(instances), default=None,
                    help="FORWARD: one instance only")
    ap.add_argument("--from-instance", choices=list(instances), default=None,
                    help="REVERSE: pull config FROM this instance INTO canonical (filtered)")
    ap.add_argument("--all-confirm", action="store_true",
                    help="REVERSE: allow a FULL pull-back with no --only (dangerous, explicit)")
    args = ap.parse_args()

    if not os.path.isdir(canonical):
        sys.exit("canonical config not found: " + canonical)

    if args.from_instance:
        reverse(canonical, instances, args.from_instance, args.only, args.apply, args.all_confirm)
    else:
        forward(canonical, instances, args.only, args.apply, args.instance)


if __name__ == "__main__":
    main()
