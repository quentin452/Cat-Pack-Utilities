#!/usr/bin/env python3
"""bundle_regression_gate.py — a pack mod must NEVER ship an OLDER version than a previously
published cut. Catches a silent bundle REGRESSION before it ships.

Why (2026-07-09): a reverse-sync (`a2ee2f28 config(all)`) clobbered the mod-director bundles and
regressed several fileIDs/versions (ImmersiveEngineering fork4 -> fork3, OaT V1.17.4 -> V1.17.1,
BQ/LootBeams…) — the exact "reverse-sync must EXCLUDE mod-director" trap, struck again. It only
surfaced because pack_sync --audit happened to run this session; without that, V1.1.11 could have
shipped players an OLDER ImmersiveEngineering than V1.1.8 already had. No gate guarded against a
bundle going BACKWARD. This does.

How: parse the mod-director bundles (curse/url/modrinth) + the CF client manifest at every
`release: cut` commit AND at HEAD (reusing changelog_from_bundles' stable keys: cf:<addonId>,
gh:<owner/repo>, mr:<projectId>, manifest:<projectID>). For each mod key present at HEAD, compare
HEAD's delivered version to the MAX version it ever had across published cuts. HEAD < that max =
a regression (a downgrade vs something already shipped). Versions compare by a natural key so
fileId ints, `0.7.11-fork3` vs `-fork4`, `1.0.3` vs `1.0.4`, and `…2309` vs `…2310` all order right.

A version CHANGE that only goes forward (or a genuinely new mod / an intentional removal) is fine —
only a BACKWARD move flags. Exit non-zero on any regression (wired as release_pack GATE 10).

Usage:
  python3 bundle_regression_gate.py            # scan all cuts vs HEAD
  python3 bundle_regression_gate.py --ref X    # test an arbitrary ref instead of HEAD
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import changelog_from_bundles as C  # noqa: E402 — reuse parse/parse_manifest/git_show/BUNDLES

CUT_GREP = r"release: cut"   # the commit-subject marker of a published pack version


def _vkey(v):
    """Natural version-sort key: split into numeric/alpha runs so 8390297<8397887, fork3<fork4,
    1.0.3<1.0.4, 2309<2310 all order correctly. Numeric tokens (0,int) sort before alpha (1,str)."""
    toks = re.findall(r"\d+|[A-Za-z]+", str(v))
    return [(0, int(t)) if t.isdigit() else (1, t.lower()) for t in toks]


def _git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def cut_commits(pack):
    """(hash, subject) of every published-cut commit, OLDEST first."""
    out = _git(pack, "log", "--grep", CUT_GREP, "--format=%H\t%s")
    rows = [ln.split("\t", 1) for ln in out.splitlines() if "\t" in ln]
    return list(reversed(rows))  # git log is newest-first; want chronological


def parse_all(pack, ref):
    """{key: (version, label)} across every bundle + the client manifest at one ref."""
    merged = {}
    for path in C.BUNDLES + [C.CLIENT_MANIFEST]:
        pfn = C.parse_manifest if path == C.CLIENT_MANIFEST else C.parse
        for k, v in pfn(C.git_show(pack, ref, path)).items():
            merged[k] = (v.get("version"), v.get("label"))
    return merged


def pinned_exempt_keys():
    """Keys of mods DELIBERATELY held at a fixed (possibly older) file — a manifest pack block with
    `_pin` or a top-level `no_release`. Their "downgrade" is intentional policy, not a regression
    (FileDirector: fork8/9 REJECTED by CF -> pinned to the grandfathered Approved fork6 = a lower
    fileId than a rejected newer one). Exempt cf:<projectId> + manifest:<projectId> for each."""
    import json
    exempt = set()
    try:
        mods = json.load(open(C.E.RELEASE_MANIFEST))["mods"]
    except Exception:
        return exempt
    for m in mods:
        pk = m.get("pack")
        pinned = m.get("no_release") or (isinstance(pk, dict) and "_pin" in pk)
        if not pinned:
            continue
        pid = (m.get("curseforge") or {}).get("project_id")
        if pid is not None:
            exempt.add(f"cf:{pid}")
            exempt.add(f"manifest:{pid}")
    return exempt


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="HEAD", help="ref to check (default HEAD)")
    ap.add_argument("--pack", default=C.REPO_DEFAULT, help="pack repo path")
    args = ap.parse_args()
    pack = os.path.expanduser(args.pack)

    cuts = cut_commits(pack)
    if not cuts:
        print("no 'release: cut' commits found — nothing to compare against.")
        return
    head = parse_all(pack, args.ref)
    # per key: the max (version, from-which-cut) ever shipped in a published cut
    best = {}   # key -> (vkey, version, cut_subject)
    for h, subj in cuts:
        for k, (ver, _label) in parse_all(pack, h).items():
            if ver is None:
                continue
            vk = _vkey(ver)
            if k not in best or vk > best[k][0]:
                best[k] = (vk, ver, subj.strip())

    exempt = pinned_exempt_keys()
    print(f"=== bundle regression gate — {args.ref} vs {len(cuts)} published cut(s)"
          + (f" ({len(exempt)//2} pinned mod(s) exempt)" if exempt else ""))
    regressions = []
    for k, (ver, label) in sorted(head.items()):
        if ver is None or k not in best or k in exempt:
            continue
        prior_vk, prior_ver, prior_cut = best[k]
        if _vkey(ver) < prior_vk:
            regressions.append((k, label, ver, prior_ver, prior_cut))

    if not regressions:
        print(f"  ✓ no regression — every mod at {args.ref} is >= the newest version ever published.")
        return
    print(f"\n⛔ {len(regressions)} bundle REGRESSION(S) — a mod would ship OLDER than a published cut:")
    for k, label, ver, prior_ver, prior_cut in regressions:
        print(f"  ✗ {k}  {label}")
        print(f"      {args.ref} delivers {ver!r} but {prior_ver!r} was already shipped in "
              f"\"{prior_cut[:60]}\"")
    print("\nReconcile the mod-director bundle (a reverse-sync/edit likely clobbered it) — e.g. "
          "pack_sync.py --apply, or restore the fileID/URL — then re-run.")
    sys.exit(2)


if __name__ == "__main__":
    main()
