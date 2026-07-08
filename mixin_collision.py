#!/usr/bin/env python3
"""mixin_collision.py - cross-mod contested-target report for OaT mixins.

PRE-BOOT diagnostic: find where OptimizationsAndTweaks (OaT) mixins collide with
OTHER pack mods' mixins on the SAME target class/method. The dangerous case is not
"OaT has many @Overwrites" - it is an OaT handler whose TARGET class (or method) is
ALSO targeted by another mod: silent behavior loss (an @Overwrite drops another
mod's @Inject) or an apply-time crash / ordering-dependent breakage.

Two sides:
  * OaT (attacker set): parsed from SOURCE (.java) under the OaT repo - exact
    @Mixin targets + per-handler injection type + target method name.
  * Pack mods (defender set): parsed from BYTECODE via `javap -v -p` on each jar's
    mixin classes (discovered from MANIFEST MixinConfigs + *mixins*.json configs),
    caching per jar (path|mtime|size) so re-runs are fast.

Output: a severity-ranked table (CRITICAL->LOW) of contested target classes, plus
`--json` for machine consumption. See --help.

Accuracy caveats (printed in the report footer too):
  * OaT mixins are conditionally enabled by config flags (asm/Mixin.java enum) - this
    tool assumes all are active (worst case), so some findings may be inert in a given
    config.
  * Method-name matching for CRITICAL relies on both sides using the same (deobf/MCP)
    name in the annotation. refmap-only / srg-named targets can miss (false negative).
  * @Pseudo / soft-target mixins, mixin priority ordering, and @At specificity are NOT
    modeled - a class-level overlap does not always mean a real conflict.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# --- injection-type taxonomy -------------------------------------------------
# EXCLUSIVE / rewriting handlers: they replace or reroute a whole method body or an
# invoke inside it. Two of these on the SAME method = real contention.
REWRITE = {"OVERWRITE", "OVERWRITE_IMPLICIT", "REDIRECT"}
# Ordering-dependent transformers (coexist but result depends on apply order).
MODIFY = {"MODIFYARG", "MODIFYARGS", "MODIFYCONSTANT", "MODIFYVARIABLE", "MODIFYRETURN"}
# Additive callbacks - usually coexist fine.
INJECT = {"INJECT"}
# Passthrough accessors - no behavior change, ignored for collisions.
ACCESSOR = {"ACCESSOR", "INVOKER"}

ALL_HANDLER_ANNOS = REWRITE | MODIFY | INJECT | ACCESSOR

SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# =============================================================================
# OaT side (source parse)
# =============================================================================

def _balanced_parens(text, open_idx):
    """Given index of '(' in text, return substring inside the matching ')'."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i
    return text[open_idx + 1:], len(text)


def _parse_imports(text):
    """simple-name -> FQN map from `import a.b.C;` lines."""
    imp = {}
    for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)\.(\w+)\s*;", text, re.M):
        imp[m.group(2)] = m.group(1) + "." + m.group(2)
    return imp


def _resolve_mixin_targets(args_str, imports):
    """From the inside of @Mixin(...), return (list_of_fqn, list_of_unresolved_simple)."""
    fqns, unresolved = [], []
    # targets = "..." string form (one or many)
    tm = re.search(r"targets\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args_str)
    if tm:
        for s in re.findall(r"\"([^\"]+)\"", tm.group(1)):
            fqns.append(s.replace("/", "."))
    # value = X.class / {A.class,B.class}  OR bare X.class / {A.class,B.class}
    vm = re.search(r"value\s*=\s*(\{[^}]*\}|[\w.]+\.class)", args_str)
    class_scope = vm.group(1) if vm else args_str
    for cm in re.finditer(r"([\w.]+)\.class", class_scope):
        simple = cm.group(1)
        if "." in simple:  # already qualified in source
            fqns.append(simple)
        elif simple in imports:
            fqns.append(imports[simple])
        else:
            unresolved.append(simple)
    return fqns, unresolved


_HANDLER_ANNOS_RE = re.compile(
    r"@(Overwrite|Redirect|Inject|ModifyArgs|ModifyArg|ModifyConstant|ModifyVariable"
    r"|ModifyReturnValue|Accessor|Invoker)\b"
)
# any annotation that already "claims" a member (so it is NOT an implicit overwrite)
_CLAIMED_RE = re.compile(
    r"@(Overwrite|Redirect|Inject|ModifyArgs|ModifyArg|ModifyConstant|ModifyVariable"
    r"|ModifyReturnValue|Accessor|Invoker|Shadow|Intrinsic|Unique|Dynamic|Coerce)\b"
)
_METHOD_DECL_RE = re.compile(
    r"(?m)^[ \t]+"
    r"(?P<mods>(?:public|protected|private|static|final|abstract|synchronized|native|"
    r"strictfp|default)\s+)+"
    r"(?:[\w.$<>\[\], ?]+?\s+)?"
    r"(?P<name>\w+)\s*\("
)
_JAVA_KEYWORDS = {"if", "for", "while", "switch", "return", "new", "catch",
                  "synchronized", "super", "this"}


def _overwrite_target_name(text, after_idx):
    """After an @Overwrite, return the annotated method's name (skip further annos)."""
    for tl in text[after_idx:after_idx + 800].splitlines():
        s = tl.strip()
        if not s or s.startswith(("@", "//", "*", "/*")):
            continue
        nm = re.search(r"\b(\w+)\s*\(", s)
        if nm and nm.group(1) not in _JAVA_KEYWORDS:
            return nm.group(1)
        # a code line that is not a signature (e.g. field) -> stop
        if ";" in s and "(" not in s:
            return None
    return None


def extract_oat_handlers(text, class_simple):
    """Return list of {type, methods:[...]} for one OaT mixin source file.

    Covers annotated handlers AND implicit overwrites (a public, non-static,
    non-constructor method with a body and no claiming annotation - Sponge merges
    it, overwriting the target method of the same signature, e.g. FMLClientHandler
    #queryUser via an implemented interface).
    """
    handlers = []
    # 1) annotated handlers
    for m in _HANDLER_ANNOS_RE.finditer(text):
        typ = m.group(1).upper()
        typ = "MODIFYRETURN" if typ == "MODIFYRETURNVALUE" else typ
        methods = []
        if typ in ("OVERWRITE", "ACCESSOR", "INVOKER"):
            if typ == "OVERWRITE":
                nm = _overwrite_target_name(text, m.end())
                if nm:
                    methods = [nm]
        else:
            op = text.find("(", m.end())
            if op != -1 and op - m.end() < 4:
                inside, _ = _balanced_parens(text, op)
                mm = re.search(r"method\s*=\s*(\{[^}]*\}|\"[^\"]*\")", inside)
                if mm:
                    methods = [x.strip() for x in re.findall(r"\"([^\"(]+)", mm.group(1))
                               if x.strip()]
        handlers.append({"type": typ, "methods": methods})

    # 2) implicit overwrites (public concrete method, no claiming annotation)
    for dm in _METHOD_DECL_RE.finditer(text):
        mods = dm.group("mods")
        name = dm.group("name")
        if "public" not in mods or "static" in mods or "abstract" in mods:
            continue
        if name == class_simple or name in _JAVA_KEYWORDS:
            continue
        # must have a body (a '{' before the next ';')
        semi = text.find(";", dm.end())
        brace = text.find("{", dm.end())
        if brace == -1 or (semi != -1 and semi < brace):
            continue
        # annotations attached to this decl = text since the last ; } {
        boundary = max(text.rfind(c, 0, dm.start()) for c in ";}{")
        attached = text[boundary + 1:dm.start()]
        if _CLAIMED_RE.search(attached):
            continue
        handlers.append({"type": "OVERWRITE_IMPLICIT", "methods": [name]})
    return handlers


def parse_oat_mixins(oat_root):
    """Return (oat_map, stats). oat_map: target_fqn -> list of entries.

    entry = {mixin, file, handlers:[{type, methods:[...]}]}
    """
    mixins_dir = Path(oat_root) / "src/main/java/fr/iamacat/optimizationsandtweaks/mixins"
    if not mixins_dir.is_dir():
        raise SystemExit(f"OaT mixins dir not found: {mixins_dir}")

    oat_map = {}
    files = 0
    mixin_classes = 0
    unresolved_targets = []
    pending = []  # (simple_name, mixin_name, file, handlers) - wildcard-import targets

    for jf in mixins_dir.rglob("*.java"):
        text = jf.read_text(encoding="utf-8", errors="replace")
        imports = _parse_imports(text)

        # collect all @Mixin targets in the file (OaT is ~1 mixin/file)
        targets, unres = [], []
        for m in re.finditer(r"@Mixin\s*\(", text):
            inside, _ = _balanced_parens(text, m.end() - 1)
            fq, un = _resolve_mixin_targets(inside, imports)
            targets.extend(fq)
            unres.extend(un)
        if not targets and not unres:
            continue
        mixin_classes += 1
        files += 1

        handlers = extract_oat_handlers(text, jf.stem)
        mixin_name = "fr.iamacat.optimizationsandtweaks.mixins." + \
            str(jf.relative_to(mixins_dir)).replace("/", ".")[:-5]
        for t in set(targets):
            oat_map.setdefault(t, []).append(
                {"mixin": mixin_name, "file": str(jf), "handlers": handlers}
            )
        for u in unres:
            unresolved_targets.append((jf.name, u))
            pending.append((u, mixin_name, str(jf), handlers))

    stats = {
        "files": files,
        "mixin_classes": mixin_classes,
        "target_classes": len(oat_map),
        "unresolved_targets": unresolved_targets,
    }
    return oat_map, pending, stats


def resolve_pending(oat_map, pending, foreign_targets):
    """Recover wildcard-imported OaT targets by unique class-name suffix match
    against the set of classes some pack mod actually mixes. Returns count recovered."""
    suffix = {}
    for fqn in foreign_targets:
        suffix.setdefault(fqn.rsplit(".", 1)[-1], set()).add(fqn)
    recovered = 0
    for simple, mixin_name, jf, handlers in pending:
        cands = suffix.get(simple)
        if cands and len(cands) == 1:
            fqn = next(iter(cands))
            oat_map.setdefault(fqn, []).append(
                {"mixin": mixin_name, "file": jf, "handlers": handlers}
            )
            recovered += 1
    return recovered


# =============================================================================
# Pack side (bytecode parse via javap)
# =============================================================================

def find_javap():
    for cand in ("/usr/lib/jvm/default-runtime/bin/javap", shutil.which("javap")):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("javap not found (need a JDK).")


def jar_mixin_configs(zf):
    """Return list of mixin-config json entry names in the jar."""
    names = set()
    entries = zf.namelist()
    # 1) MANIFEST MixinConfigs
    try:
        mf = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
        m = re.search(r"MixinConfigs\s*:\s*(.+)", mf)
        if m:
            for c in re.split(r"[,\s]+", m.group(1).strip()):
                if c:
                    names.add(c)
    except KeyError:
        pass
    # 2) glob *mixins*.json / *.mixins.json at any depth (skip refmaps)
    for e in entries:
        base = e.rsplit("/", 1)[-1].lower()
        if base.endswith(".json") and "refmap" not in base and (
            "mixins" in base or base.endswith(".mixins.json")
        ):
            names.add(e)
    return [n for n in names if n in entries or n.endswith(".json")]


def jar_candidate_classes(zf, cfg_names):
    """From mixin configs, collect candidate mixin .class entries.

    Uses the config `package`(s): enumerate every .class under the package path
    (captures both explicit-list and mixin-plugin-discovered classes).
    """
    entries = set(zf.namelist())
    packages = set()
    for cn in cfg_names:
        try:
            data = json.loads(zf.read(cn))
        except (KeyError, json.JSONDecodeError):
            continue
        pkg = data.get("package")
        if pkg:
            packages.add(pkg.replace(".", "/"))
    cands = set()
    for pkg in packages:
        prefix = pkg + "/"
        for e in entries:
            if e.startswith(prefix) and e.endswith(".class") and "$" not in e.rsplit("/", 1)[-1]:
                cands.add(e)
    return sorted(cands), sorted(packages)


def _parse_javap_section(lines):
    """Parse one class's `javap -v -p` lines -> (target_fqns, handlers).

    handlers: list of {type, methods:[...]}
    """
    targets = []
    handlers = []
    current_member = None
    n = len(lines)
    i = 0
    saw_class_mixin = False
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # class-level @Mixin (first mixin.Mixin block, before members)
        if not saw_class_mixin and "mixin.Mixin(" in stripped:
            saw_class_mixin = True
            j = i + 1
            block = []
            while j < n and lines[j].strip() and not lines[j].strip().endswith(")") \
                    and "mixin.Mixin(" not in lines[j]:
                block.append(lines[j])
                j += 1
            if j < n:
                block.append(lines[j])
            btext = "\n".join(block)
            for cm in re.finditer(r"class\s+L([\w/$]+);", btext):
                targets.append(cm.group(1).replace("/", "."))
            for sm in re.finditer(r"\"([\w./$]+)\"", btext):
                targets.append(sm.group(1).replace("/", "."))
            i = j + 1
            continue

        # member signature line: "...  name(args);"  followed by a descriptor: line
        sig = re.match(r"^\s+[\w.$\[\]<>?, ]*\b(\w+)\([^;]*\);\s*$", line)
        if sig and i + 1 < n and lines[i + 1].strip().startswith("descriptor:"):
            current_member = sig.group(1)

        # method-level injection annotation
        for anno, typ in (
            ("mixin.Overwrite(", "OVERWRITE"),
            ("injection.Redirect(", "REDIRECT"),
            ("injection.Inject(", "INJECT"),
            ("injection.ModifyArgs(", "MODIFYARGS"),
            ("injection.ModifyArg(", "MODIFYARG"),
            ("injection.ModifyConstant(", "MODIFYCONSTANT"),
            ("injection.ModifyVariable(", "MODIFYVARIABLE"),
            ("gen.Accessor(", "ACCESSOR"),
            ("gen.Invoker(", "INVOKER"),
        ):
            if anno in stripped:
                methods = []
                if typ == "OVERWRITE" and current_member:
                    methods = [current_member]
                else:
                    # read forward a few lines for method=[...]
                    win = "\n".join(lines[i:i + 8])
                    mm = re.search(r"method=\[([^\]]*)\]", win)
                    if mm:
                        methods = re.findall(r"\"([^\"(]+)", mm.group(1))
                        methods = [x.strip() for x in methods if x.strip()]
                handlers.append({"type": typ, "methods": methods})
                break
        i += 1

    # de-dup targets, keep order
    seen = set()
    tgt = [t for t in targets if not (t in seen or seen.add(t))]
    return tgt, handlers


def scan_jar(jar_path, javap):
    """Return {mixinClass -> {"targets":[...], "handlers":[...]}} for one jar."""
    result = {}
    try:
        zf = zipfile.ZipFile(jar_path)
    except zipfile.BadZipFile:
        return result
    with zf:
        cfgs = jar_mixin_configs(zf)
        if not cfgs:
            return result
        cands, _pkgs = jar_candidate_classes(zf, cfgs)
        if not cands:
            return result
        tmp = tempfile.mkdtemp(prefix="mxcol_")
        try:
            rels = []
            for e in cands:
                dest = Path(tmp) / e
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(e))
                rels.append(e)
            # batch javap in chunks (avoid ARG_MAX); one JVM per chunk
            out = []
            for k in range(0, len(rels), 200):
                chunk = rels[k:k + 200]
                try:
                    p = subprocess.run(
                        [javap, "-v", "-p"] + chunk,
                        cwd=tmp, capture_output=True, text=True, timeout=300,
                    )
                    out.append(p.stdout)
                except (subprocess.TimeoutExpired, OSError):
                    continue
            full = "\n".join(out)
            # split into per-class sections on "Classfile " headers
            sections = re.split(r"^Classfile ", full, flags=re.M)
            for sec in sections:
                if "mixin.Mixin(" not in sec:
                    continue
                lines = sec.splitlines()
                # class name from the unindented `... class <FQN>` declaration line
                # (NOT the indented `class L...;` inside the @Mixin annotation value)
                cm = re.search(r"(?m)^(?:\w+ )*(?:class|interface|enum) ([\w.$]+)", sec)
                if cm:
                    mixin_name = cm.group(1)
                else:
                    header = lines[0] if lines else ""
                    mixin_name = header.strip().replace(".class", "").replace("/", ".").lstrip(".")
                targets, handlers = _parse_javap_section(lines)
                if targets:
                    result[mixin_name] = {"targets": targets, "handlers": handlers}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return result


# =============================================================================
# Cache
# =============================================================================

def cache_key(p):
    st = os.stat(p)
    return f"{os.path.abspath(p)}|{st.st_mtime_ns}|{st.st_size}"


def load_cache(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(path, cache):
    try:
        Path(path).write_text(json.dumps(cache))
    except OSError:
        pass


# =============================================================================
# Collision analysis
# =============================================================================

def handler_type_sets(handlers):
    types = set(h["type"] for h in handlers)
    methods_rewrite = set()
    methods_modify = set()
    methods_inject = set()
    for h in handlers:
        for m in h["methods"]:
            if h["type"] in REWRITE:
                methods_rewrite.add(m)
            elif h["type"] in MODIFY:
                methods_modify.add(m)
            elif h["type"] in INJECT:
                methods_inject.add(m)
    return types, methods_rewrite, methods_modify, methods_inject


def analyze(oat_map, foreign_by_target):
    """foreign_by_target: target_fqn -> list of {mod, mixin, handlers}."""
    findings = []
    for target, oat_entries in oat_map.items():
        others = foreign_by_target.get(target)
        if not others:
            continue

        # aggregate OaT handler picture for this target
        oat_handlers = [h for e in oat_entries for h in e["handlers"]]
        oat_types, oat_rw, oat_mod, oat_inj = handler_type_sets(oat_handlers)
        oat_has_overwrite = bool(oat_types & {"OVERWRITE", "OVERWRITE_IMPLICIT"})
        oat_behavioral = bool(oat_types & (REWRITE | MODIFY | INJECT))
        if not oat_behavioral:
            continue  # OaT only has accessors/shadows here - no behavior contention

        finding_sev = "LOW"
        other_rows = []
        contested_methods = set()
        for o in others:
            o_types, o_rw, o_mod, o_inj = handler_type_sets(o["handlers"])
            o_behavioral = bool(o_types & (REWRITE | MODIFY | INJECT))
            if not o_behavioral:
                continue
            sev = "LOW"
            # CRITICAL: same METHOD rewritten by both
            same_rw = oat_rw & o_rw
            if same_rw:
                sev = "CRITICAL"
                contested_methods |= same_rw
            # HIGH: OaT @Overwrite on a class the other mod also mixes (drops it)
            elif oat_has_overwrite:
                sev = "HIGH"
            # MEDIUM: same method redirected/modified by both (ordering)
            elif (oat_rw | oat_mod) & (o_rw | o_mod):
                sev = "MEDIUM"
                contested_methods |= (oat_rw | oat_mod) & (o_rw | o_mod)
            else:
                sev = "LOW"
            if SEV_ORDER[sev] < SEV_ORDER[finding_sev]:
                finding_sev = sev
            other_rows.append({
                "mod": o["mod"],
                "mixin": o["mixin"],
                "types": sorted(o_types),
                "sev": sev,
            })
        if not other_rows:
            continue
        findings.append({
            "severity": finding_sev,
            "target": target,
            "contested_methods": sorted(contested_methods),
            "oat": {
                "mixins": sorted(set(e["mixin"] for e in oat_entries)),
                "types": sorted(oat_types),
                "rewrite_methods": sorted(oat_rw),
            },
            "others": sorted(other_rows, key=lambda r: (SEV_ORDER[r["sev"]], r["mod"])),
        })

    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["target"]))
    return findings


# =============================================================================
# Output
# =============================================================================

def print_table(findings, oat_stats, scan_stats):
    C = {"CRITICAL": "\033[91m", "HIGH": "\033[93m", "MEDIUM": "\033[96m",
         "LOW": "\033[90m", "R": "\033[0m", "B": "\033[1m"}
    if not sys.stdout.isatty():
        C = {k: "" for k in C}

    print(f"\n{C['B']}== OaT cross-mod mixin collision report =={C['R']}")
    print(f"OaT: {oat_stats['mixin_classes']} mixin classes -> "
          f"{oat_stats['target_classes']} target classes")
    print(f"Pack: {scan_stats['jars']} jars scanned, "
          f"{scan_stats['jars_with_mixins']} with mixin configs, "
          f"{scan_stats['foreign_mixin_classes']} foreign mixin classes, "
          f"{scan_stats['contested']} contested target classes\n")

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["severity"]] += 1

    for f in findings:
        col = C[f["severity"]]
        head = f["target"]
        if f["contested_methods"]:
            head += "#{" + ",".join(f["contested_methods"]) + "}"
        print(f"{col}[{f['severity']:<8}]{C['R']} {C['B']}{head}{C['R']}")
        print(f"    OaT: {'/'.join(f['oat']['types'])}"
              + (f"  (rewrites: {', '.join(f['oat']['rewrite_methods'])})"
                 if f['oat']['rewrite_methods'] else "")
              + f"  [{', '.join(m.split('.')[-1] for m in f['oat']['mixins'])}]")
        for o in f["others"]:
            print(f"    vs {o['mod']}: {'/'.join(o['types'])}  "
                  f"({o['mixin'].split('.')[-1]})  ->{o['sev']}")
        print()

    print(f"{C['B']}Summary:{C['R']} "
          f"{C['CRITICAL']}CRITICAL={counts['CRITICAL']}{C['R']}  "
          f"{C['HIGH']}HIGH={counts['HIGH']}{C['R']}  "
          f"{C['MEDIUM']}MEDIUM={counts['MEDIUM']}{C['R']}  "
          f"{C['LOW']}LOW={counts['LOW']}{C['R']}  "
          f"(total {len(findings)} contested classes)")
    print("\nCaveats: OaT mixins are config-gated (assumed all active); method match "
          "needs same deobf name both sides; @Pseudo/priority/@At specificity not modeled.")


# =============================================================================
# Main
# =============================================================================

def read_env_mods(util_root):
    """Resolve pack mods dir from Cat-Pack-Utilities/.env (INSTANCE_PATH)."""
    env = Path(util_root) / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        m = re.match(r"\s*INSTANCE_PATH\s*=\s*(.+)", line)
        if m:
            base = m.group(1).strip().strip('"')
            return str(Path(base) / "mods")
    return None


def main():
    util_root = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="Cross-mod contested-target report for OaT mixins (pre-boot).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Severity: CRITICAL=same method rewritten by 2+ mods; "
               "HIGH=OaT @Overwrite on a class another mod also mixes; "
               "MEDIUM=same method @Redirect/@ModifyX both sides; LOW=@Inject overlap.",
    )
    ap.add_argument("--oat", default=str(Path.home() / "Documents/GitHub/OptimizationsAndTweaks"),
                    help="OaT repo root (default: ~/Documents/GitHub/OptimizationsAndTweaks)")
    ap.add_argument("--mods", default=None,
                    help="Pack mods dir (default: from .env INSTANCE_PATH + /mods)")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap number of jars scanned (0=all). Logged, no silent truncation.")
    ap.add_argument("--jobs", type=int, default=min(8, (os.cpu_count() or 4)),
                    help="Parallel jar scan workers")
    ap.add_argument("--severity-min", choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    default="LOW", help="Only show findings at/above this severity")
    ap.add_argument("--cache-file", default=str(util_root / ".mixin_collision_cache.json"))
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    mods_dir = args.mods or read_env_mods(util_root)
    if not mods_dir or not Path(mods_dir).is_dir():
        raise SystemExit(f"Pack mods dir not found: {mods_dir}")

    javap = find_javap()
    t0 = time.time()

    # --- OaT side ---
    oat_map, oat_pending, oat_stats = parse_oat_mixins(args.oat)

    # --- Pack side ---
    jars = [str(p) for p in Path(mods_dir).rglob("*.jar")]
    jars = [j for j in jars if "optimizationsandtweaks" not in os.path.basename(j).lower()]
    jars.sort()
    total_jars = len(jars)
    if args.limit and args.limit < total_jars:
        print(f"[cap] scanning {args.limit}/{total_jars} jars (--limit)", file=sys.stderr)
        jars = jars[:args.limit]

    cache = {} if args.no_cache else load_cache(args.cache_file)
    results = {}   # jar -> {mixinClass: {...}}
    to_scan = []
    for j in jars:
        try:
            k = cache_key(j)
        except OSError:
            continue
        if k in cache:
            results[j] = cache[k]
        else:
            to_scan.append((j, k))

    scanned = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(scan_jar, j, javap): (j, k) for j, k in to_scan}
        for fut in as_completed(futs):
            j, k = futs[fut]
            try:
                res = fut.result()
            except Exception:
                res = {}
            results[j] = res
            cache[k] = res
            scanned += 1
            if scanned % 100 == 0:
                print(f"[scan] {scanned}/{len(to_scan)} jars javap'd", file=sys.stderr)

    if not args.no_cache:
        save_cache(args.cache_file, cache)

    # --- build foreign_by_target ---
    foreign_by_target = {}
    foreign_mixin_classes = 0
    jars_with_mixins = 0
    for j, res in results.items():
        if res:
            jars_with_mixins += 1
        mod = os.path.basename(j)
        for mixin_class, info in res.items():
            foreign_mixin_classes += 1
            for t in info["targets"]:
                foreign_by_target.setdefault(t, []).append(
                    {"mod": mod, "mixin": mixin_class, "handlers": info["handlers"]}
                )

    # recover wildcard-imported OaT targets against classes mods actually mix
    recovered = resolve_pending(oat_map, oat_pending, set(foreign_by_target))
    oat_stats["recovered_targets"] = recovered

    findings = analyze(oat_map, foreign_by_target)
    findings = [f for f in findings if SEV_ORDER[f["severity"]] <= SEV_ORDER[args.severity_min]]

    scan_stats = {
        "jars": len(jars),
        "jars_total": total_jars,
        "jars_scanned_now": scanned,
        "jars_cached": len(jars) - len(to_scan),
        "jars_with_mixins": jars_with_mixins,
        "foreign_mixin_classes": foreign_mixin_classes,
        "contested": len(findings),
        "elapsed_s": round(time.time() - t0, 1),
    }

    if args.json:
        print(json.dumps({
            "oat_stats": {k: v for k, v in oat_stats.items() if k != "unresolved_targets"},
            "oat_unresolved_targets": oat_stats["unresolved_targets"],
            "scan_stats": scan_stats,
            "findings": findings,
        }, indent=2))
    else:
        print_table(findings, oat_stats, scan_stats)
        print(f"\n[perf] {scan_stats['elapsed_s']}s  "
              f"({scan_stats['jars_scanned_now']} javap'd, "
              f"{scan_stats['jars_cached']} from cache)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
