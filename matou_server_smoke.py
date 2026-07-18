#!/usr/bin/env python3
"""matou_server_smoke.py — boot a minimal Forge 1.7.10 DEDICATED server with matoulib+gigafauna and
gate on a boot crash.

WHY (the blind spot this fills): every matou test so far runs a client — a solo / integrated world.
An integrated world carries the CLIENT classpath, so a class annotated `@SideOnly(Side.CLIENT)` that is
(mis)referenced from server/common code loads FINE in solo yet crashes on a REAL dedicated server, where
those client classes simply are not present. RPC devtools are CLIENT-only, so they cannot see this class
of bug either. The only faithful catch is an actual headless dedicated boot — which is what this does.

WHAT it does:
  1. materialise a THROWAWAY server dir: symlink the boot essentials (forge universal jar, libraries/,
     minecraft_server jar) from FORGE_SERVER_TEMPLATE (read-only reference — never written to), drop ONLY
     matoulib + gigafauna + geckolib into mods/, write eula=true + a minimal server.properties on a
     non-colliding port (default 25599; the running Prism CLIENT owns RPC 25580 + its own game port).
  2. boot headless: `java -jar <forge-universal> nogui -Dfml.queryResult=confirm`, redirect to boot.log.
  3. poll (process-alive guarded — no zombie loop) until the server prints its ready marker
     (`Done (…s)! For help`), a crash signature appears, the JVM dies, or timeout.
  4. GATE = boot log + FML mod-list, NOT an RPC ping (RPC is client-only). PASS requires: no crash
     (reuses boot_crash_scan.py's SPECIFIC matchers — not bare [FATAL]) AND both matoulib & gigafauna
     reached load (their logger tags appear in the log).
  5. tear the temp dir down (kept on --keep or on failure for triage).

Optional --static-scan: a cheap source-level pre-check that flags a mod's own @SideOnly(Side.CLIENT)
class being imported from a non-client source file. HEURISTIC + advisory (does NOT gate) — the boot is
authoritative. See _static_sideonly_scan for its documented limits.

Exit: 0 = PASS (clean dedicated boot), 1 = FAIL (crash / a matou mod missing / timeout), 2 = setup error.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import packenv as E
import boot_crash_scan as BCS  # REUSE its crash matchers (do not copy them)

# Java 8 is the vanilla 1.7.10 server runtime; overridable via --java. Server needs no LWJGL/natives.
JAVA_DEFAULT = "/usr/lib/jvm/java-8-openjdk/bin/java"
# default matou jars = the Minimal-Matou Prism instance mods (PRISM_ROOT is packenv-owned; not hardcoded)
MINIMAL_MATOU_MODS = os.path.join(E.PRISM_ROOT, "Minimal-Matou", "minecraft", "mods")
# server-ready marker on a dedicated 1.7.10 boot (client SUCCESS markers in boot_crash_scan don't apply)
READY_MARKERS = ('Done (', 'For help, type')


def die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def _one(pattern, where, what):
    hits = sorted(glob.glob(os.path.join(where, pattern)))
    if not hits:
        die("no %s (%s) under template %s" % (what, pattern, where))
    return hits[-1]  # newest-sorted (version strings sort well enough for the forge jar)


def resolve_template(template):
    """Locate the three boot essentials inside the forge-server template dir."""
    if not os.path.isdir(template):
        die("FORGE_SERVER_TEMPLATE not a dir: %s" % template)
    forge = _one("forge-*universal.jar", template, "forge universal jar")
    mcjar = _one("minecraft_server*.jar", template, "minecraft server jar")
    libs = os.path.join(template, "libraries")
    if not os.path.isdir(libs):
        die("template has no libraries/ dir: %s" % libs)
    return forge, mcjar, libs


def _find_unimixins(explicit):
    """UniMixins provides the SpongePowered MixinTweaker (org.spongepowered.asm.launch.MixinTweaker)
    that matoulib's coremod requires to boot — on server AND client alike. Version varies, so glob it
    from the Minimal-Matou mods (same source as the matou jars)."""
    if explicit:
        return explicit
    hits = sorted(glob.glob(os.path.join(MINIMAL_MATOU_MODS, "*unimixins*.jar")))
    return hits[-1] if hits else None


def resolve_mods(args):
    """[(dest_filename, src_path)] for the required jars; error clearly if any is missing.
    UniMixins is included because matoulib's Mixin coremod cannot boot without a MixinTweaker."""
    unimixins = _find_unimixins(args.unimixins)
    # geckolib is OPTIONAL: in the current pack it is shaded into gigafauna-test.jar (the Minimal-Matou instance
    # ships no standalone geckolib jar, and the 2-JVM dedicated boot loads gigafauna fine without it). Only require
    # it if an explicit path or a discoverable jar exists; otherwise skip. Pass --geckolib to force-add one.
    required = [
        ("matoulib-test.jar", args.matoulib or os.path.join(MINIMAL_MATOU_MODS, "matoulib-test.jar")),
        ("gigafauna-test.jar", args.gigafauna or os.path.join(MINIMAL_MATOU_MODS, "gigafauna-test.jar")),
        (os.path.basename(unimixins) if unimixins else "unimixins.jar", unimixins),
    ]
    out = []
    for name, path in required:
        if not path or not os.path.isfile(path):
            die("required jar missing: %s\n  (override with --matoulib/--gigafauna/--unimixins)" % (path or name))
        out.append((name, path))
    gecko = args.geckolib or os.path.join(MINIMAL_MATOU_MODS, "geckolib-unofficial-1.0.3.jar")
    if gecko and os.path.isfile(gecko):
        out.append(("geckolib-unofficial-1.0.3.jar", gecko))
    elif args.geckolib:
        die("geckolib jar not found: %s" % args.geckolib)
    return out


def build_server_dir(forge, mcjar, libs, mods, port):
    """Create a throwaway server dir; symlink boot essentials, copy the 3 mod jars, write eula + props."""
    d = tempfile.mkdtemp(prefix="matou-server-smoke-")
    os.symlink(libs, os.path.join(d, "libraries"))
    os.symlink(mcjar, os.path.join(d, os.path.basename(mcjar)))
    forge_link = os.path.join(d, os.path.basename(forge))
    os.symlink(forge, forge_link)
    os.makedirs(os.path.join(d, "mods"))
    os.makedirs(os.path.join(d, "config"))
    for name, src in mods:
        shutil.copy2(src, os.path.join(d, "mods", name))
    with open(os.path.join(d, "eula.txt"), "w") as fh:
        fh.write("eula=true\n")
    # minimal properties: FLAT world (fast gen), offline, non-colliding port, no MOTD queries
    with open(os.path.join(d, "server.properties"), "w") as fh:
        fh.write("\n".join([
            "server-port=%d" % port,
            "online-mode=false",
            "level-type=FLAT",
            "spawn-npcs=false",
            "spawn-animals=false",
            "spawn-monsters=false",
            "generate-structures=false",
            "max-players=1",
            "view-distance=4",
            "motd=matou-server-smoke",
            "",
        ]))
    return d, forge_link


def scan_logs(server_dir):
    """Aggregate the log surfaces a dedicated boot writes; return (concat_text, [scanned_paths])."""
    candidates = [
        os.path.join(server_dir, "boot.log"),
        os.path.join(server_dir, "logs", "fml-server-latest.log"),
        os.path.join(server_dir, "logs", "latest.log"),
        os.path.join(server_dir, "server.log"),
    ]
    text, seen = "", []
    for p in candidates:
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                text += fh.read()
            seen.append(p)
        except OSError:
            pass
    return text, seen


def mod_loaded(text, modid):
    """FML tags a mod's own log lines `[modid]` and prints `Activating mod <modid>` — either proves load."""
    return ("[%s]" % modid) in text or ("Activating mod %s" % modid) in text


def poll(server_dir, marker, timeout):
    """Poll (process-alive guarded) until READY marker, a crash signature, JVM death, or timeout.
    Returns one of 'ready' | 'crash' | 'dead' | 'timeout'. Aborts early on a crash signature so a
    failed boot doesn't burn the whole timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = subprocess.run(["pgrep", "-f", marker],
                               capture_output=True, text=True).stdout.strip()
        text, _ = scan_logs(server_dir)
        if any(m in text for m in READY_MARKERS):
            return "ready"
        # crash-report file is authoritative; the specific in-log signatures via boot_crash_scan too
        if glob.glob(os.path.join(server_dir, "crash-reports", "*.txt")):
            return "crash"
        r = BCS.scan(os.path.join(server_dir, "boot.log"))
        if r and (r["fatal"] or r["crashed"] or r["culprits"] or r["mixins"]):
            return "crash"
        if not alive:  # process gone AND no ready marker seen ⇒ it died on boot
            time.sleep(2)  # let the final log flush
            text, _ = scan_logs(server_dir)
            return "ready" if any(m in text for m in READY_MARKERS) else "dead"
        time.sleep(4)
    return "timeout"


def kill(marker):
    # set +e semantics: pkill exits 1 when nothing matched — never let that abort us. -f on the UNIQUE
    # sysprop marker (a uuid) can never self-match this python process.
    try:
        subprocess.run(["pkill", "-f", marker], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    time.sleep(2)


# ── optional static @SideOnly pre-scan (advisory, heuristic) ────────────────────────────────────────
def _static_sideonly_scan():
    """Cheap source-level pre-check. For matoulib-core + gigafauna: find each class file whose type is
    class-level annotated `@SideOnly(Side.CLIENT)`, then flag any OTHER source file (not itself
    client-annotated, not under a `.client.` package) that imports/uses that class's simple name.

    LIMITS (documented, on purpose): source-level + simple-name grep → catches only a mod's OWN
    client-only CLASS referenced from its own common code. It does NOT model @SideOnly at method/field
    level, nor vanilla client symbols (Minecraft.getMinecraft() etc.) — those need bytecode + MC
    mappings. So this is an advisory complement; the dedicated BOOT remains the authoritative gate.
    Returns a list of human-readable warning strings (possibly empty)."""
    import json
    import re
    warns = []
    try:
        data = json.load(open(E.REPOS_JSON))
        repos = {r["name"]: os.path.expanduser(r["path"])
                 for r in data.get("repos", data if isinstance(data, list) else [])}
    except Exception as e:
        return ["(static-scan skipped: cannot read repos.json: %s)" % e]

    cls_client = re.compile(r'@SideOnly\s*\(\s*Side\.CLIENT\s*\)\s*(?:public\s+|final\s+|abstract\s+)*'
                            r'(?:class|interface|enum)\s+(\w+)')
    for repo in ("matoulib-core", "gigafauna"):
        root = repos.get(repo)
        if not root:
            continue
        srcroot = os.path.join(root, "src", "main", "java")
        if not os.path.isdir(srcroot):
            continue
        client_classes = {}  # simple name -> file
        files = []
        for dp, _dn, fns in os.walk(srcroot):
            for fn in fns:
                if fn.endswith(".java"):
                    files.append(os.path.join(dp, fn))
        for f in files:
            try:
                head = open(f, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in cls_client.finditer(head):
                # only class-level (annotation immediately preceding the type decl) — finditer on the
                # combined pattern already requires the class keyword to follow the annotation.
                client_classes[m.group(1)] = f
        if not client_classes:
            continue
        for f in files:
            if f in client_classes.values():
                continue
            low = f.replace(os.sep, "/").lower()
            if "/client/" in low:  # a client-only package: fine to touch client-only classes
                continue
            try:
                body = open(f, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "@SideOnly(Side.CLIENT)" in body and "class" in body.split("@SideOnly", 1)[0][-80:]:
                pass  # (rough) whole-class client file — skip below via reference check anyway
            for cname, cfile in client_classes.items():
                if re.search(r'\b' + re.escape(cname) + r'\b', body):
                    warns.append("%s: references client-only class %s (from %s)" %
                                 (os.path.relpath(f, root), cname, os.path.relpath(cfile, root)))
    # dedupe, keep stable order
    seen, out = set(), []
    for w in warns:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Gate: exit 0 = clean dedicated boot (matoulib+gigafauna loaded, no crash); 1 = FAIL.")
    ap.add_argument("--template", default=E.FORGE_SERVER_TEMPLATE,
                    help="forge-server template dir (forge jar + libraries/ + minecraft_server jar)")
    ap.add_argument("--matoulib", help="matoulib jar (default: Minimal-Matou Prism mods)")
    ap.add_argument("--gigafauna", help="gigafauna jar (default: Minimal-Matou Prism mods)")
    ap.add_argument("--geckolib", help="geckolib jar (default: Minimal-Matou Prism mods)")
    ap.add_argument("--unimixins", help="UniMixins jar — MixinTweaker matoulib needs (default: glob Minimal-Matou)")
    ap.add_argument("--java", default=JAVA_DEFAULT, help="java binary (default: java 8)")
    ap.add_argument("--port", type=int, default=25599,
                    help="server-port — must NOT collide with a running client (default 25599)")
    ap.add_argument("--xmx", default="2G", help="max heap (default 2G — a 3-mod boot is light)")
    ap.add_argument("--timeout", type=int, default=240, help="boot timeout seconds (default 240)")
    ap.add_argument("--keep", action="store_true", help="keep the temp server dir (default: remove on PASS)")
    ap.add_argument("--static-scan", action="store_true",
                    help="also run the advisory static @SideOnly(CLIENT) source pre-check")
    args = ap.parse_args()

    if args.static_scan:
        print("── static @SideOnly(Side.CLIENT) pre-scan (advisory, heuristic — boot is authoritative) ──")
        w = _static_sideonly_scan()
        if w:
            for line in w:
                print("  ⚠ " + line)
        else:
            print("  (no own client-only class referenced from non-client source found)")
        print()

    if not os.path.isfile(args.java):
        die("java binary not found: %s (set --java)" % args.java)
    forge, mcjar, libs = resolve_template(args.template)
    mods = resolve_mods(args)

    server_dir, forge_link = build_server_dir(forge, mcjar, libs, mods, args.port)
    marker = "matou.smoke.id=" + uuid.uuid4().hex  # UNIQUE pgrep/pkill key — never self-matches python
    boot_log = os.path.join(server_dir, "boot.log")

    print("forge     : %s" % os.path.basename(forge))
    print("mods      : %s" % ", ".join(n for n, _ in mods))
    print("server dir: %s" % server_dir)
    print("port      : %d   java: %s" % (args.port, args.java))
    print("booting headless (nogui -Dfml.queryResult=confirm) …\n")

    cmd = [args.java, "-Xmx%s" % args.xmx, "-D" + marker,
           "-Dfml.queryResult=confirm",
           "-jar", forge_link, "nogui"]
    verdict = None
    try:
        with open(boot_log, "w") as lf:
            subprocess.Popen(cmd, cwd=server_dir, stdout=lf, stderr=lf,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        result = poll(server_dir, marker, args.timeout)
    finally:
        kill(marker)

    text, scanned = scan_logs(server_dir)

    # crash analysis via the reused matcher
    r = BCS.scan(boot_log) or dict(path=boot_log, culprits=[], symbols={}, mixins=[],
                                   fatal=None, crashed=False, succeeded=False, first_exc=None)
    print("\n── crash scan (boot_crash_scan matchers) ──")
    crash_hit = BCS.report(r)
    crash_report = bool(glob.glob(os.path.join(server_dir, "crash-reports", "*.txt")))
    if crash_report:
        crash_hit = True
        print("  crash-report file present in crash-reports/")

    got_matou = mod_loaded(text, "matoulib")
    got_giga = mod_loaded(text, "gigafauna")
    ready = any(m in text for m in READY_MARKERS)

    print("\n── mod-list / boot gate ──")
    print("  boot outcome    : %s" % result)
    print("  server ready    : %s" % ready)
    print("  matoulib loaded : %s" % got_matou)
    print("  gigafauna loaded: %s" % got_giga)
    print("  logs scanned    : %s" % ", ".join(os.path.relpath(p, server_dir) for p in scanned))

    ok = ready and got_matou and got_giga and not crash_hit
    verdict = "PASS" if ok else "FAIL"
    print("\n=== matou server smoke: %s ===" % verdict)
    if not ok:
        reasons = []
        if crash_hit:
            reasons.append("crash detected")
        if not ready:
            reasons.append("server never reached ready (%s)" % result)
        if not got_matou:
            reasons.append("matoulib not in FML mod-list")
        if not got_giga:
            reasons.append("gigafauna not in FML mod-list")
        print("  reasons: " + "; ".join(reasons))

    if ok and not args.keep:
        shutil.rmtree(server_dir, ignore_errors=True)
    else:
        print("  temp server dir kept for triage: %s" % server_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
