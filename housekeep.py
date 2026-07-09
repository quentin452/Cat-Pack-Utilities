#!/usr/bin/env python3
"""
housekeep.py — keep the Mod-Sandbox memory files from bloating.

One chore, DRY-RUN by default (prints what it would do; pass --apply to write):

  --archive-bugs      Move resolved bug paragraphs out of memory/BUGS.md into
                      memory/BUGS-archive.md. A bug is resolved when its "## BUG-NNN — ...
                      (STATUS...)" header carries a done keyword (CORRIGÉ / RÉSOLU / VALIDÉ /
                      ABANDONNÉ / FAUSSE ALERTE) and NOT "en attente" (so half-closed bugs stay).
                      git history keeps the full record either way.

(--clear-changelog was removed 2026-07-07 along with memory/changelog-pending.md: changelogs
are now derived — mods from git commits (changelog_from_git.py), pack from bundle+config diffs
(changelog_from_bundles.py → changelog_publish.py --from-derive). Nothing to reset.)

Convention lives in the files themselves (BUGS.md header lists the statuses).
"""

import argparse
import os
import re

import packenv as E

HUB = os.path.join(E.HUB, "memory")
BUGS = os.path.join(HUB, "BUGS.md")
BUGS_ARCHIVE = os.path.join(HUB, "BUGS-archive.md")

DONE = re.compile(r"CORRIG[ÉE]|R[ÉE]SOLU|VALID[ÉE]|ABANDONN[ÉE]|FAUSSE ALERTE", re.IGNORECASE)
PENDING = re.compile(r"en attente|pending", re.IGNORECASE)


def split_blocks(text, marker):
    """(preamble, [(header_line, block_text), ...]) split on lines starting with `marker`."""
    lines = text.splitlines(keepends=True)
    idxs = [i for i, l in enumerate(lines) if l.startswith(marker)]
    if not idxs:
        return text, []
    preamble = "".join(lines[: idxs[0]])
    blocks = []
    for a, b in zip(idxs, idxs[1:] + [len(lines)]):
        blocks.append((lines[a].rstrip("\n"), "".join(lines[a:b])))
    return preamble, blocks


def archive_bugs(apply):
    text = open(BUGS, encoding="utf-8").read()
    preamble, blocks = split_blocks(text, "## BUG-")
    resolved = [(h, t) for h, t in blocks if DONE.search(h) and not PENDING.search(h)]
    active = [(h, t) for h, t in blocks if not (DONE.search(h) and not PENDING.search(h))]

    print(f"[housekeep] {len(blocks)} bugs — {len(resolved)} resolved (archive), {len(active)} kept")
    for h, _ in resolved:
        print(f"  archive: {h}")
    if not resolved:
        return
    if not apply:
        print("[housekeep] dry-run — pass --apply to move them.")
        return

    header = "# BUGS archive — bugs résolus/abandonnés (sortis de BUGS.md par housekeep.py)\n\n"
    prev = open(BUGS_ARCHIVE, encoding="utf-8").read() if os.path.isfile(BUGS_ARCHIVE) else header
    open(BUGS_ARCHIVE, "w", encoding="utf-8").write(prev + "".join(t for _, t in resolved))
    open(BUGS, "w", encoding="utf-8").write(preamble + "".join(t for _, t in active))
    print(f"[housekeep] moved {len(resolved)} bug(s) to {BUGS_ARCHIVE}")


def main():
    ap = argparse.ArgumentParser(description="Prune the Mod-Sandbox memory files.")
    ap.add_argument("--archive-bugs", action="store_true")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()
    if not args.archive_bugs:
        ap.error("give --archive-bugs")
    archive_bugs(args.apply)


if __name__ == "__main__":
    main()
