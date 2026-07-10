#!/usr/bin/env python3
"""chunkgen_bench.py — RIGOROUS chunk-generation benchmark harness for the async-chunk rewrite.

Feeds the per-phase GATES of docs/32 (async chunk gen). This project has repeatedly been burned by
noisy single-shot A/B benchmarks ("SUGGESTIF pas concluant", "machine trop chargée fausse tout") — so
rigor IS the deliverable, not raw numbers. Every property below exists to make a perf delta TRUSTABLE:

  * Fixed workload, reproducible across boots: a DETERMINISTIC set of origins (anchor + k*stride) in a
    fixed-seed world, same region radius. Same origins are replayed flag-OFF and flag-ON -> per-origin
    PAIRED A/B that controls for terrain variance.
  * N runs, drop warmup (JIT + disk warm), then report MEDIAN + p95 — never a mean-of-one.
  * Two axes: (a) THROUGHPUT = ms/chunk + wall from /worldgen/genprofile ; (b) TICK-HEALTH = mean tick
    ms / TPS sampled from /metrics by a background thread WHILE the gen sweep runs (under load).
  * A/B by flag: capture each flag state to its own frozen artifact, then `compare` reports the delta
    WITH the spread — a delta inside the run-to-run noise band is called INCONCLUSIVE, not a win.
  * Machine-idle guard: refuses (unless --force) if `ps --sort=-pcpu` shows a heavy competing process
    or `nvidia-smi` shows GPU contention BEFORE a run (the "machine trop chargée" lesson, baked in).
  * Bit-identity gate: /worldgen/fingerprint hash OFF vs ON — an async rewrite MUST be bit-identical
    (docs/32 §6). A hash mismatch is a hard FAIL (semantics changed), independent of any speedup.

Existing endpoints cover all three axes, so NO new measurement endpoint was needed:
  /worldgen/genprofile -> per-chunk ms distribution (avg/p95/min/max) + wall elapsedMs + alreadyLoaded
  /metrics             -> meanTickMs, tps, heap, and (added for this harness) chunkPipeline flag state
  /worldgen/fingerprint-> deterministic FNV-1a block-state hash for the serial==parallel gate

Single-instance rule: this script NEVER boots/kills the game. Boot ONE matoulib-RPC instance
(-Dmatoulib.rpc=true) with the pipeline flag set the way you are capturing, get in-world, THEN run.

Usage:
  # 1. Boot instance with the pipeline OFF, in-world, then capture the frozen baseline:
  python3 chunkgen_bench.py capture --flag off --label C0-OFF
  # 2. Reboot with -Dmatoulib.chunk.pipeline=true, in-world, capture the candidate:
  python3 chunkgen_bench.py capture --flag on  --label C0-ON
  # 3. A/B the two frozen artifacts (delta WITH spread; inconclusive-if-within-noise):
  python3 chunkgen_bench.py compare C0-OFF C0-ON
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import packenv as E

DEFAULT_RPC = "http://127.0.0.1:25580"

# ── RPC ──────────────────────────────────────────────────────────────────────────────────────────


def rpc(rpc_base, path, params=None, timeout=650):
    """GET an RPC endpoint. queryParams(ex) reads the URL query for every verb, so GET works for the
    'POST' endpoints too (same as census.py / perf_bench.py). Long timeout: genprofile is synchronous."""
    url = rpc_base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wait_in_world(rpc_base, deadline_s=20):
    end = time.time() + deadline_s
    last = None
    while time.time() < end:
        try:
            p = rpc(rpc_base, "/ping", timeout=3)
            if p.get("inWorld"):
                return True
            last = p
        except Exception as e:
            last = e
        time.sleep(2)
    print(f"  RPC not in-world at {rpc_base} (last: {last})", file=sys.stderr)
    return False


# ── stats ────────────────────────────────────────────────────────────────────────────────────────


def _sorted(xs):
    return sorted(float(x) for x in xs)


def median(xs):
    s = _sorted(xs)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def percentile(xs, q):
    """Nearest-rank p<q>, matching WorldgenGenProfile's ceil(q*n)-1 convention for cross-consistency."""
    s = _sorted(xs)
    if not s:
        return None
    import math
    i = max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))
    return s[i]


def spread(xs):
    """Run-to-run noise proxy = p95 - median. A delta smaller than this is NOT a win."""
    m, p = median(xs), percentile(xs, 0.95)
    return None if (m is None or p is None) else (p - m)


# ── machine-idle guard (the "machine trop chargée fausse tout" lesson) ─────────────────────────────


def _mc_pids():
    try:
        out = subprocess.run(["pgrep", "-f", "java"], capture_output=True, text=True)
        return {int(p) for p in out.stdout.split() if p.isdigit()}
    except Exception:
        return set()


def cpu_guard(threshold_pct):
    """Heaviest non-self, non-MC process. The MC JVM is our TARGET (expected busy) so it is excluded;
    what invalidates a bench is a COMPETING hog. Returns (ok, worst_dict)."""
    mine = {os.getpid(), os.getppid()}
    mc = _mc_pids()
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,pcpu,comm", "--sort=-pcpu", "--no-headers"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return True, {"note": f"ps unavailable ({e})"}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, pcpu = int(parts[0]), float(parts[1])
        except ValueError:
            continue
        if pid in mine or pid in mc:
            continue
        worst = {"pid": pid, "pcpu": pcpu, "comm": parts[2]}
        return pcpu < threshold_pct, worst
    return True, {"note": "no competing process"}


def gpu_guard(threshold_pct):
    """nvidia-smi GPU utilization. Absent GPU / non-NVIDIA -> n/a (not a failure)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return True, {"note": "nvidia-smi n/a"}
    if out.returncode != 0:
        return True, {"note": "nvidia-smi n/a"}
    utils = []
    for tok in out.stdout.split():
        try:
            utils.append(float(tok))
        except ValueError:
            pass
    if not utils:
        return True, {"note": "no gpu reading"}
    mx = max(utils)
    return mx < threshold_pct, {"gpu_util_pct": mx}


def idle_guard(cpu_thr, gpu_thr, force):
    cpu_ok, cpu = cpu_guard(cpu_thr)
    gpu_ok, gpu = gpu_guard(gpu_thr)
    print(f"  idle-guard  cpu: {cpu}  (thr {cpu_thr}%)")
    print(f"  idle-guard  gpu: {gpu}  (thr {gpu_thr}%)")
    ok = cpu_ok and gpu_ok
    if not ok and not force:
        print("\n  REFUSING: machine is not idle (a loaded machine falsifies the bench — the exact\n"
              "  lesson this harness exists to prevent). Close the competing load, or pass --force to\n"
              "  proceed anyway (the override is recorded in the artifact).", file=sys.stderr)
    return ok, {"cpu": cpu, "gpu": gpu, "cpu_ok": cpu_ok, "gpu_ok": gpu_ok, "overridden": bool(force and not ok)}


# ── tick-health sampler (runs WHILE the throughput sweep drives gen) ────────────────────────────────


class TickSampler(threading.Thread):
    """Polls /metrics on a background thread so meanTickMs/tps are sampled UNDER the gen load driven by
    the main thread. /metrics shares the server main-thread executor with genprofile, so a sample lands
    between gen runs and reflects the just-elapsed heavy ticks (tickTimeArray = rolling ~100-tick mean)."""

    def __init__(self, rpc_base, interval=0.5):
        super().__init__(daemon=True)
        self.rpc_base = rpc_base
        self.interval = interval
        self._stop = threading.Event()
        self.samples = []  # list of {meanTickMs, tps}

    def run(self):
        while not self._stop.is_set():
            try:
                m = rpc(self.rpc_base, "/metrics", timeout=30)
                if m.get("ok"):
                    self.samples.append({
                        "meanTickMs": float(m.get("meanTickMs", -1)),
                        "tps": float(m.get("tps", -1)),
                    })
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


# ── capture ────────────────────────────────────────────────────────────────────────────────────────


def origins(anchor_x, anchor_z, stride, runs):
    """Deterministic virgin regions: (anchor + k*stride) along a diagonal ray. Reproducible across boots
    (fixed world seed) and replayed identically OFF/ON for a per-origin paired A/B. stride >> region size
    so runs never overlap (repeating the SAME origin would hit the disk cache -> ~0 ms, a false speedup)."""
    return [(anchor_x + k * stride, anchor_z + k * stride) for k in range(runs)]


def capture(args):
    rpc_base = args.rpc
    if not wait_in_world(rpc_base, args.boot_wait):
        sys.exit("not in-world — boot the matoulib-RPC instance first (devtools skill), then retry.")

    # 1. idle guard
    ok, guard = idle_guard(args.cpu_threshold, args.gpu_threshold, args.force)
    if not ok and not args.force:
        sys.exit(2)

    # 2. flag-label integrity: the booted flag MUST match what we claim to capture
    metrics0 = rpc(rpc_base, "/metrics", timeout=30)
    booted = metrics0.get("chunkPipeline")
    declared = args.flag == "on"
    if booted is None:
        print("  WARN: /metrics has no chunkPipeline field (matoulib not rebuilt with the flag readout);"
              " labelling from --flag only, cannot verify.")
    elif booted != declared and not args.no_flag_check:
        sys.exit(f"FLAG MISMATCH: booted chunkPipeline={booted} but --flag {args.flag} (declared={declared}). "
                 f"You are about to mislabel a capture. Reboot with the right flag, or pass --no-flag-check.")
    else:
        print(f"  flag verified: booted chunkPipeline={booted} == declared {args.flag}")

    org = origins(args.anchor_x, args.anchor_z, args.stride, args.runs)
    print(f"\n  throughput sweep: {args.runs} runs (drop {args.warmup} warmup), r={args.r}, dim={args.dim}")
    print(f"  origins: {org[0]} .. {org[-1]}  stride={args.stride} blocks")

    # 3. throughput axis + concurrent tick-health sampling
    sampler = TickSampler(rpc_base, args.tick_interval)
    sampler.start()
    per_run = []
    for k, (x, z) in enumerate(org):
        r = rpc(rpc_base, "/worldgen/genprofile",
                {"x": x, "z": z, "r": args.r, "dim": args.dim})
        if not r.get("ok"):
            sampler.stop()
            sys.exit(f"genprofile failed at run {k} ({x},{z}): {r}")
        chunks = int(r["chunks"])
        already = int(r.get("alreadyLoaded", 0))
        ratio = already / chunks if chunks else 0.0
        row = {
            "run": k, "x": x, "z": z, "warmup": k < args.warmup,
            "chunks": chunks, "alreadyLoaded": already, "alreadyRatio": round(ratio, 3),
            "avgMsPerChunk": float(r["avgMs"]), "p95MsPerChunk": float(r["p95Ms"]),
            "minMs": float(r["minMs"]), "maxMs": float(r["maxMs"]),
            "totalMs": float(r["totalMs"]), "wallMs": float(r["elapsedMs"]),
        }
        per_run.append(row)
        tag = "  (warmup, dropped)" if row["warmup"] else ""
        contam = "  ** CONTAMINATED (region pre-generated: bump --anchor or fresh world) **" if ratio > 0.5 else ""
        print(f"    run {k}: {chunks} chunks  avg {row['avgMsPerChunk']:.3f} ms/chunk  "
              f"p95 {row['p95MsPerChunk']:.3f}  wall {row['wallMs']:.0f} ms  "
              f"resident {already}/{chunks}{tag}{contam}")
    sampler.stop()
    sampler.join(timeout=5)

    measured = [r for r in per_run if not r["warmup"]]
    if not measured:
        sys.exit("no measured runs after dropping warmup — increase --runs.")
    contaminated = [r for r in measured if r["alreadyRatio"] > 0.5]

    avg_series = [r["avgMsPerChunk"] for r in measured]
    wall_series = [r["wallMs"] for r in measured]
    tick_series = [s["meanTickMs"] for s in sampler.samples if s["meanTickMs"] >= 0]
    tps_series = [s["tps"] for s in sampler.samples if s["tps"] >= 0]

    # 4. bit-identity fingerprint (the serial==parallel gate)
    fp = rpc(rpc_base, "/worldgen/fingerprint",
             {"x": args.fp_x, "z": args.fp_z, "r": args.fp_r, "dim": args.dim})

    throughput = {
        "median_ms_per_chunk": median(avg_series),
        "p95_ms_per_chunk": percentile(avg_series, 0.95),
        "spread_ms_per_chunk": spread(avg_series),
        "median_wall_ms": median(wall_series),
        "p95_wall_ms": percentile(wall_series, 0.95),
        "per_run": per_run,
    }
    tick_health = {
        "n_samples": len(tick_series),
        "median_mean_tick_ms": median(tick_series),
        "p95_mean_tick_ms": percentile(tick_series, 0.95),
        "min_tps": (min(tps_series) if tps_series else None),
        "median_tps": median(tps_series),
    }
    artifact = {
        "schema": "chunkgen-bench/1",
        "label": args.label,
        "flag": args.flag,
        "chunkPipeline_booted": booted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().nodename,
        "rpc": rpc_base,
        "seed_note": args.seed_note,
        "dim": args.dim,
        "workload": {
            "anchor": [args.anchor_x, args.anchor_z], "stride": args.stride,
            "r": args.r, "runs": args.runs, "warmup": args.warmup,
        },
        "idle_guard": guard,
        "throughput": throughput,
        "tick_health": tick_health,
        "fingerprint": {
            "x": args.fp_x, "z": args.fp_z, "r": args.fp_r,
            "hash": fp.get("hash"), "chunksHashed": fp.get("chunksHashed"),
            "unpopulated": fp.get("unpopulated"),
        },
        "contaminated_runs": [r["run"] for r in contaminated],
    }

    os.makedirs(E.BENCH_CAPTURES, exist_ok=True)
    stem = os.path.join(E.BENCH_CAPTURES, args.label)
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)
        fh.write("\n")
    _write_markdown(stem + ".md", artifact)

    print(f"\n  === {args.label} ({args.flag}) ===")
    print(f"  throughput : median {throughput['median_ms_per_chunk']:.3f} ms/chunk  "
          f"(p95 {throughput['p95_ms_per_chunk']:.3f}, spread ±{throughput['spread_ms_per_chunk']:.3f})")
    print(f"  wall       : median {throughput['median_wall_ms']:.0f} ms/region")
    print(f"  tick-health: median {_fmt(tick_health['median_mean_tick_ms'])} ms/tick  "
          f"min TPS {_fmt(tick_health['min_tps'])}  ({tick_health['n_samples']} samples under load)")
    print(f"  fingerprint: {artifact['fingerprint']['hash']}  "
          f"({artifact['fingerprint']['chunksHashed']} chunks)")
    if contaminated:
        print(f"  WARNING: {len(contaminated)} contaminated run(s) {artifact['contaminated_runs']} "
              f"(region was already generated) — bump --anchor or use a fresh world for a clean capture.")
    print(f"  written: {stem}.json  +  {stem}.md")


def _fmt(v):
    return "n/a" if v is None else f"{v:.2f}"


def _write_markdown(path, a):
    t, th = a["throughput"], a["tick_health"]
    fp = a["fingerprint"]
    w = a["workload"]
    lines = [
        f"# chunk-gen baseline — {a['label']} (flag {a['flag']})",
        "",
        f"- captured: {a['timestamp']}  host: {a['host']}",
        f"- booted chunkPipeline: `{a['chunkPipeline_booted']}`   seed: {a['seed_note']}   dim: {a['dim']}",
        f"- workload: anchor {w['anchor']} stride {w['stride']} r{w['r']} — {w['runs']} runs, drop {w['warmup']} warmup",
        f"- idle-guard: cpu `{a['idle_guard']['cpu']}` gpu `{a['idle_guard']['gpu']}`"
        + ("  **(OVERRIDDEN via --force)**" if a["idle_guard"].get("overridden") else ""),
        "",
        "## Throughput",
        "",
        f"- **median {t['median_ms_per_chunk']:.3f} ms/chunk** (p95 {t['p95_ms_per_chunk']:.3f}, "
        f"run-to-run spread ±{t['spread_ms_per_chunk']:.3f})",
        f"- wall: median {t['median_wall_ms']:.0f} ms/region",
        "",
        "| run | x,z | avg ms/chunk | p95 | wall ms | resident | warmup |",
        "|----:|-----|-------------:|----:|--------:|---------:|:------:|",
    ]
    for r in t["per_run"]:
        lines.append(f"| {r['run']} | {r['x']},{r['z']} | {r['avgMsPerChunk']:.3f} | "
                     f"{r['p95MsPerChunk']:.3f} | {r['wallMs']:.0f} | "
                     f"{r['alreadyLoaded']}/{r['chunks']} | {'yes' if r['warmup'] else ''} |")
    lines += [
        "",
        "## Tick-health (sampled under gen load)",
        "",
        f"- median {_fmt(th['median_mean_tick_ms'])} ms/tick (p95 {_fmt(th['p95_mean_tick_ms'])}), "
        f"min TPS {_fmt(th['min_tps'])}, median TPS {_fmt(th['median_tps'])} — {th['n_samples']} samples",
        "",
        "## Fingerprint (serial==parallel gate)",
        "",
        f"- `{fp['hash']}`  ({fp['chunksHashed']} chunks, {fp['unpopulated']} unpopulated) "
        f"@ ({fp['x']},{fp['z']}) r{fp['r']}",
        "",
        "> Frozen reference. Later async-chunk phases A/B against this via `chunkgen_bench.py compare`.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ── compare ──────────────────────────────────────────────────────────────────────────────────────


def _load(label):
    p = label if os.path.isfile(label) else os.path.join(E.BENCH_CAPTURES, label + ".json")
    if not os.path.isfile(p):
        sys.exit(f"artifact not found: {p}")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def compare(args):
    base = _load(args.baseline)   # OFF (reference)
    cand = _load(args.candidate)  # ON
    bt, ct = base["throughput"], cand["throughput"]

    b_mpc, c_mpc = bt["median_ms_per_chunk"], ct["median_ms_per_chunk"]
    # noise band = combined run-to-run spread of the two captures. A delta inside it is NOT a signal.
    band = ((bt.get("spread_ms_per_chunk") or 0.0) + (ct.get("spread_ms_per_chunk") or 0.0)) / 2.0
    delta = c_mpc - b_mpc
    pct = (100.0 * delta / b_mpc) if b_mpc else 0.0
    conclusive = abs(delta) > band

    print("=" * 74)
    print(f"  chunk-gen A/B   baseline {base['label']} (flag {base['flag']})  vs  "
          f"candidate {cand['label']} (flag {cand['flag']})")
    print("=" * 74)
    print(f"\n  THROUGHPUT (ms/chunk, lower=better)")
    print(f"    baseline : {b_mpc:.3f}   (p95 {bt['p95_ms_per_chunk']:.3f}, spread ±{bt['spread_ms_per_chunk']:.3f})")
    print(f"    candidate: {c_mpc:.3f}   (p95 {ct['p95_ms_per_chunk']:.3f}, spread ±{ct['spread_ms_per_chunk']:.3f})")
    print(f"    delta    : {delta:+.3f} ms/chunk ({pct:+.1f}%)   noise band ±{band:.3f}")
    if not conclusive:
        print(f"    -> INCONCLUSIVE: |delta| {abs(delta):.3f} <= noise band {band:.3f}. NOT a win/regression;"
              " re-run with more --runs on a quieter machine to shrink the band.")
    elif delta < 0:
        print(f"    -> SPEEDUP: candidate is {-pct:.1f}% faster, outside the noise band.")
    else:
        print(f"    -> REGRESSION: candidate is {pct:.1f}% slower, outside the noise band.")

    bw, cw = bt["median_wall_ms"], ct["median_wall_ms"]
    print(f"\n  WALL (ms/region): baseline {bw:.0f}  candidate {cw:.0f}  "
          f"delta {cw - bw:+.0f} ({(100.0 * (cw - bw) / bw if bw else 0):+.1f}%)")

    bth, cth = base["tick_health"], cand["tick_health"]
    print(f"\n  TICK-HEALTH (ms/tick under load, lower=better):")
    print(f"    baseline {_fmt(bth['median_mean_tick_ms'])}  candidate {_fmt(cth['median_mean_tick_ms'])}  "
          f"| min TPS {_fmt(bth['min_tps'])} -> {_fmt(cth['min_tps'])}")

    bh, ch = base["fingerprint"]["hash"], cand["fingerprint"]["hash"]
    print(f"\n  FINGERPRINT (serial==parallel bit-identity gate):")
    print(f"    baseline  {bh}")
    print(f"    candidate {ch}")
    identical = bh is not None and bh == ch
    print(f"    -> {'PASS: bit-identical (worldgen semantics unchanged).' if identical else 'FAIL: HASH MISMATCH — the async path changed generated blocks. This is a HARD gate; fix before trusting any speedup.'}")

    # contamination sanity
    for a in (base, cand):
        if a.get("contaminated_runs"):
            print(f"\n  WARNING: {a['label']} had contaminated runs {a['contaminated_runs']} "
                  f"(pre-generated regions) — its numbers are suspect.")

    print()
    if not identical:
        sys.exit(3)  # gate failure is a non-zero exit for CI/preflight use


# ── cli ──────────────────────────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="run the benchmark, write a frozen baseline artifact")
    cap.add_argument("--flag", choices=("off", "on"), required=True,
                     help="which pipeline flag state is BOOTED (must match /metrics chunkPipeline)")
    cap.add_argument("--label", required=True, help="artifact name (e.g. C0-OFF)")
    cap.add_argument("--rpc", default=DEFAULT_RPC)
    cap.add_argument("--dim", type=int, default=0)
    cap.add_argument("--r", type=int, default=8, help="region radius in chunks (docs/32 gate uses r8)")
    cap.add_argument("--runs", type=int, default=6, help="total runs (>= warmup+1)")
    cap.add_argument("--warmup", type=int, default=1, help="leading runs dropped (JIT/disk warm)")
    cap.add_argument("--anchor-x", type=int, default=100000, dest="anchor_x",
                     help="first-region origin X in blocks (far from spawn = virgin terrain)")
    cap.add_argument("--anchor-z", type=int, default=100000, dest="anchor_z")
    cap.add_argument("--stride", type=int, default=2048,
                     help="blocks between successive origins (>> region so runs never overlap)")
    cap.add_argument("--fp-x", type=int, default=100000, dest="fp_x", help="fingerprint origin X (fixed)")
    cap.add_argument("--fp-z", type=int, default=100000, dest="fp_z")
    cap.add_argument("--fp-r", type=int, default=4, dest="fp_r")
    cap.add_argument("--tick-interval", type=float, default=0.5, dest="tick_interval",
                     help="seconds between /metrics tick-health samples")
    cap.add_argument("--seed-note", default="world-fixed", dest="seed_note",
                     help="free-text note recording WHICH fixed-seed world was used")
    cap.add_argument("--cpu-threshold", type=float, default=30.0, dest="cpu_threshold",
                     help="refuse if a competing (non-MC) process exceeds this %%CPU")
    cap.add_argument("--gpu-threshold", type=float, default=40.0, dest="gpu_threshold")
    cap.add_argument("--boot-wait", type=int, default=20, dest="boot_wait",
                     help="seconds to wait for in-world before giving up")
    cap.add_argument("--force", action="store_true", help="proceed despite a failed idle guard (recorded)")
    cap.add_argument("--no-flag-check", action="store_true", dest="no_flag_check",
                     help="skip the booted-flag vs --flag integrity check")
    cap.set_defaults(func=capture)

    cmp = sub.add_parser("compare", help="A/B two artifacts: delta WITH spread, inconclusive-if-in-noise")
    cmp.add_argument("baseline", help="reference artifact label or path (usually the OFF capture)")
    cmp.add_argument("candidate", help="candidate artifact label or path (the ON capture)")
    cmp.set_defaults(func=compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
