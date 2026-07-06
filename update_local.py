#!/usr/bin/env python3
"""
update_local.py — swap freshly-built personal-mod jars into the local instances.

Reads the release manifest (repo + jar_glob per mod) and, for each local instance, removes the
old jars of that mod (by name prefix) and copies the newest build in. Pair with launching the
instance with `-Dmoddirector.devMode=true` so mod-director KEEPS your jar instead of
re-downloading the bundle version (see FileDirector #2 / the devtools skill).

DRY-RUN by default; pass --apply to actually rm/cp.

Usage:
  update_local.py                       # preview all mods -> all instances
  update_local.py --apply
  update_local.py --only OptimizationsAndTweaks --instance TEST --apply
"""

import argparse
import glob
import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
MANIFEST = os.path.join(HOME, "Documents/GitHub/Mod-Sandbox/memory/release-manifest.json")

INSTANCES = {
    # The non-TEST "Biggess Pack Cat Edition V1" instance was deleted (it was a 2nd test instance).
    "TEST":   os.path.join(HOME, "Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST/mods"),
    "server": os.path.join(HOME, "Bureau/SERVERS/Biggess Pack Cat Edition V1 Server/mods"),
}

# Mods not in the release manifest but still swapped locally (devtools). gigafauna is
# TEST-only (devtools; needs geckolib-unofficial-1.0.3.jar there too — install once by hand).
EXTRA_MODS = [
    {"name": "gigafauna", "repo": os.path.join(HOME, "Documents/GitHub/matoulib"),
     "jar_glob": "build/libs/gigafauna-*.jar", "jar_exclude": ["-dev", "-sources", "-api"],
     "instances": ["TEST"]},
]

# The mod-director bootstrapper lives as "!mod-director-launchwrapper-*.jar" (the "!" forces it to
# load first). Local swap works with the "!"-aware prefix/name below (players get it via the pack
# repo + CF project 1359998, separately). Add names here for any other special-named local mod.
LOCAL_OVERRIDES = {
    "FileDirector": {"prefix": "!mod-director-launchwrapper-", "name_prefix": "!"},
}
SKIP_LOCAL = set()


def newest_jar(repo, mod):
    cands = [p for p in glob.glob(os.path.join(os.path.expanduser(repo), mod["jar_glob"]))
             if not any(x in os.path.basename(p) for x in mod.get("jar_exclude", []))]
    return max(cands, key=os.path.getmtime) if cands else None


def prefix_of(mod):
    """Name prefix identifying the mod, from the glob basename before the first '*'."""
    return os.path.basename(mod["jar_glob"]).split("*")[0].lower()


def swap(mod, jar, mods_dir, apply):
    ov = LOCAL_OVERRIDES.get(mod["name"], {})
    pfx = ov.get("prefix", prefix_of(mod)).lower()
    dest = os.path.join(mods_dir, ov.get("name_prefix", "") + os.path.basename(jar))
    removed = []
    for p in glob.glob(os.path.join(mods_dir, "*.jar")):
        name = os.path.basename(p)
        if name.lower().startswith(pfx) and os.path.abspath(p) != os.path.abspath(dest):
            removed.append(name)
            if apply:
                os.remove(p)
    action = "cp" if not os.path.exists(dest) else "overwrite"
    print(f"    {mods_dir.rsplit('/', 2)[-2]}: -{len(removed)} old ({', '.join(removed) or 'none'}) "
          f"{action} {os.path.basename(dest)}")
    if apply:
        shutil.copy2(jar, dest)


def main():
    ap = argparse.ArgumentParser(description="Swap built personal-mod jars into local instances.")
    ap.add_argument("--only", help="one mod name")
    ap.add_argument("--instance", choices=list(INSTANCES), help="one instance")
    ap.add_argument("--apply", action="store_true", help="actually rm/cp (default: dry-run)")
    args = ap.parse_args()

    mods = json.load(open(MANIFEST))["mods"] + EXTRA_MODS
    if args.only:
        mods = [m for m in mods if m["name"] == args.only]
        if not mods:
            sys.exit(f"no mod named {args.only!r}")
    targets = {args.instance: INSTANCES[args.instance]} if args.instance else INSTANCES

    print(f"===== update_local [{'APPLY' if args.apply else 'DRY-RUN'}] "
          f"— {len(mods)} mod(s) x {len(targets)} instance(s) =====")
    print("Remember: launch with -Dmoddirector.devMode=true so mod-director keeps these jars.\n")
    for m in mods:
        if m["name"] in SKIP_LOCAL:
            print(f"{m['name']}: SKIP (special — updated via pack repo + CF, not a local swap)\n")
            continue
        jar = newest_jar(m["repo"], m)
        print(f"{m['name']}: {os.path.basename(jar) if jar else 'NO BUILT JAR (build it first)'}")
        if not jar:
            continue
        allowed = m.get("instances")  # None = all
        for iname, mods_dir in targets.items():
            if allowed and iname not in allowed:
                continue
            if not os.path.isdir(mods_dir):
                print(f"    {iname}: (instance mods dir missing, skip)")
                continue
            swap(m, jar, mods_dir, args.apply)
        print()


if __name__ == "__main__":
    main()
