#!/usr/bin/env python3
"""
housekeep.py — keep the Mod-Sandbox memory files from bloating.

Two chores, DRY-RUN by default (prints what it would do; pass --apply to write):

  --archive-bugs      Move resolved bug paragraphs out of memory/BUGS.md into
                      memory/BUGS-archive.md. A bug is resolved when its "## BUG-NNN — ...
                      (STATUS...)" header carries a done keyword (CORRIGÉ / RÉSOLU / VALIDÉ /
                      ABANDONNÉ / FAUSSE ALERTE) and NOT "en attente" (so half-closed bugs stay).
                      git history keeps the full record either way.

  --clear-changelog SUBSTR   After a section of memory/changelog-pending.md has been uploaded,
                      reset it to a stub: keep its "## ..." header, replace the body with a
                      placeholder so the rolling file starts clean for the next cycle.

Convention lives in the files themselves (BUGS.md header lists the statuses).
"""

import argparse
import os
import re
import sys

HUB = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory")
BUGS = os.path.join(HUB, "BUGS.md")
BUGS_ARCHIVE = os.path.join(HUB, "BUGS-archive.md")
CHANGELOG = os.path.join(HUB, "changelog-pending.md")

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


def clear_changelog(substr, apply):
    text = open(CHANGELOG, encoding="utf-8").read()
    preamble, blocks = split_blocks(text, "## ")
    hit = [i for i, (h, _) in enumerate(blocks) if substr.lower() in h.lower()]
    if not hit:
        sys.exit(f"no changelog section header contains {substr!r}")
    for i in hit:
        h = blocks[i][0]
        print(f"[housekeep] reset section: {h}")
        if apply:
            blocks[i] = (h, f"{h}\n\n(rien en attente)\n\n")
    if apply:
        open(CHANGELOG, "w", encoding="utf-8").write(preamble + "".join(t for _, t in blocks))
        print("[housekeep] changelog section(s) reset to stub.")
    else:
        print("[housekeep] dry-run — pass --apply to reset.")


def main():
    ap = argparse.ArgumentParser(description="Prune the Mod-Sandbox memory files.")
    ap.add_argument("--archive-bugs", action="store_true")
    ap.add_argument("--clear-changelog", metavar="SUBSTR",
                    help="reset the changelog-pending section whose header contains SUBSTR")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()
    if not (args.archive_bugs or args.clear_changelog):
        ap.error("give --archive-bugs and/or --clear-changelog SUBSTR")
    if args.archive_bugs:
        archive_bugs(args.apply)
    if args.clear_changelog:
        clear_changelog(args.clear_changelog, args.apply)


if __name__ == "__main__":
    main()
