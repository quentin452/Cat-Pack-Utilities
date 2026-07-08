#!/usr/bin/env python3
"""Build a fast-boot MINIMAL instance to de-risk a single mixin/mod before the full pack.

Booting the ~900-mod pack to test one mixin costs ~8 min. This assembles a throwaway
instance = the proven coremod baseline (UniMixins/FPLib/gtnhlib/lwjgl3ify/geckolib/gigafauna)
+ your OaT build + the target mod(s) + extras (e.g. archaicfix for cascade logging), reusing
the pack's launch plumbing (classpath, forgePatches, config). It boots in ~90 s.

1.7.10 mods do NOT reliably declare their deps in mcmod.info, so deps are resolved by
BOOT-AND-HEAL: boot, read the crash for a missing class/mod, copy the pack jar that provides
it, reboot — until it reaches a world (or stops making progress).

Usage:
  make_minimal_instance.py --mod manametalmod --extra archaicfix --extra endlessids \
      --oat <path-to-oat.jar> --autoworld default --boot

  # then drive it over RPC on 127.0.0.1:25580 exactly like the pack TEST.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

HOME = os.path.expanduser("~")
INSTANCES = os.path.join(HOME, "Documents/curseforge/minecraft/Instances")
PACK = os.path.join(INSTANCES, "Biggess Pack Cat Edition V1 TEST")
BASE = os.path.join(INSTANCES, "Minimal-Pathfinding")  # plumbing + coremod baseline template
JAVA = "/usr/lib/jvm/default-runtime/bin/java"
RPC = "http://127.0.0.1:25580/ping"

# The coremod/lib jars that MUST be present for anything to boot (matched as name substrings
# against the BASE instance's mods/). Everything else a target needs is resolved by --heal.
COREMOD_BASELINE = [
    "unimixins", "falsepatternlib", "gtnhlib", "lwjgl3ify", "geckolib",
]


def die(msg):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)


def find_jar(mods_dir, substr):
    """The single pack jar whose filename contains substr (case-insensitive), or None."""
    hits = [j for j in glob.glob(os.path.join(mods_dir, "*.jar"))
            if substr.lower() in os.path.basename(j).lower()]
    if len(hits) > 1:
        # Prefer the shortest name (usually the base mod, not an addon).
        hits.sort(key=lambda p: len(os.path.basename(p)))
    return hits[0] if hits else None


def class_to_jar_index(mods_dir, cache):
    """Lazily build {internal/class/Name -> jar path} over every pack jar (for --heal)."""
    if cache:
        return cache
    print("  indexing pack jars for class lookup (one-time)…")
    for jar in glob.glob(os.path.join(mods_dir, "*.jar")):
        try:
            with zipfile.ZipFile(jar) as z:
                for n in z.namelist():
                    if n.endswith(".class"):
                        cache.setdefault(n[:-6], jar)  # first jar wins
        except zipfile.BadZipFile:
            continue
    return cache


def missing_mods_from_log(text):
    """FML missing-dependency modids, e.g. endlessids requires [chunkapi]. The most common
    de-risk failure: a lib the target needs isn't in the baseline. Detected WITHOUT a human
    from FML's own log lines (this is the 'chunkapi : minimum version required' fatal screen)."""
    mods = []
    # "The mod X (Name) requires mods [chunkapi, foo] to be available"
    for grp in re.findall(r"requires mods \[([\w, ]+)\] to be available", text):
        mods += [m.strip() for m in grp.split(",")]
    # GuiFatalError body: "chunkapi : minimum version required is 0.6.4"
    for m in re.findall(r"^\s*([\w-]+)\s*:\s*minimum version required", text, re.M):
        mods.append(m)
    # "Missing Mods: chunkapi@[0.6.4,)"
    for m in re.findall(r"required-after:([\w-]+)@", text):
        mods.append(m)
    seen = []
    for m in mods:
        if m and m not in seen and m.lower() not in ("minecraft", "forge", "fml", "mcp"):
            seen.append(m)
    return seen


def missing_class_from_crash(crash_text):
    """The first mod class the JVM couldn't find, or None (secondary heal after missing-mods)."""
    for pat in (r"NoClassDefFoundError: ([\w/$]+)",
                r"ClassNotFoundException:\s*(?:Exception[^']*')?([\w.$]+)"):
        m = re.search(pat, crash_text)
        if m:
            c = m.group(1).replace(".", "/")
            if "/" in c and not c.startswith(("net/minecraft/", "cpw/", "net/minecraftforge/",
                                              "java/", "javax/", "org/", "scala/", "com/google/")):
                return c
    return None


def modid_to_jar_index(mods_dir, cache):
    """{modid -> jar} from each pack jar's mcmod.info (malformed ones skipped; used to heal a
    missing FML dependency modid to the jar that provides it)."""
    if cache:
        return cache
    import json
    for jar in glob.glob(os.path.join(mods_dir, "*.jar")):
        try:
            with zipfile.ZipFile(jar) as z:
                if "mcmod.info" not in z.namelist():
                    continue
                raw = z.read("mcmod.info").decode("utf-8", "ignore")
        except (zipfile.BadZipFile, KeyError):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            # malformed mcmod.info (common) -> fall back to a modid regex over the raw text
            for mid in re.findall(r'"modid"\s*:\s*"([\w-]+)"', raw):
                cache.setdefault(mid, jar)
            continue
        entries = data if isinstance(data, list) else data.get("modList", [])
        for e in entries:
            mid = e.get("modid")
            if mid:
                cache.setdefault(mid, jar)
    return cache


def resolve_mod_jar(modid, mods_dir, modid_idx):
    """The pack jar providing modid: by mcmod.info modid, else by jar-name substring."""
    j = modid_idx.get(modid)
    if j:
        return j
    return find_jar(mods_dir, modid)


def build_argfile(base_arg, dest_dir, autoworld):
    """Copy BASE's argfile, repoint --gameDir at dest_dir and set the autoworld type."""
    with open(base_arg, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().strip('"') == "--gameDir" and i + 1 < len(lines):
            out.append(ln)
            out.append('"%s"\n' % dest_dir)
            i += 2
            continue
        if "matoulib.autoworld.type=" in ln:
            ln = re.sub(r"autoworld\.type=\w+", "autoworld.type=" + autoworld, ln)
        out.append(ln)
        i += 1
    dest_arg = os.path.join(dest_dir, "minimal.arg")
    with open(dest_arg, "w", encoding="utf-8") as f:
        f.writelines(out)
    return dest_arg


# Kept SPECIFIC: a bare "could not be found" false-matches benign warns (e.g. BoP's
# "version.properties file could not be found"). Only real FML fatal-dependency phrasing.
FATAL_SIGNATURES = ("requires mods [", "The mods and versions listed below could not be found",
                    "Missing Mods:", "GuiFatalErrorScreen", "A fatal error has occurred while")


def poll_boot(dest_dir, argname, timeout=420):
    """Poll until the RPC reports inWorld, a fatal FML/dep error appears, the JVM dies, or timeout.
    Returns 'world'|'deps'|'crash'|'timeout'. Aborts EARLY on a fatal signature in the log so a
    missing-dependency doesn't burn the full timeout waiting for a world that will never load."""
    boot_log = os.path.join(dest_dir, "boot.log")
    for _ in range(timeout // 6):
        alive = subprocess.run(
            ["pgrep", "-fa", argname], capture_output=True, text=True).stdout
        try:
            text = open(boot_log, encoding="utf-8", errors="ignore").read()
        except OSError:
            text = ""
        if any(s in text for s in FATAL_SIGNATURES):
            return "deps"
        if "bin/java" not in alive:
            return "crash"
        try:
            p = urllib.request.urlopen(RPC, timeout=3).read().decode()
            if '"inWorld":true' in p:
                return "world"
        except Exception:
            pass
        time.sleep(6)
    return "timeout"


def kill_instance(argname):
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "bin/java" in line and argname in line:
            try:
                os.kill(int(line.split()[0]), 9)
            except (ValueError, ProcessLookupError):
                pass
    time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mod", action="append", required=True, help="target mod (name substring in the pack)")
    ap.add_argument("--extra", action="append", default=[], help="extra mod/lib to include (substring)")
    ap.add_argument("--oat", help="path to the OaT jar under test (default: the pack's current OaT)")
    ap.add_argument("--giga", help="path to the gigafauna (matoulib RPC) jar (default: pack/BASE copy)")
    ap.add_argument("--name", help="instance name suffix (default: first --mod)")
    ap.add_argument("--autoworld", default="default", choices=["default", "superflat"])
    ap.add_argument("--pack", default=PACK, help="source instance for mod jars")
    ap.add_argument("--boot", action="store_true", help="boot + heal missing deps after assembling")
    ap.add_argument("--sound", action="store_true",
                    help="keep game sound ON (default: OFF — test instances don't need music/sound)")
    ap.add_argument("--heal-max", type=int, default=6, help="max heal reboots")
    args = ap.parse_args()

    if not os.path.isdir(BASE):
        die("base template instance not found: %s (build Minimal-Pathfinding first)" % BASE)
    pack_mods = os.path.join(args.pack, "mods")
    base_mods = os.path.join(BASE, "mods")

    name = args.name or args.mod[0]
    dest = os.path.join(INSTANCES, "Minimal-DeRisk-" + re.sub(r"[^\w.-]", "_", name))
    dest_mods = os.path.join(dest, "mods")

    # Fresh mods/, cloned plumbing (config from the PACK = canonical block-id-bits etc.; saves fresh).
    if os.path.isdir(dest):
        shutil.rmtree(os.path.join(dest, "mods"), ignore_errors=True)
        shutil.rmtree(os.path.join(dest, "saves"), ignore_errors=True)
    os.makedirs(dest_mods, exist_ok=True)
    for sub in ("falsepattern", "natives"):
        src = os.path.join(BASE, sub)
        if os.path.isdir(src) and not os.path.isdir(os.path.join(dest, sub)):
            shutil.copytree(src, os.path.join(dest, sub))
    # config: prefer the pack's canonical config (has the target's config + matching id bits).
    dest_cfg = os.path.join(dest, "config")
    if not os.path.isdir(dest_cfg):
        src_cfg = os.path.join(args.pack, "config")
        print("  copying canonical config from pack (large, one-time)…")
        shutil.copytree(src_cfg, dest_cfg)

    # Assemble mods/: coremod baseline (from BASE) + OaT + gigafauna + targets + extras.
    picked = []

    def add(jar, label):
        if jar and os.path.isfile(jar):
            shutil.copy(jar, os.path.join(dest_mods, os.path.basename(jar)))
            picked.append("%-14s %s" % (label, os.path.basename(jar)))
            return True
        return False

    for sub in COREMOD_BASELINE:
        j = find_jar(base_mods, sub)
        if not add(j, "coremod"):
            die("baseline coremod missing from %s: %s" % (base_mods, sub))

    oat = args.oat or find_jar(pack_mods, "optimizationsandtweaks")
    if not add(oat, "oat"):
        die("OaT jar not found (pass --oat)")
    giga = args.giga or find_jar(base_mods, "gigafauna") or find_jar(pack_mods, "gigafauna")
    add(giga, "gigafauna")  # optional (RPC/autoworld driver) — warn only

    for sub in args.mod:
        j = find_jar(pack_mods, sub)
        if not add(j, "target"):
            die("target mod not found in pack: %s" % sub)
    for sub in args.extra:
        j = find_jar(pack_mods, sub)
        if not add(j, "extra"):
            print("  WARN: extra not found, skipping:", sub)

    dest_arg = build_argfile(os.path.join(BASE, "minimal.arg"), dest, args.autoworld)

    # Sound OFF by default (test instances don't need music/sound; --sound keeps it). Seed options.txt
    # from BASE if absent, then force the sound categories to 0.0 so the boot is silent.
    if not args.sound:
        opt = os.path.join(dest, "options.txt")
        base_opt = os.path.join(BASE, "options.txt")
        if not os.path.isfile(opt) and os.path.isfile(base_opt):
            shutil.copy(base_opt, opt)
        off = {"soundCategory_master": "0.0", "soundCategory_music": "0.0"}
        lines = open(opt, encoding="utf-8").read().splitlines() if os.path.isfile(opt) else []
        seen = set()
        for i, ln in enumerate(lines):
            k = ln.split(":", 1)[0]
            if k in off:
                lines[i] = k + ":" + off[k]
                seen.add(k)
        lines += [k + ":" + v for k, v in off.items() if k not in seen]
        with open(opt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("  sound OFF (master+music=0.0; pass --sound to keep)")

    print("\nAssembled %s:" % dest)
    for p in picked:
        print("  " + p)
    print("argfile:", dest_arg)

    if not args.boot:
        print("\nBoot it with:  cd '%s' && setsid %s '@%s' > boot.log 2>&1 &" % (dest, JAVA, dest_arg))
        return

    # Boot-and-heal loop.
    argname = os.path.basename(dest_arg)
    boot_log = os.path.join(dest, "boot.log")
    fml_log = os.path.join(dest, "logs", "fml-client-latest.log")
    class_idx, modid_idx = {}, {}
    healed = set()
    for attempt in range(args.heal_max + 1):
        kill_instance(argname)
        with open(boot_log, "w") as lf:
            subprocess.Popen([JAVA, "@" + dest_arg], cwd=dest, stdout=lf, stderr=lf,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        print("\n[boot %d] launched, polling…" % attempt)
        status = poll_boot(dest, argname)
        if status == "world":
            print("[boot %d] IN WORLD — de-risk instance ready (RPC on 127.0.0.1:25580)." % attempt)
            return
        kill_instance(argname)
        # Gather every diagnostic surface: boot.log, the FML log, and the newest crash report.
        text = ""
        for p in (boot_log, fml_log):
            if os.path.isfile(p):
                text += open(p, encoding="utf-8", errors="ignore").read()
        crashes = sorted(glob.glob(os.path.join(dest, "crash-reports", "*.txt")), key=os.path.getmtime)
        if crashes:
            text += open(crashes[-1], encoding="utf-8", errors="ignore").read()

        # 1) FML missing-mod dependency (the common case: a lib the target needs). Resolve modid->jar.
        added = False
        for modid in missing_mods_from_log(text):
            if modid in healed:
                continue
            modid_idx = modid_to_jar_index(pack_mods, modid_idx)
            provider = resolve_mod_jar(modid, pack_mods, modid_idx)
            if provider:
                shutil.copy(provider, os.path.join(dest_mods, os.path.basename(provider)))
                healed.add(modid)
                added = True
                print("[boot %d] healed missing mod '%s' -> added %s" % (attempt, modid, os.path.basename(provider)))
            else:
                print("[boot %d] WARN: missing mod '%s' but no pack jar provides it" % (attempt, modid))
        if added:
            continue
        # 2) Missing class fallback.
        cls = missing_class_from_crash(text)
        if cls and cls not in healed:
            class_idx = class_to_jar_index(pack_mods, class_idx)
            provider = class_idx.get(cls)
            if provider:
                shutil.copy(provider, os.path.join(dest_mods, os.path.basename(provider)))
                healed.add(cls)
                print("[boot %d] healed missing class %s -> added %s" % (attempt, cls, os.path.basename(provider)))
                continue
        print("[boot %d] status=%s, nothing auto-healable. Inspect %s" % (attempt, status, boot_log))
        return
    die("still not booting after %d heal attempts" % args.heal_max)


if __name__ == "__main__":
    main()
