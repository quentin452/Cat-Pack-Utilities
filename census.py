#!/usr/bin/env python3
"""Rank which modpack mod/subsystem to convert to matoulib's DATA-DRIVEN systems FIRST.

Drives the matoulib RPC devtools endpoint GET /census (added to the RpcServer, see matoulib
fr.iamacat.matoulib.devtools.Census). That endpoint walks the LIVE game registries and returns,
per owning modid, the count of content in the KINDS matoulib can already represent data-driven:
entities (EntityMatouMob), fluids (FluidDefinition), blocks + items (Block/Item defs). It also
reports matoulib's OWN current coverage (its core registries' live sizes) = the baseline the gap
is measured against, plus package-heuristic worldgen-generator / tile-entity tallies.

This script turns that JSON into a ranked "conversion opportunity" report: score = weighted volume
of convertible content per mod, so the mod with the most foreign content matoulib could absorb
bubbles to the top (biggest gap = biggest win). matoulib-owned domains (gigafauna/matoulib) and
vanilla (minecraft) are flagged and excluded from the FOREIGN ranking (they are not conversion
targets), but shown in the coverage/gap summary.

Contract (JSON shape returned by GET /census):
  {
    "ok": true,
    "matoulibDomains": ["gigafauna", "matoulib"],
    "matoulibCoverage": {"mobs":N,"fluids":N,"items":N,"blocks":N,"recipes":N,
                         "materials":N,"guis":N,"oreVeins":N|null},
    "mods": { "<modid>": {"entities":N,"blocks":N,"items":N,"fluids":N,"matoulibOwned":bool}, ... },
    "totals": {"entities":N,"blocks":N,"items":N,"fluids":N,"mods":N},
    "matoulibOwnedTotals": {"entities":N,"blocks":N,"items":N,"fluids":N},
    "worldGenerators": {"reachable":bool,"total":N,"byPackage":{token:N},"classes":[...],"note":...},
    "tileEntities":    {"reachable":bool,"total":N,"byPackage":{token:N},"note":...},
    "errors": [ ... ]
  }

Score (per mod) = entities*5 + fluids*4 + blocks*2 + items*1  (+ worldgen*3 for a package-token
that case-insensitively matches the modid — soft, heuristic). Weights favour the kinds where a
data-driven conversion buys the most (a whole mob/fluid family) over bulk blocks/items.

Drive it in-game (client TEST instance up with -Dmatoulib.rpc=true, at the main menu or in a world):
  python3 census.py
  python3 census.py --top 20
  python3 census.py --json             # raw endpoint JSON, no formatting
  python3 census.py --rpc http://127.0.0.1:25580

Degrades gracefully: if the RPC is not reachable it prints a clear message and exits non-zero.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_RPC = "http://127.0.0.1:25580"

# Weight per convertible KIND: how much a data-driven conversion of one unit is worth.
WEIGHTS = {"entities": 5, "fluids": 4, "blocks": 2, "items": 1}
WORLDGEN_WEIGHT = 3  # soft bonus per generator whose package token matches the modid


def fetch_census(rpc: str, timeout: int = 30) -> dict:
    url = f"{rpc.rstrip('/')}/census"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def score_mod(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * w for k, w in WEIGHTS.items())


def worldgen_bonus(modid: str, by_package: dict) -> int:
    """Soft, heuristic: generators whose package token matches this modid (case-insensitive)."""
    low = modid.lower()
    hits = 0
    for token, n in by_package.items():
        t = str(token).lower()
        if t == low or t in low or low in t:
            hits += int(n)
    return hits * WORLDGEN_WEIGHT


def fmt_int(n) -> str:
    return f"{int(n):,}"


def render(data: dict) -> str:
    lines = []
    domains = set(data.get("matoulibDomains", []))
    mods = data.get("mods", {})
    totals = data.get("totals", {})
    owned = data.get("matoulibOwnedTotals", {})
    cov = data.get("matoulibCoverage", {})
    wg = data.get("worldGenerators", {}) or {}
    wg_by_pkg = wg.get("byPackage", {}) if wg.get("reachable") else {}

    # --- ranking of FOREIGN mods (conversion targets) ---
    rows = []
    for modid, c in mods.items():
        is_owned = bool(c.get("matoulibOwned"))
        is_vanilla = modid == "minecraft"
        base = score_mod(c)
        wgb = worldgen_bonus(modid, wg_by_pkg)
        rows.append(
            {
                "mod": modid,
                "score": base + wgb,
                "base": base,
                "wg": wgb,
                "entities": int(c.get("entities", 0)),
                "blocks": int(c.get("blocks", 0)),
                "items": int(c.get("items", 0)),
                "fluids": int(c.get("fluids", 0)),
                "owned": is_owned,
                "vanilla": is_vanilla,
            }
        )
    foreign = sorted(
        (r for r in rows if not r["owned"] and not r["vanilla"]),
        key=lambda r: r["score"],
        reverse=True,
    )

    lines.append("=" * 78)
    lines.append("matoulib conversion-opportunity ranking (biggest foreign gap = convert first)")
    lines.append("score = entities*5 + fluids*4 + blocks*2 + items*1  (+worldgen*3, heuristic)")
    lines.append("=" * 78)
    hdr = f"{'#':>3}  {'mod':<26}{'score':>8}{'ent':>6}{'fld':>5}{'blk':>7}{'itm':>7}{'wg':>5}"
    lines.append(hdr)
    lines.append("-" * 78)
    for i, r in enumerate(foreign, 1):
        lines.append(
            f"{i:>3}  {r['mod'][:26]:<26}{fmt_int(r['score']):>8}"
            f"{r['entities']:>6}{r['fluids']:>5}{fmt_int(r['blocks']):>7}"
            f"{fmt_int(r['items']):>7}{r['wg']:>5}"
        )
    if not foreign:
        lines.append("   (no foreign mods reported — only vanilla/matoulib content loaded?)")

    # --- coverage vs total (the gap) ---
    lines.append("")
    lines.append("matoulib CURRENT coverage (its own data-driven defs shipped/loaded):")
    order = ["mobs", "fluids", "oreVeins", "items", "blocks", "recipes", "materials", "guis"]
    cov_bits = [f"{k}={cov.get(k)}" for k in order if k in cov]
    lines.append("   " + "  ".join(cov_bits))

    lines.append("")
    lines.append("Convertible content in the WHOLE pack vs what matoulib already owns (the gap):")
    lines.append(f"   {'kind':<10}{'total':>10}{'matoulib-owned':>16}{'foreign gap':>14}")
    for k in ("entities", "fluids", "blocks", "items"):
        tot = int(totals.get(k, 0))
        own = int(owned.get(k, 0))
        lines.append(f"   {k:<10}{fmt_int(tot):>10}{fmt_int(own):>16}{fmt_int(tot - own):>14}")
    lines.append(f"   mods reported: {totals.get('mods', 0)}  (domains matoulib owns: {', '.join(sorted(domains))})")

    # --- worldgen / tile-entity (heuristic, informational) ---
    if wg.get("reachable"):
        top_wg = sorted(wg_by_pkg.items(), key=lambda kv: int(kv[1]), reverse=True)[:8]
        lines.append("")
        lines.append(
            f"worldgen generators: {wg.get('total', 0)} total (package-heuristic owner) — top: "
            + ", ".join(f"{t}:{n}" for t, n in top_wg)
        )
    else:
        lines.append("")
        lines.append(f"worldgen generators: NOT reachable via reflection ({wg.get('note', 'n/a')})")

    errs = data.get("errors") or []
    if errs:
        lines.append("")
        lines.append("endpoint errors (section degraded):")
        for e in errs:
            lines.append(f"   - {e}")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc", default=DEFAULT_RPC, help=f"matoulib RPC base URL (default {DEFAULT_RPC})")
    ap.add_argument("--top", type=int, default=0, help="limit the foreign ranking to the top N mods (0 = all)")
    ap.add_argument("--json", action="store_true", help="print the raw endpoint JSON and exit")
    ap.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    args = ap.parse_args()

    try:
        data = fetch_census(args.rpc, timeout=args.timeout)
    except urllib.error.URLError as e:
        print(
            f"RPC not reachable at {args.rpc}/census : {e.reason}\n"
            "Is the pack TEST client up with -Dmatoulib.rpc=true? (the census endpoint works at the "
            "main menu too — no world needed.)",
            file=sys.stderr,
        )
        return 2
    except (TimeoutError, OSError) as e:
        print(f"RPC not reachable at {args.rpc}/census : {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"RPC returned non-JSON from {args.rpc}/census : {e}", file=sys.stderr)
        return 2

    if not data.get("ok"):
        print(f"census endpoint returned an error: {json.dumps(data, indent=2)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    if args.top and args.top > 0:
        # Trim the ranking by keeping only the top-N foreign mods in the payload before rendering.
        domains = set(data.get("matoulibDomains", []))
        ranked = sorted(
            (
                (m, score_mod(c))
                for m, c in data.get("mods", {}).items()
                if not c.get("matoulibOwned") and m != "minecraft"
            ),
            key=lambda kv: kv[1],
            reverse=True,
        )
        keep = {m for m, _ in ranked[: args.top]} | domains | {"minecraft"}
        data["mods"] = {m: c for m, c in data.get("mods", {}).items() if m in keep}

    print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
