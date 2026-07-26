#!/usr/bin/env python3
"""Capture a matou-engine T-02 frame baseline, WITH the flag profile that produced it.

matou-engine PRD §5.5: a baseline whose flag profile is not recorded is not comparable to a later run —
the instance carries ~120 flags and the two baselines differ only by which cut flags are on. So this
script never captures numbers alone: it snapshots the properties file, the jar, the commit and the
runtime alongside them, into the hub's versioned bench-captures tree.

Two baselines are expected (PRD §5.5):
  (a) cut flags OFF   -> "what players run today"        — spark's throughput half works here
  (b) hardCut ON      -> "the in-progress engine-exit"   — spark's tick layer is dead here

Usage:
  python3 baseline_capture.py --label a-cutflags-off
  python3 baseline_capture.py --label b-hardcut-on --note "5 min traversal, view distance 12"
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import packenv as E  # noqa: E402

CAPTURE_ROOT = os.path.join(E.BENCH_CAPTURES, "matou-engine-t02")
LOG = os.path.join(E.INSTANCE_MATOU, "logs", "fml-client-latest.log")
PROPS = os.path.join(E.INSTANCE_MATOU_CONFIG, "matoulib.properties")

FRAME_RE = re.compile(r"\[matoulib\.framestats/?\]: (.*)")
PROBE_RE = re.compile(r"\[matoulib\.engine-probe/?\]: (.*)")


def _read_log():
    if not os.path.isfile(LOG):
        sys.exit(f"log not found: {LOG}\n(the client must have run at least once)")
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _runtime_facts(text):
    """Pull the facts that make a number interpretable six months later."""
    facts = {}
    m = re.search(r"Java is .*?version ([0-9._]+)", text)
    if m:
        facts["java"] = m.group(1)
    m = re.search(r"lwjgl-([0-9][^/\s\"]*)\.jar", text)
    if m:
        facts["lwjgl"] = m.group(1)
    for key, pat in (("gl_renderer", r"GL_RENDERER = (.+)"), ("gl_version", r"GL_VERSION  = (.+)")):
        m = re.search(pat, text)
        if m:
            facts[key] = m.group(1).strip()
    return facts


def _git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def _matoulib_repo():
    with open(E.REPOS_JSON, encoding="utf-8") as fh:
        repos = json.load(fh)
    repos = repos if isinstance(repos, list) else repos.get("repos", [])
    for r in repos:
        if r.get("name") == "matoulib-core":
            return os.path.expanduser(r["path"])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="capture name, e.g. a-cutflags-off")
    ap.add_argument("--note", default="", help="what was done during the run (scenario, duration)")
    args = ap.parse_args()

    text = _read_log()
    frames = FRAME_RE.findall(text)
    if not frames:
        sys.exit(
            "no matoulib.framestats lines in the log.\n"
            "Set matoulib.engine.frameStats=true in the instance config, then play a few minutes."
        )

    out = os.path.join(CAPTURE_ROOT, args.label)
    os.makedirs(out, exist_ok=True)

    # The profile IS part of the measurement — copied, not summarised.
    if os.path.isfile(PROPS):
        shutil.copy2(PROPS, os.path.join(out, "matoulib.properties"))

    with open(os.path.join(out, "frame-stats.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(frames) + "\n")

    probe = PROBE_RE.findall(text)
    if probe:
        with open(os.path.join(out, "gl-probe.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(probe) + "\n")

    repo = _matoulib_repo()
    manifest = {
        "label": args.label,
        "note": args.note,
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime": _runtime_facts(text),
        "matoulib_commit": _git(repo, "rev-parse", "--short", "HEAD") if repo else None,
        "matoulib_branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD") if repo else None,
        "matoulib_dirty": bool(_git(repo, "status", "--porcelain")) if repo else None,
        "frame_stat_lines": len(frames),
        "last_frame_stat": frames[-1] if frames else None,
        "profile_flags": {},
    }
    if os.path.isfile(PROPS):
        with open(PROPS, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if re.search(r"cut|own|interest|shadow|hardCut", k, re.I):
                        manifest["profile_flags"][k.strip()] = v.strip()

    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    print(f"captured -> {out}")
    print(f"  frame-stat lines : {len(frames)}")
    print(f"  matoulib         : {manifest['matoulib_branch']} @ {manifest['matoulib_commit']}"
          f"{' (DIRTY)' if manifest['matoulib_dirty'] else ''}")
    print(f"  runtime          : {manifest['runtime']}")
    print(f"  cut flags        : {manifest['profile_flags']}")
    if frames:
        print(f"  last             : {frames[-1]}")


if __name__ == "__main__":
    main()
