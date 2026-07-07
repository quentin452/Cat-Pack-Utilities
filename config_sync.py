#!/usr/bin/env python3
"""
config_sync.py — propagate a pack game-config CHANGE from the CANONICAL source into the local
test instances (client + server). Fills the gap the mod-based tools leave: update_local=jars,
pack_sync=mod fileIDs, bundle_check=fetchability — NONE touch game config.

Workflow for a config change (perf tune / fix, e.g. CoFH ore density, BoP feature):
  1. edit the CANONICAL: privates-minecraft-modpack/MODPACKS/<pack>/src/common/config  (NEVER the instance)
  2. this tool: mirror canonical -> instances, validate in-game
  3. changelog-pending Pack section: **config updated**  +  bump pack version
  4. ship: generate_modpack_zips packages src/common/config -> overrides/config (client AND serverpack)

  config_sync.py                         # DIFF: files where an instance differs from canonical
  config_sync.py --only cofh/world       # limit to a subpath (file or dir)
  config_sync.py --apply                 # copy canonical -> instances (byte copy, CRLF preserved)
  config_sync.py --apply --only cofh/world/ThermalFoundation-Ores.json
  config_sync.py --instance TEST         # one instance only (TEST | server)

Direction is canonical -> instance ONLY (canonical = source of truth). A DIFFERS line for a file you
never edited in the instance means the instance drifted (someone edited it directly = the anti-pattern).
Non-destructive: never deletes instance-only files.
"""
import argparse
import filecmp
import os
import shutil
import sys

HOME = os.path.expanduser("~")
PACK = "Biggess Pack Cat Edition"
CANONICAL = os.path.join(HOME, "Documents/GitHub/privates-minecraft-modpack/MODPACKS", PACK, "src/common/config")
INSTANCES = {
    "TEST":   os.path.join(HOME, "Documents/curseforge/minecraft/Instances", PACK + " V1 TEST", "config"),
    "server": os.path.join(HOME, "Bureau/SERVERS", PACK + " V1 Server", "config"),
}


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="config subpath (file or dir) to limit to")
    ap.add_argument("--apply", action="store_true", help="copy canonical -> instances (else preview)")
    ap.add_argument("--instance", choices=list(INSTANCES), default=None, help="one instance only")
    args = ap.parse_args()

    if not os.path.isdir(CANONICAL):
        sys.exit("canonical config not found: " + CANONICAL)
    rels = sorted(set(rel_files(CANONICAL, args.only)))
    if not rels:
        sys.exit("no canonical files under: " + (args.only or "config"))
    insts = {args.instance: INSTANCES[args.instance]} if args.instance else INSTANCES

    grand = 0
    for name, idir in insts.items():
        print(f"=== {name}  ({idir}) ===")
        if not os.path.isdir(idir):
            print("  (instance config dir missing — skip)")
            continue
        n_diff = n_apply = 0
        for rel in rels:
            csrc = os.path.join(CANONICAL, rel)
            idst = os.path.join(idir, rel)
            if not os.path.exists(idst):
                differ, tag = True, "NEW    "
            elif not filecmp.cmp(csrc, idst, shallow=False):
                differ, tag = True, "DIFFERS"
            else:
                differ = False
            if differ:
                n_diff += 1
                print(f"  {tag}: {rel}")
                if args.apply:
                    os.makedirs(os.path.dirname(idst), exist_ok=True)
                    shutil.copy2(csrc, idst)  # byte copy: preserves CRLF (no LF normalization) + mtime
                    n_apply += 1
        grand += n_diff
        print(f"  -> {n_diff} differing" + (f", {n_apply} copied" if args.apply else " (preview — pass --apply)"))
    if not args.apply and grand:
        print("\nRun with --apply to mirror canonical -> instances, then validate in-game.")
    if args.apply:
        print("\nDone. Next: changelog-pending **config updated** + bump version + generate_modpack_zips to ship.")


if __name__ == "__main__":
    main()
