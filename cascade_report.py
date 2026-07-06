#!/usr/bin/env python3
"""Aggregate ArchaicFix cascading-worldgen warnings from 1.7.10 logs.

ArchaicFix logs each event as:
  [ArchaicFix/]: <Mod> loaded a new chunk [x, z] in dimension <id> (<name>)
  while populating chunk [x, z], causing cascading worldgen lag.

This ranks offenders by (mod, dimension) — the fix priority list for
per-mod offset patches (OptimizationsAndTweaks) or the deferred-generation net.

Usage: cascade_report.py <log...> [--json out.json]
"""
import argparse
import collections
import json
import re

LINE = re.compile(
    r"\[ArchaicFix/?\]: (?P<mod>.+?) loaded a new chunk \[(?P<cx>-?\d+), (?P<cz>-?\d+)\] "
    r"in dimension (?P<dim>-?\d+) \((?P<dimname>.+?)\) while populating chunk")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--json")
    args = ap.parse_args()

    by_mod = collections.Counter()
    by_mod_dim = collections.Counter()
    dim_names = {}
    total = 0
    for path in args.logs:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE.search(line)
                if not m:
                    continue
                total += 1
                by_mod[m.group("mod")] += 1
                by_mod_dim[(m.group("mod"), m.group("dim"))] += 1
                dim_names[m.group("dim")] = m.group("dimname")

    if not total:
        print("Aucun warning ArchaicFix trouvé (ArchaicFix requis, et il faut générer des chunks).")
        return

    print(f"{total} cascades — par mod :")
    for mod, n in by_mod.most_common():
        dims = ", ".join(f"{dim_names[d]}({d}):{c}" for (mo, d), c in by_mod_dim.most_common() if mo == mod)
        print(f"  {mod:32} {n:6}  [{dims}]")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {"total": total, "by_mod": dict(by_mod),
                 "by_mod_dim": {f"{m}@{dim_names[d]}": c for (m, d), c in by_mod_dim.items()}},
                f, indent=1, ensure_ascii=False)
        print(f"JSON → {args.json}")


if __name__ == "__main__":
    main()
