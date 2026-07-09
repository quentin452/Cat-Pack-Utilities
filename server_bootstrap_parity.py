#!/usr/bin/env python3
"""server_bootstrap_parity.py — guard server-side correctness vs the pack's declared bundles.

Two checks (both gate a pack release; see GATE 8 in release_pack.py):

  (1) BOOTSTRAP VERSION PARITY — a few bundle mods also have a hardcoded server LAUNCHER.
  (2) SIDE PARITY — a client-only mod must never be present on the server (--no-side-check to skip).

--- (1) bootstrap version parity ---------------------------------------------------------------

The gap this closes (lwjgl3ify, 2026-07-09): a mod-director bundle mod is installed client AND server
by mod-director from ONE source of truth (curse.bundle.json), so its mods/ jar can't drift. But a few
mods ALSO have a hardcoded launcher/bootstrap on the SERVER side that mod-director does NOT touch:

  lwjgl3ify ships a `lwjgl3ify-<v>-forgePatches.jar` that the server is LAUNCHED with (`3startserver.sh`
  / `.bat` hardcode `-jar lwjgl3ify-<v>-forgePatches.jar`). That version is NOT the bundle's — nothing
  syncs it. Bump lwjgl3ify in the bundle and the serverpack still boots the OLD forgePatches (or, once
  the jar is swapped but the startscript isn't, fails jar-not-found). No existing tool covered it
  (update_local = local-build jars only; pack_sync = fileIDs; config_sync = config) → silent mismatch.

This tool makes the bundle version the single source of truth and asserts the server bootstrap mirrors
it (committed forgePatches jar + both startscripts). Data-driven (BOOTSTRAP_MODS) so a future coupled
mod is one dict, not new code. Report-only by default; `--fix` rewrites the startscript version tokens
byte-preserving (EOL kept: .sh=LF, .bat=CRLF) and drops stale forgePatches jars.

Targets:
  (default)            the CANONICAL serverpack: <PACK>/src/server  — what ships.
  --instance <root>    a live server instance root (e.g. ~/Bureau/SERVERS/...): same check/fix.

Exit codes: 0 = parity OK; 2 = drift found (or --fix could not fully reconcile — e.g. the correct
forgePatches jar is absent and no --forgepatches-src given, since a binary can't be fabricated).
"""
import argparse
import json
import os
import re
import shutil
import sys

import packenv as E

PACK = E.PACK_DIR
MOD_DIRECTOR = E.MOD_DIRECTOR
CURSE_BUNDLE = E.CURSE_BUNDLE
URL_BUNDLE = E.URL_BUNDLE
SERVER_SRC = E.SERVER_SRC

# Bundle mods that ALSO have a hardcoded server-side bootstrap that must mirror the bundle version.
# fileName_re: capture the version from the bundle entry's fileName (the source of truth = what
#   mod-director installs client+server). bootstrap_jar: the committed launcher jar, {v} = version.
#   startscripts: launcher files under the target root that hardcode -jar <bootstrap_jar>.
BOOTSTRAP_MODS = [
    {
        "name": "lwjgl3ify",
        "addonId": 998880,
        "fileName_re": r"lwjgl3ify-(?P<v>[0-9][0-9A-Za-z.\-]*)\.jar",
        "bootstrap_jar": "lwjgl3ify-{v}-forgePatches.jar",
        # matches any versioned forgePatches jar token in a startscript / on disk
        "bootstrap_jar_re": r"lwjgl3ify-[0-9][0-9A-Za-z.\-]*-forgePatches\.jar",
        "startscripts": ["3startserver.sh", "3startserver.bat"],
    },
]


def canonical_version(spec):
    """Version of a bootstrap mod as declared in curse.bundle.json (the source of truth)."""
    with open(CURSE_BUNDLE, encoding="utf-8") as fh:
        blob = fh.read()
    # find the fileName near the mod's addonId (entries are small ordered blocks)
    m = re.search(r'"addonId"\s*:\s*%d\b' % spec["addonId"], blob)
    if not m:
        sys.exit(f"⛔ {spec['name']}: addonId {spec['addonId']} not found in {CURSE_BUNDLE}")
    tail = blob[m.end(): m.end() + 400]
    fn = re.search(r'"fileName"\s*:\s*"([^"]+)"', tail)
    if not fn:
        sys.exit(f"⛔ {spec['name']}: no fileName after addonId {spec['addonId']}")
    v = re.match(spec["fileName_re"], fn.group(1))
    if not v:
        sys.exit(f"⛔ {spec['name']}: fileName {fn.group(1)!r} does not match {spec['fileName_re']}")
    return v.group("v")


def _side(entry):
    """CLIENT / SERVER / '' (both) — mod-director stores it under metadata.side (or top-level)."""
    return str((entry.get("metadata") or {}).get("side", "") or entry.get("side", "")).upper()


def client_only_mods():
    """fileNames of every bundle mod flagged side=CLIENT (curse: fileName; url: URL basename). These
    must NEVER land on the server — mod-director skips them per side, but a mismarked entry or a direct
    jar dropped into src/server/mods would 'teleport' a clientside (rendering) mod onto the server."""
    out = []
    cb = json.load(open(CURSE_BUNDLE, encoding="utf-8"))
    for e in cb.get("curse", []):
        if _side(e) == "CLIENT":
            out.append(e.get("fileName", ""))
    if os.path.isfile(URL_BUNDLE):
        ub = json.load(open(URL_BUNDLE, encoding="utf-8"))
        for e in ub.get("url", []):
            if _side(e) == "CLIENT":
                out.append(e.get("url", "").rstrip("/").split("/")[-1])
    return [f for f in out if f]


def _stem(f):
    return re.sub(r"[-_. ]", "", f.lower()).replace(".jar", "")


def check_side_parity(mods_dir):
    """Flag any client-only mod physically present in a SERVER mods/ dir. Returns problem count."""
    if not os.path.isdir(mods_dir):
        print(f"\n[side-parity] mods dir absent (skip): {mods_dir}")
        return 0
    client = {_stem(f): f for f in client_only_mods()}
    jars = [f for f in os.listdir(mods_dir) if f.endswith(".jar")]
    print(f"\n[side-parity] {len(client)} client-only bundle mod(s) vs {len(jars)} server jar(s) in "
          f"{mods_dir}")
    problems = 0
    for jf in jars:
        js = _stem(jf)
        for cs, cf in client.items():
            if cs and js and (cs == js or (len(cs) >= 14 and cs[:14] == js[:14])):
                print(f"  ✗ CLIENT-ONLY mod on the server: {jf}  (bundle side=CLIENT: {cf})")
                problems += 1
                break
    if not problems:
        print("  ✓ no client-only mod present on the server")
    return problems


def _rewrite_bytes(path, jar_re, want_jar):
    """Byte-preserving replace of every versioned bootstrap-jar token with want_jar. Returns True if
    the file changed. Reads/writes bytes so EOL (LF vs CRLF) and encoding are untouched."""
    with open(path, "rb") as fh:
        data = fh.read()
    new = re.sub(jar_re.encode(), want_jar.encode(), data)
    if new == data:
        return False
    with open(path, "wb") as fh:
        fh.write(new)
    return True


def check_target(root, fix, forgepatches_src):
    """Check (and optionally fix) one target root against every bootstrap mod. Returns problem count."""
    problems = 0
    for spec in BOOTSTRAP_MODS:
        cv = canonical_version(spec)
        want_jar = spec["bootstrap_jar"].format(v=cv)
        print(f"\n[{spec['name']}] canonical bundle version = {cv}  (expect {want_jar})")

        # 1) committed bootstrap jar present at the right version?
        present = [f for f in os.listdir(root) if re.fullmatch(spec["bootstrap_jar_re"], f)]
        if want_jar in present:
            print(f"  ✓ bootstrap jar present: {want_jar}")
            stale = [f for f in present if f != want_jar]
        else:
            stale = present
            if fix and forgepatches_src and os.path.isfile(forgepatches_src):
                shutil.copy2(forgepatches_src, os.path.join(root, want_jar))
                print(f"  ↻ installed {want_jar} from {forgepatches_src}")
            else:
                problems += 1
                hint = "" if forgepatches_src else " (pass --forgepatches-src <jar> to install it)"
                print(f"  ✗ MISSING bootstrap jar {want_jar}; present: {present or '(none)'}{hint}")

        # drop stale forgePatches jars (only when the wanted one is/became present)
        if stale and (want_jar in present or (fix and forgepatches_src)):
            if fix:
                for f in stale:
                    os.remove(os.path.join(root, f))
                print(f"  ↻ removed stale bootstrap jar(s): {stale}")
            else:
                print(f"  ⚠ stale bootstrap jar(s) also present (remove with --fix): {stale}")

        # 2) startscripts reference the right version?
        for sc in spec["startscripts"]:
            p = os.path.join(root, sc)
            if not os.path.isfile(p):
                print(f"  · {sc}: absent (skip)")
                continue
            with open(p, "rb") as fh:
                txt = fh.read().decode("utf-8", "replace")
            refs = set(re.findall(spec["bootstrap_jar_re"], txt))
            if refs == {want_jar}:
                print(f"  ✓ {sc}: -> {want_jar}")
            elif fix:
                changed = _rewrite_bytes(p, spec["bootstrap_jar_re"], want_jar)
                print(f"  ↻ {sc}: {sorted(refs) or '(no ref)'} -> {want_jar}"
                      + ("" if changed else " (no-op)"))
                if not refs:  # nothing to rewrite = the launcher doesn't name the jar; flag it
                    problems += 1
                    print(f"  ✗ {sc}: no forgePatches jar reference to fix")
            else:
                problems += 1
                print(f"  ✗ {sc}: references {sorted(refs) or '(none)'}, want {want_jar}")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Guard server bootstrap version parity vs the bundle.")
    ap.add_argument("--instance", metavar="ROOT",
                    help="check a live server instance root instead of the canonical src/server")
    ap.add_argument("--fix", action="store_true",
                    help="rewrite startscript version tokens (byte-preserving) + drop stale jars")
    ap.add_argument("--forgepatches-src", metavar="JAR",
                    help="path to the correct forgePatches jar to install when absent (for --fix)")
    ap.add_argument("--no-side-check", action="store_true",
                    help="skip the client-only-mods-on-server check (bootstrap version parity only)")
    args = ap.parse_args()

    root = os.path.expanduser(args.instance) if args.instance else SERVER_SRC
    if not os.path.isdir(root):
        sys.exit(f"⛔ target root not found: {root}")
    print(f"=== server bootstrap parity — target: {root}" + ("  [--fix]" if args.fix else ""))

    problems = check_target(root, args.fix, args.forgepatches_src)
    if not args.no_side_check:
        problems += check_side_parity(os.path.join(root, "mods"))
    if problems:
        print(f"\n⛔ {problems} bootstrap parity problem(s) — client<->server / launcher MISMATCH.")
        sys.exit(2)
    print("\n✓ server bootstrap parity OK — launcher jar + startscripts match the bundle version.")


if __name__ == "__main__":
    main()
