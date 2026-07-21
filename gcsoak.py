#!/usr/bin/env python3
"""gcsoak.py — S0 GC-soak referee (docs/105 §P7 / §506, the binding zero-alloc gate).

Polls the game's `/gcstats` RPC (GarbageCollectorMXBean counts) at start + after --minutes, and gates on
the MINOR-GC delta over the session. The binding gate is `minorGc delta == 0` — which FULLY holds only at
the husk phase (docs/105 §506: "0 minor GC over the N-minute session — this is the phase where it must
fully hold"). Today the world is still transitional (allocating), so this MEASURES the drift as a referee
skeleton (built at S6 entry per docs/105 §538) — watch the number shrink as ownership progresses; it
becomes a hard gate at the husk.

Works against ANY in-world game with RPC up (client 25580, or a dedicated server on its alt port). Read is
on the RPC thread (no MinecraftServer needed), so it works on a remote client too.

  python3 gcsoak.py [--minutes 5] [--max-minor-gc 0] [--port 25580] [--host 127.0.0.1]

Exit 0 = within budget (minorGc delta <= max), 1 = over budget, 2 = setup error (no RPC / game died).
"""
import argparse
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/iamacat/Documents/GitHub/Cat-Pack-Utilities")
import packenv as E  # noqa: E402


def _gcstats(base):
    with urllib.request.urlopen(base + "/gcstats", timeout=6) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise RuntimeError("gcstats not ok: " + json.dumps(d))
    return d


def main():
    ap = argparse.ArgumentParser(description="S0 GC-soak referee — gate on minor-GC delta over a soak.")
    ap.add_argument("--minutes", type=float, default=5.0, help="soak length (default 5)")
    ap.add_argument("--max-minor-gc", type=int, default=0, help="pass if minor-GC delta <= this (default 0)")
    ap.add_argument("--host", default=getattr(E, "RPC_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=getattr(E, "RPC_PORT", 25580))
    ap.add_argument("--interval", type=float, default=30.0,
                    help="progress/liveness re-poll cadence in seconds (default 30)")
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"

    try:
        s = _gcstats(base)
    except Exception as ex:  # noqa: BLE001
        print(f"SETUP ERROR: no /gcstats at {base} ({ex}) — is a game in-world with RPC up?")
        return 2
    m0, ms0, maj0 = s["minorGc"], s["minorGcMs"], s["majorGc"]
    cols = ", ".join(c["name"] for c in s.get("collectors", []))
    print(f"start: minorGc={m0} minorGcMs={ms0} majorGc={maj0}  collectors=[{cols}]")
    print(f"soaking {a.minutes} min (leave the game idle/in-world)...")
    # Re-poll each interval instead of one blocking sleep: shows the delta climbing + aborts EARLY if the game
    # dies mid-soak (a monolithic sleep would waste the whole window then fail at the end).
    end = time.monotonic() + a.minutes * 60.0
    e = s
    while time.monotonic() < end:
        time.sleep(min(a.interval, max(1.0, end - time.monotonic())))
        try:
            e = _gcstats(base)
        except Exception as ex:  # noqa: BLE001
            print(f"ERROR: /gcstats gone ({ex}) — game died mid-soak, result INVALID.")
            return 2
        left = max(0.0, end - time.monotonic())
        print(f"  +{a.minutes - left / 60.0:.1f} min: minorGc delta={e['minorGc'] - m0}  ({left / 60.0:.1f} min left)")
    dm = e["minorGc"] - m0
    dms = e["minorGcMs"] - ms0
    dmaj = e["majorGc"] - maj0
    ok = dm <= a.max_minor_gc
    print(f"end:   minorGc={e['minorGc']} (+{dm})  minorGcMs +{dms}  majorGc +{dmaj}  over {a.minutes} min")
    verdict = "PASS" if ok else "OVER-BUDGET"
    tail = "" if ok else "  — expected until zero-alloc/husk (docs/105 §506); referee skeleton, watch it shrink"
    print(f"{verdict}: minor-GC delta {dm} (max {a.max_minor_gc}){tail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
