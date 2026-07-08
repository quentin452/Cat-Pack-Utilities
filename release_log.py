#!/usr/bin/env python3
"""release_log.py — append-only markdown logger for release runs.

Every release_mods.py / release_pack.py run (VERIFY / DRY-RUN / UPLOAD) leaves ONE traceable
entry in release-log.md (repo root, next to this file), so the local build/test/audit is
recorded and stays separated from the outward upload. A logged verify stage before any upload
is the point: you can see WHAT was checked and WHEN, and prove a release did its homework.

LOGGING MUST NEVER BREAK A RELEASE: every public call is wrapped so a logger failure only prints
a warning and returns — a release is never aborted because we could not write a log line.

API:
    run = open_run(target, version, mode)   # mode in {"VERIFY","DRY-RUN","UPLOAD"}; optional repo=
    run.gate(name, ok, detail="")           # ok True (pass) / False (fail) / None (skipped)
    run.finish(outcome, notes="")           # append ONE markdown entry, then return

Entry format (append, newest at the BOTTOM):
    ## <UTC ISO timestamp> — <target> <version> [<MODE>]
    - HEAD: <git short sha of the target's repo>
    - gates: ✅ <name> (detail) / ❌ <name> (detail) / ⏭️ <name> (skipped)
    - outcome: <outcome>
    - notes: <notes>

stdlib only.
"""

import datetime
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "release-log.md")

_OK = "✅"
_FAIL = "❌"
_SKIP = "⏭️"


def _short_sha(repo):
    """git short sha of repo's HEAD, or '?' — never throws."""
    if not repo:
        return "?"
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "?"
    except Exception:
        return "?"


class Run:
    """One release-log run. Collects gate results, then finish() appends a single entry."""

    def __init__(self, target, version, mode, repo=None):
        self.target = target
        self.version = version
        self.mode = mode
        self.repo = repo
        self.gates = []          # list of (name, ok, detail)
        self._finished = False

    def gate(self, name, ok, detail=""):
        """Record a gate result (True pass / False fail / None skipped). Returns `ok` so callers
        can `if run.gate(...):`. Never throws."""
        try:
            self.gates.append((str(name), ok, str(detail or "")))
        except Exception as e:  # pragma: no cover — logging must never break a release
            print(f"  [release-log] WARN could not record gate {name!r}: {e}")
        return ok

    def _render_gates(self):
        if not self.gates:
            return "(none)"
        parts = []
        for name, ok, detail in self.gates:
            sym = _OK if ok is True else (_FAIL if ok is False else _SKIP)
            if ok is None and not detail:
                detail = "skipped"
            parts.append(f"{sym} {name}" + (f" ({detail})" if detail else ""))
        return " / ".join(parts)

    def finish(self, outcome, notes=""):
        """Append ONE markdown entry for this run and return. Idempotent (a second call is a no-op)
        so a caller's belt-and-suspenders finish in an except/finally cannot double-log. Never
        throws — a logging failure prints a warning and returns."""
        if self._finished:
            return
        self._finished = True
        try:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            entry = (
                f"\n## {ts} — {self.target} {self.version} [{self.mode}]\n"
                f"- HEAD: {_short_sha(self.repo)}\n"
                f"- gates: {self._render_gates()}\n"
                f"- outcome: {outcome}\n"
                f"- notes: {notes or '-'}\n"
            )
            fresh = not os.path.exists(LOG_PATH)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                if fresh:
                    f.write("# Release log\n\nAppend-only record of release_mods.py / "
                            "release_pack.py runs (VERIFY = local build/test/audit only; "
                            "DRY-RUN = plan; UPLOAD = published). Newest at the bottom.\n")
                f.write(entry)
        except Exception as e:  # pragma: no cover — logging must never break a release
            print(f"  [release-log] WARN could not write release-log entry: {e}")


def open_run(target, version, mode, repo=None):
    """Open a release-log run. `target` = mod/pack name, `version` = the resolved version, `mode` in
    {"VERIFY","DRY-RUN","UPLOAD"}, `repo` = path used for the HEAD short sha. Never throws."""
    try:
        return Run(str(target), str(version), str(mode), repo=repo)
    except Exception as e:  # pragma: no cover — logging must never break a release
        print(f"  [release-log] WARN could not open run: {e}")
        return Run("?", "?", str(mode))
