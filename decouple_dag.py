#!/usr/bin/env python3
"""decouple_dag.py — the vanilla base-class DROP cascade, derived (PROTOTYPE).

The problem this answers: a base-class drop (`MatouAnimatedEntity` dropping `extends Entity`
docs/128; `MatouBiome` dropping `extends BiomeGenBase`; `Block` de-realization) is NOT one edit —
the vanilla object is consumed across subsystems, and you must OWN the whole consumer neighbourhood
BEFORE the final `extends` cutover. That is a topological cascade. This tool derives the graph so you
can SEE the next actionable step instead of grep-and-classify by hand (which drifts + mis-counts).

WHAT IS AUTO (this tool):
  - parse the matou source -> edges (matou symbol -> vanilla type), tagged by KIND
    (extends/implements = the drop anchor; type-use; inline-FQN; import-only)
  - reverse index: vanilla TYPE -> its consumers (file / matou class / kind / package)
  - droppable-now: a base-class whose non-bridge consumer set has shrunk toward 0
  - --next: base-classes ranked by droppability x roadmap stage

WHAT STAYS HUMAN (annotations, not code-derivable):
  - the stage/ownership map (reused from vanilla_import_map.SUBSYSTEM_MAP + TYPE_STAGE below):
    "this byte dies at husk", "this tint is render-lane" — decreed in docs/46/107, not in the code
  - the byte-contract judgment per site (relocatable byte-identical vs corruption-risk): the tool
    FLAGS the site, a human classifies it

Reuses vanilla_import_map.py (scanner regexes, repo resolution via repos.json, the stage-map). No
hardcoded paths (packenv). Handles the non-ASCII worldgen files via errors="replace" (they trip a
naive grep as "binary"; see import-budgets.txt ledger note).

Usage:
  python3 decouple_dag.py --repo matoulib-core --target net.minecraft.world.biome.BiomeGenBase
  python3 decouple_dag.py --repo matoulib-core --next
  python3 decouple_dag.py --repo matoulib-core --extends   # every vanilla base-class matou subclasses
"""
import argparse
import collections
import os
import re

import vanilla_import_map as V  # reuse scanner regexes + stage-map + repo resolution (no dup)

# ── Edge-kind extraction (the relational layer vanilla_import_map does NOT do) ──────────────────────
# `class Foo ... extends Bar` / `... implements Baz, Qux` — the DROP ANCHOR: matou TYPE subclasses a
# vanilla base. Superclass may be short (resolved via the file's imports) or already FQN.
CLASS_DECL_RE = re.compile(
    r'\bclass\s+(\w+)\b[^{]*?(?:\bextends\s+([\w.]+))?(?:\s+implements\s+([\w.,\s]+?))?\s*\{',
    re.S,
)
IMPORT_ANY_RE = re.compile(r'^\s*import\s+([\w.]+)\s*;', re.M)

# A vanilla/FML fully-qualified name (same domain as vanilla_import_map).
VANILLA_PREFIX_RE = re.compile(r'^(?:net\.minecraft\w*|net\.minecraftforge|cpw\.mods\.fml)')

# Files that are SUPPOSED to hold the vanilla type (the designated bridge/adapter tier) — excluded
# from "drop-blocker" consumer counts. Heuristic by path; refine as real bridges are catalogued.
BRIDGE_HINT_RE = re.compile(r'(?:bridge|adapter|mixin|devtools)', re.I)

# ── TYPE-level stage overrides (finer than vanilla_import_map's coarse subsystem buckets) ───────────
# The marquee base-classes need per-type gating the "world" bucket is too coarse for. HUMAN-authored;
# keep in sync with the owning docs. Absent here -> falls back to vns_group()/SUBSYSTEM_MAP.
TYPE_STAGE = {
    "net.minecraft.entity.Entity":
        ("S6 (mobs DONE)", "docs/128",
         "MatouAnimatedEntity dropped `extends Entity` c86103e; EntityPlayer anchor remains (docs/112)"),
    "net.minecraft.world.biome.BiomeGenBase":
        ("far-lane: husk(byte)+render(tint)", "docs/61 §0 + 63/64",
         "identity owned (P-a/b/c); drop = own byte-store(husk) + tint(render) THEN drop `extends`"),
    "net.minecraft.world.biome.WorldChunkManager":
        ("far-lane: pairs BiomeGenBase", "docs/61",
         "MatouChunkManager provider; sheds with the BiomeGenBase drop"),
    "net.minecraft.block.Block":
        ("S7", "docs/106",
         "content id surface; de-realization reader-gated (id-spine flag-off today)"),
}


def stage_of(vfqn):
    """(stage, doc, note) for a vanilla type: TYPE_STAGE override, else the coarse SUBSYSTEM_MAP."""
    if vfqn in TYPE_STAGE:
        return TYPE_STAGE[vfqn]
    grp = V.vns_group(vfqn)
    tier, doc, stage, note = V.SUBSYSTEM_MAP.get(grp, ("?", "?", "?", grp))
    return (f"{stage} [{tier}]", doc, note)


def scan_edges(walk_root):
    """Walk the source, return reverse index: vanilla class FQN -> list of edge dicts.

    Each edge = {file, matou_class, kind, pkg, bridge}. kind in {extends, implements, inline, use, import}.
    """
    rev = collections.defaultdict(list)          # vanilla FQN -> [edge, ...]
    subclasses = collections.defaultdict(list)   # vanilla FQN -> [matou class names] (extends/implements)
    for dirpath, _, filenames in os.walk(walk_root):
        for fn in filenames:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, walk_root)
            pkg = rel.split(os.sep)[0] if os.sep in rel else "(top)"
            bridge = bool(BRIDGE_HINT_RE.search(rel))
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            # short-name -> FQN for vanilla imports in THIS file (to resolve `extends Short`)
            imap = {}
            for imp in IMPORT_ANY_RE.findall(raw):
                if VANILLA_PREFIX_RE.match(imp):
                    imap[imp.rsplit(".", 1)[-1]] = imp
            code = V.BLOCK_COMMENT_RE.sub("", raw)  # strip block/javadoc comments like the twin scanner

            def resolve(name):
                name = name.strip()
                if VANILLA_PREFIX_RE.match(name):
                    return V.class_of(name)
                return imap.get(name)  # short -> imported vanilla FQN, or None

            seen = set()  # (fqn, kind) dedup per file

            def add(fqn, kind, matou_class=None):
                if not fqn or (fqn, kind) in seen:
                    return
                seen.add((fqn, kind))
                rev[fqn].append({"file": rel, "matou_class": matou_class, "kind": kind,
                                 "pkg": pkg, "bridge": bridge})

            # extends / implements — the drop anchor
            for m in CLASS_DECL_RE.finditer(code):
                mclass, sup, impls = m.group(1), m.group(2), m.group(3)
                if sup:
                    fqn = resolve(sup)
                    if fqn:
                        add(fqn, "extends", mclass)
                        subclasses[fqn].append(mclass)
                if impls:
                    for it in impls.split(","):
                        fqn = resolve(it)
                        if fqn:
                            add(fqn, "implements", mclass)
                            subclasses[fqn].append(mclass)
            # inline FQN references
            for m in V.FQN_RE.finditer(code):
                add(V.class_of(m.group(1)), "inline")
            # short-form type uses of imported vanilla types (approx: name appears in code beyond import)
            for short, fqn in imap.items():
                if re.search(r'\b' + re.escape(short) + r'\b', code):
                    add(V.class_of(fqn), "use")
            # import-only (recorded so a type imported-but-unused still shows; low signal)
            for fqn in imap.values():
                add(V.class_of(fqn), "import")
    return rev, subclasses


_KIND_RANK = {"inline": 0, "use": 1, "import": 2}  # strongest signal first when collapsing a file


def blockers(edges):
    """Drop-blocker FILES: distinct non-bridge consumer files that must be owned before the extends cut.

    One entry per file (strongest kind kept). Excludes the subclass anchor (extends/implements) and
    `import`-only files (an imported type with no code use is subsumed / low-signal)."""
    best = {}
    for e in edges:
        if e["bridge"] or e["kind"] in ("extends", "implements"):
            continue
        cur = best.get(e["file"])
        if cur is None or _KIND_RANK[e["kind"]] < _KIND_RANK[cur["kind"]]:
            best[e["file"]] = e
    # a file whose ONLY edge is import-only is low-signal — keep it but it ranks last; drop pure-import
    return [e for e in best.values() if e["kind"] != "import"]


def report_target(vfqn, rev):
    edges = rev.get(vfqn, [])
    stage, doc, note = stage_of(vfqn)
    subs = [e["matou_class"] for e in edges if e["kind"] in ("extends", "implements")]
    blk = blockers(edges)
    by_pkg = collections.Counter(e["pkg"] for e in blk)
    print(f"\n=== DROP TARGET: {vfqn} ===")
    print(f"stage: {stage}   ({doc})")
    print(f"note:  {note}")
    print(f"subclasses (extends/implements): {', '.join(sorted(set(subs))) or '(none)'}")
    print(f"drop-blockers (non-bridge consumers to own first): {len(blk)} sites across {len(by_pkg)} pkgs")
    if not blk:
        print("  ✅ DROPPABLE-NOW candidate — no non-bridge consumers left (verify seams by eye).")
    for pkg, n in by_pkg.most_common():
        sites = [e for e in blk if e["pkg"] == pkg]
        kinds = collections.Counter(e["kind"] for e in sites)
        kd = ", ".join(f"{k}:{c}" for k, c in kinds.items())
        print(f"  {pkg:<24} {n:>3}  [{kd}]")
        for e in sites[:6]:
            print(f"       {e['kind']:<10} {e['file']}")
        if len(sites) > 6:
            print(f"       … +{len(sites) - 6} more")
    bridges = [e for e in edges if e["bridge"]]
    if bridges:
        print(f"bridge-tier holders (OK to keep the vanilla type): {len(bridges)} "
              f"({', '.join(sorted({e['file'] for e in bridges})[:4])}…)")


def report_extends(rev, subclasses):
    print("\n=== VANILLA BASE-CLASSES matou SUBCLASSES (drop anchors) ===")
    rows = []
    for vfqn, subs in subclasses.items():
        blk = blockers(rev.get(vfqn, []))
        stage, doc, _ = stage_of(vfqn)
        rows.append((len(blk), vfqn, sorted(set(subs)), stage, doc))
    for nblk, vfqn, subs, stage, doc in sorted(rows):
        flag = "✅ droppable-now?" if nblk == 0 else f"{nblk} blockers"
        print(f"  {flag:<18} {vfqn.split('.')[-1]:<22} <- {', '.join(subs)}   [{stage}] {doc}")


def report_next(rev, subclasses):
    """Rank drop anchors by droppability (fewest blockers first) — the actionable order."""
    print("\n=== NEXT (drop anchors ranked by blockers ascending) ===")
    ranked = sorted(subclasses, key=lambda v: len(blockers(rev.get(v, []))))
    for vfqn in ranked:
        nblk = len(blockers(rev.get(vfqn, [])))
        stage, doc, note = stage_of(vfqn)
        head = "✅ NOW" if nblk == 0 else f"{nblk:>3} blk"
        print(f"  {head}  {vfqn.split('.')[-1]:<22} [{stage}]  {note[:70]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=V.DEFAULT_REPO, help="repo name in repos.json (default matoulib-core)")
    ap.add_argument("--target", help="a vanilla FQN — show its consumer graph + droppability")
    ap.add_argument("--extends", action="store_true", help="list every vanilla base-class matou subclasses")
    ap.add_argument("--next", action="store_true", help="rank drop anchors by droppability")
    args = ap.parse_args()

    V.configure(args.repo)          # reuse: sets V.WALK_ROOT from repos.json path
    rev, subclasses = scan_edges(V.WALK_ROOT)

    if args.target:
        report_target(args.target, rev)
    if args.extends:
        report_extends(rev, subclasses)
    if args.next:
        report_next(rev, subclasses)
    if not (args.target or args.extends or args.next):
        report_extends(rev, subclasses)
        report_next(rev, subclasses)


if __name__ == "__main__":
    main()
