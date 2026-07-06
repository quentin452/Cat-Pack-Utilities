#!/usr/bin/env python3
"""Profile 1.7.10 boot times per mod from FML logs.

Forge 1.7.10 already logs per-mod per-phase timings at DEBUG level
("Bar Step: <phase> - <mod> took X.XXXs" via LoadController/ProgressManager).
This tool aggregates them: no mod, no mixin, works on any instance.

Usage:
  boot_profiler.py <fml-client-latest.log> [--top N] [--json out.json]
"""
import argparse
import collections
import json
import re
import sys

BAR_STEP = re.compile(r"Bar Step: (?P<phase>.+?) - (?P<step>.+?) took (?P<sec>[\d.,]+)s")
BAR_FINISHED = re.compile(r"Bar Finished: (?P<phase>[^-]+?) took (?P<sec>[\d.,]+)s")

MOD_PHASES = {"Construction", "PreInitialization", "Initialization",
              "InterModComms$IMC", "PostInitialization", "LoadComplete", "ModIdMapping"}


def parse(path):
    steps, phases = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = BAR_STEP.search(line)
            if m:
                sec = float(m.group("sec").replace(",", "."))
                steps.append((m.group("phase"), m.group("step"), sec))
                continue
            m = BAR_FINISHED.search(line)
            if m:
                sec = float(m.group("sec").replace(",", "."))
                phases.append((m.group("phase").strip(), sec))
    return steps, phases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json")
    args = ap.parse_args()

    steps, phases = parse(args.log)
    if not steps:
        sys.exit("Aucun 'Bar Step' trouvé — log FML en niveau DEBUG requis (défaut en 1.7.10).")

    per_mod = collections.defaultdict(float)
    per_mod_phase = collections.defaultdict(dict)
    other = collections.defaultdict(float)
    for phase, step, sec in steps:
        if phase in MOD_PHASES:
            per_mod[step] += sec
            per_mod_phase[step][phase] = per_mod_phase[step].get(phase, 0.0) + sec
        else:
            other[f"{phase} :: {step}"] += sec

    ranked = sorted(per_mod.items(), key=lambda kv: -kv[1])
    total_mods = sum(per_mod.values())

    print(f"{'MOD':44} {'TOTAL':>8}  détail par phase")
    print("-" * 100)
    for mod, sec in ranked[: args.top]:
        detail = ", ".join(f"{p.replace('Initialization','Init').replace('Pre','pre').replace('Post','post')}:{s:.2f}"
                           for p, s in sorted(per_mod_phase[mod].items(), key=lambda kv: -kv[1]) if s >= 0.01)
        print(f"{mod[:44]:44} {sec:7.2f}s  {detail}")
    print("-" * 100)
    print(f"{'TOTAL phases mod (tous mods)':44} {total_mods:7.2f}s")
    for phase, sec in sorted(phases, key=lambda kv: -kv[1])[:8]:
        print(f"  Phase '{phase}': {sec:.2f}s")

    hors = sorted(other.items(), key=lambda kv: -kv[1])[:8]
    if hors:
        print("\nHors mods (ressources/textures), top 8 :")
        for k, sec in hors:
            if sec >= 0.05:
                print(f"  {k[:80]:80} {sec:6.2f}s")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"per_mod": {m: {"total": s, "phases": per_mod_phase[m]} for m, s in ranked},
                       "phases": dict(phases)}, f, indent=1, ensure_ascii=False)
        print(f"\nJSON → {args.json}")


if __name__ == "__main__":
    main()
