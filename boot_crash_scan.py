#!/usr/bin/env python3
"""boot_crash_scan.py — attribute a Minecraft/FML boot failure to the culprit mod.

Scans a boot/latest log and surfaces, ranked, WHY a boot died and WHICH mod is at
fault — the signal our poll-liveness monitors kept missing (they only knew a crash
happened, not who caused it). Detects the FML per-mod capture line
`Caught exception from <mod>`, the fatal phase transition, missing symbols
(NoSuchField/NoClassDef/ClassNotFound/NoSuchMethod), and mixin apply failures.

Usage:
  boot_crash_scan.py <boot.log> [<boot.log> ...]
  boot_crash_scan.py <instance-dir>          # scans <dir>/boot*.log + logs/latest.log
Exit code: 0 = boot looks OK / no fatal, 1 = fatal detected, 2 = usage error.
"""
import re
import sys
import os
import glob

# Strip log/STDERR-wrapper prefixes so patterns match whether a line is a direct FML
# log line or routed through FML's WrappedPrintStream ([STDERR]: [...:println:NNN]: ).
_PREFIX = re.compile(r'^.*?WrappedPrintStream:println:\d+\]:\s?|^\[[^\]]*\]\s*\[[^\]]*\]:\s?|^\[[^\]]*\]\s*\[STDERR\]:\s?')

CAUGHT   = re.compile(r'Caught exception from (\S+)')
FATAL    = re.compile(r'Fatal errors were detected during the transition from (\w+) to (\w+)')
# class name must be a dotted/slashed FQCN (avoids catching prose like "...: The class")
SYMBOL   = re.compile(r'\b(NoSuchFieldError|NoClassDefFoundError|ClassNotFoundException|NoSuchMethodError):\s*([\w$]+(?:[./][\w$]+)+)')
MIXIN    = re.compile(r'(InvalidInjectionException|Mixin.*?(?:apply failed|was not applied)|Critical injection failure)')
GAMECR   = re.compile(r'Game crashed|A fatal error has occurred|has crashed')
# success markers = boot got past mod loading / into the client
SUCCESS  = re.compile(r'Forge Mod Loader has successfully loaded|reached the world|Sound engine started|Narrator library|Created: \d+x\d+.* textures?/')


def strip(line):
    return _PREFIX.sub('', line, count=1).rstrip('\n')


def scan(path):
    culprits, symbols, mixins = [], {}, []
    fatal = None
    crashed = succeeded = False
    first_exc = None
    try:
        with open(path, 'r', errors='replace') as fh:
            for raw in fh:
                c = strip(raw)
                m = CAUGHT.search(c)
                if m and m.group(1) not in culprits:
                    culprits.append(m.group(1))
                m = FATAL.search(c)
                if m:
                    fatal = (m.group(1), m.group(2))
                m = SYMBOL.search(c)
                if m:
                    key = (m.group(1), m.group(2).strip())
                    symbols[key] = symbols.get(key, 0) + 1
                    if first_exc is None:
                        first_exc = f"{m.group(1)}: {m.group(2).strip()}"
                if MIXIN.search(c):
                    mixins.append(c.strip()[:140])
                if GAMECR.search(c):
                    crashed = True
                if SUCCESS.search(c):
                    succeeded = True
    except FileNotFoundError:
        return None
    return dict(path=path, culprits=culprits, symbols=symbols, mixins=mixins,
               fatal=fatal, crashed=crashed, succeeded=succeeded, first_exc=first_exc)


def report(r):
    # r['mixins'] is included: the MIXIN regex only matches FAILURE lines (InvalidInjectionException /
    # apply failed / was not applied / critical injection), so a mixin soft-fail that does NOT hard-crash
    # (require=0) still flips the exit non-zero — otherwise boot-verify would PASS a broken mixin apply.
    fatal_hit = bool(r['fatal'] or r['crashed'] or r['culprits'] or r['mixins'])
    tag = "FATAL" if fatal_hit else ("OK" if r['succeeded'] else "INCONCLUSIVE")
    print(f"\n=== {os.path.basename(r['path'])} :: {tag} ===")
    if r['fatal']:
        print(f"  fatal transition : {r['fatal'][0]} -> {r['fatal'][1]} (loading aborted)")
    if r['culprits']:
        print(f"  CULPRIT mod(s)   : {', '.join(r['culprits'])}   <-- start here")
    if r['first_exc']:
        print(f"  first exception  : {r['first_exc']}")
    if r['symbols']:
        print("  missing symbols  :")
        for (typ, name), n in sorted(r['symbols'].items(), key=lambda kv: -kv[1])[:12]:
            print(f"    - {typ}: {name}" + (f"  (x{n})" if n > 1 else ""))
    if r['mixins']:
        print(f"  mixin failures   : {len(r['mixins'])} (e.g. {r['mixins'][0]})")
    if not fatal_hit and r['succeeded']:
        print("  boot reached client init / world — no fatal captured")
    return fatal_hit


def resolve(args):
    logs = []
    for a in args:
        if os.path.isdir(a):
            logs += sorted(glob.glob(os.path.join(a, 'boot*.log')))
            lat = os.path.join(a, 'logs', 'latest.log')
            if os.path.isfile(lat):
                logs.append(lat)
        else:
            logs.append(a)
    return logs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    any_fatal = False
    for p in resolve(sys.argv[1:]):
        r = scan(p)
        if r is None:
            print(f"(missing: {p})")
            continue
        any_fatal |= report(r)
    return 1 if any_fatal else 0


if __name__ == '__main__':
    sys.exit(main())
