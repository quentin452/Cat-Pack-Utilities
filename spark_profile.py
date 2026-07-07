#!/usr/bin/env python3
"""Drive the in-game spark profiler over the matoulib RPC and collect the .sparkprofile locally.

The pack ships spark (fork quentin452/spark-legacy, 1.10.20-forge1710) on both sides, and the
matoulib devtools RPC exposes /cmd — so a profile run is just three commands. This driver wraps
them into one CLI call and always uses `--save-to-file`: the raw .sparkprofile lands on disk,
NOTHING is uploaded to spark.lucko.me (uploading publishes the profile publicly; if you want the
web viewer, upload the file yourself, deliberately).

Flow:
  1. /cmd "spark profiler cancel"           (drop any stale/background sampler, best-effort)
  2. /cmd "spark profiler start [args]"
  3. sleep <duration>  (capture /cmd "spark tps" just before stopping, for context)
  4. /cmd "spark profiler stop --save-to-file"
  5. poll <instance>/config/spark/ (and fallbacks) for the new profile-*.sparkprofile
  6. copy it into --out and print the path

Usage:
  python3 spark_profile.py --duration 60
  python3 spark_profile.py --duration 30 --start-args "--thread *"   # all threads
  python3 spark_profile.py --rpc http://127.0.0.1:25580 --out ./captures

Requires the game to be up with -Dmatoulib.rpc=true (client TEST instance). The .sparkprofile is
protobuf — view it at https://spark.lucko.me (drag & drop the file: local parsing, no upload) or
keep it as a machine artifact next to asprof .folded captures.
"""

import argparse
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_INSTANCE = Path.home() / "Documents/curseforge/minecraft/Instances/Biggess Pack Cat Edition V1 TEST"


def rpc_cmd(base: str, command: str, timeout: int = 30) -> str:
    url = f"{base}/cmd?q={urllib.parse.quote(command)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def newest_profile(instance: Path) -> "tuple[Path, float] | None":
    candidates = []
    for pattern in ("config/spark/*.sparkprofile", "spark/*.sparkprofile", "plugins/spark/*.sparkprofile"):
        candidates.extend(instance.glob(pattern))
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest, newest.stat().st_mtime


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the in-game spark profiler and save the .sparkprofile locally.")
    ap.add_argument("--rpc", default="http://127.0.0.1:25580", help="matoulib RPC base URL")
    ap.add_argument("--duration", type=int, default=30, help="profiling window in seconds (default 30)")
    ap.add_argument("--start-args", default="", help='extra args for "spark profiler start", e.g. "--thread *"')
    ap.add_argument("--instance", default=str(DEFAULT_INSTANCE), help="game instance dir (where spark saves)")
    ap.add_argument("--out", default=".", help="directory to copy the .sparkprofile into")
    ap.add_argument("--file-wait", type=int, default=90, help="max seconds to wait for the saved file")
    args = ap.parse_args()

    instance = Path(args.instance).expanduser()
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        rpc_cmd(args.rpc, "spark profiler cancel")
    except Exception:
        pass  # no active sampler / endpoint quirk — start will tell us

    before = newest_profile(instance)
    before_mtime = before[1] if before else 0.0

    start_cmd = "spark profiler start"
    if args.start_args.strip():
        start_cmd += " " + args.start_args.strip()
    print(f"[spark] {start_cmd}")
    print(rpc_cmd(args.rpc, start_cmd)[:400])

    print(f"[spark] sampling {args.duration}s ...")
    time.sleep(args.duration)

    try:
        print("[spark] tps snapshot:", rpc_cmd(args.rpc, "spark tps")[:400])
    except Exception as e:  # context only, never fatal
        print(f"[spark] tps snapshot failed: {e}")

    print("[spark] stopping (save-to-file, no upload)")
    print(rpc_cmd(args.rpc, "spark profiler stop --save-to-file", timeout=120)[:400])

    deadline = time.time() + args.file_wait
    while time.time() < deadline:
        found = newest_profile(instance)
        if found and found[1] > before_mtime:
            dest = out_dir / found[0].name
            shutil.copy2(found[0], dest)
            print(f"[spark] profile saved: {dest}")
            print("[spark] view: drag & drop the file on https://spark.lucko.me (local parse, no upload)")
            return 0
        time.sleep(2)

    print("[spark] FAILED: no new .sparkprofile appeared (check the game log / spark output)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
