#!/usr/bin/env python3
"""Find every occurrence of a method in a .sparkprofile and print its caller chain + time share.

Companion to spark_parse.py (same raw-protobuf reader): where spark_parse shows the hot-path TREE,
this answers "who calls X and how much" across ALL sites -- e.g. getChunkFromChunkCoords (SRG
func_72964_e) aggregated 11% of the client thread over 9 scattered sites (2026-07-14 analysis).
Obf profiles use SRG names: pass func_XXXXX_x, not the MCP name.

Usage: spark_find.py <file.sparkprofile> <method-substring> [min-pct-per-site]
"""
import sys, os, importlib.util

spec = importlib.util.spec_from_file_location(
    "sp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "spark_parse.py"))
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

fname, needle = sys.argv[1], sys.argv[2]
min_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
root = sp.parse(open(fname, 'rb').read())
for th in [v for f, t, v in root if f == 2 and t == 'b']:
    tn = sp.parse(th)
    if not tn:
        continue
    tname = next((v.decode('utf8', 'replace') for f, t, v in tn if f == 1 and t == 'b'), "?")
    nodes = []
    for f, t, v in tn:
        if f != 3 or t != 'b':
            continue
        sub = sp.parse(v); cls = mth = ""; tm = 0; kids = []
        if sub:
            for sf, st, sv in sub:
                if sf == 3 and st == 'b': cls = sv.decode('utf8', 'replace')
                elif sf == 4 and st == 'b': mth = sv.decode('utf8', 'replace')
                elif sf == 8 and st == 'b': tm = sum(sp.varints(sv))
                elif sf == 9 and st == 'b': kids = sp.varints(sv)
        nodes.append((cls, mth, tm, kids))
    parent = {}
    referenced = set()
    for i, (_, _, _, k) in enumerate(nodes):
        for x in k:
            if x < len(nodes):
                parent[x] = i
                referenced.add(x)
    roots = [i for i in range(len(nodes)) if i not in referenced]
    total = sum(nodes[i][2] for i in roots) or 1
    hits = [i for i, (c, m, _, _) in enumerate(nodes) if needle in m]
    if not hits:
        continue
    agg = sum(nodes[i][2] for i in hits)
    print(f"\n=== {tname} | total={total} | {needle}: {len(hits)} sites, aggregate {agg/total*100:.2f}% ===")
    for i in sorted(hits, key=lambda j: -nodes[j][2]):
        tm = nodes[i][2]
        pct = tm / total * 100
        if pct < min_pct:
            continue
        chain = []
        j = i
        while j in parent and len(chain) < 14:
            j = parent[j]
            c, m, _, _ = nodes[j]
            chain.append(f"{c.split('.')[-1]}.{m}")
        # top child of the hit (where the time goes below)
        kids = [(nodes[k][2], f"{nodes[k][0].split('.')[-1]}.{nodes[k][1]}") for k in nodes[i][3] if k < len(nodes)]
        kids.sort(reverse=True)
        below = f" -> {kids[0][1]} ({kids[0][0]/total*100:.2f}%)" if kids else " (self)"
        print(f"{pct:5.2f}% {nodes[i][0].split('.')[-1]}.{nodes[i][1]}{below}")
        print(f"       via: {' <- '.join(chain[:8])}")
