#!/usr/bin/env python3
"""Parse a spark .sparkprofile (raw protobuf, no schema needed) into a hot-path tree on stdout.

Local replacement for the spark web viewer (spark.lucko.me is a JS viewer, unusable headless —
CONTROLLER.md). Written 2026-07-13 while chasing the SpawnerAnimals tick burn; format knowledge:
  - top-level field 2 (repeated) = thread trees; field 1 of a thread = its name
  - thread field 3 (repeated)    = FLAT array of stack nodes
  - node: 3=class, 4=method, 8=packed varints (per-window sample times, sum them), 9=packed varints (child INDEXES)
  - roots = nodes never referenced as a child; times are totals (child <= parent)

Usage: spark_parse.py <file.sparkprofile> [--min-pct 1.0] [--depth 12] [--top 30]
       --top prints the flat top-N nodes instead of the tree (good for a quick scan).
"""
import struct, sys, argparse

def parse(buf):
    out = []; i = 0
    try:
        while i < len(buf):
            tag = 0; shift = 0
            while True:
                b = buf[i]; i += 1; tag |= (b & 0x7f) << shift; shift += 7
                if not b & 0x80: break
            f, wt = tag >> 3, tag & 7
            if wt == 0:
                v = 0; shift = 0
                while True:
                    b = buf[i]; i += 1; v |= (b & 0x7f) << shift; shift += 7
                    if not b & 0x80: break
                out.append((f, 'v', v))
            elif wt == 1:
                out.append((f, 'd', struct.unpack('<d', buf[i:i+8])[0])); i += 8
            elif wt == 2:
                ln = 0; shift = 0
                while True:
                    b = buf[i]; i += 1; ln |= (b & 0x7f) << shift; shift += 7
                    if not b & 0x80: break
                out.append((f, 'b', buf[i:i+ln])); i += ln
            elif wt == 5:
                out.append((f, 'f', struct.unpack('<f', buf[i:i+4])[0])); i += 4
            else:
                return None
    except Exception:
        return None
    return out

def varints(b):
    out = []; i = 0
    try:
        while i < len(b):
            v = 0; s = 0
            while True:
                x = b[i]; i += 1; v |= (x & 0x7f) << s; s += 7
                if not x & 0x80: break
            out.append(v)
    except Exception:
        pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--min-pct", type=float, default=1.0)
    ap.add_argument("--depth", type=int, default=12)
    ap.add_argument("--top", type=int, default=0)
    a = ap.parse_args()
    root = parse(open(a.file, 'rb').read())
    if root is None:
        sys.exit("unparseable (compressed or not a .sparkprofile?)")
    for th in [v for f, t, v in root if f == 2 and t == 'b']:
        tn = parse(th)
        if not tn:
            continue
        tname = next((v.decode('utf8', 'replace') for f, t, v in tn if f == 1 and t == 'b'), "?")
        nodes = []
        for f, t, v in tn:
            if f != 3 or t != 'b':
                continue
            sub = parse(v); cls = mth = ""; tm = 0; kids = []
            if sub:
                for sf, st, sv in sub:
                    if sf == 3 and st == 'b': cls = sv.decode('utf8', 'replace')
                    elif sf == 4 and st == 'b': mth = sv.decode('utf8', 'replace')
                    elif sf == 8 and st == 'b': tm = sum(varints(sv))
                    elif sf == 9 and st == 'b': kids = varints(sv)
            nodes.append((cls, mth, tm, kids))
        if a.top:
            print(f"\n=== {tname} — top {a.top} nodes ===")
            for cls, mth, tm, _ in sorted(nodes, key=lambda n: -n[2])[:a.top]:
                print(f"{tm:>9}  {cls}.{mth}")
            continue
        referenced = set()
        for _, _, _, k in nodes:
            referenced.update(x for x in k if x < len(nodes))
        roots = [i for i in range(len(nodes)) if i not in referenced]
        total = sum(nodes[i][2] for i in roots) or 1
        print(f"\n=== {tname} | nodes={len(nodes)} total={total} ===")
        def walk(i, depth):
            cls, mth, tm, kids = nodes[i]
            if tm / total * 100 < a.min_pct:
                return
            print(f"{'  ' * depth}{tm/total*100:5.1f}% {tm:>7} {cls.split('.')[-1]}.{mth}")
            if depth < a.depth:
                for k in sorted(set(kids), key=lambda j: -(nodes[j][2] if j < len(nodes) else 0)):
                    if k < len(nodes):
                        walk(k, depth + 1)
        for r in sorted(roots, key=lambda i: -nodes[i][2])[:3]:
            walk(r, 0)

if __name__ == "__main__":
    main()
