#!/usr/bin/env python3
"""wiki_sync.py — generate the OptimizationsAndTweaks wiki Mixins page FROM THE CODE.

The wiki went stale every time mixins were added/removed (the old Optimizations/Tweaks pages
still documented the retired 263-boolean config). Same class of fix as the derived changelogs:
stop hand-maintaining, DERIVE. Source of truth:
  - asm/Mixin.java   -> the registry: enum name, Side, condition (always / require(MOD) / config
                        lambda), mixin path (category = first path segment)
  - each mixin file  -> first javadoc sentence (or @reason) = the human description

Output: <wiki-clone>/Mixins.md (fully generated, banner + per-target tables) and stub redirects
for the stale Optimizations.md / Tweaks.md pages.

Usage:
  python3 wiki_sync.py                       # print summary + write nothing
  python3 wiki_sync.py --wiki /tmp/oat-wiki  # regenerate pages in a local wiki clone
The wiki push is OUTWARD (public): commit+push only with explicit user go.
"""

import argparse
import html
import re
import sys
from pathlib import Path

OAT = Path.home() / "Documents/GitHub/OptimizationsAndTweaks"
ENUM = OAT / "src/main/java/fr/iamacat/optimizationsandtweaks/asm/Mixin.java"
MIXINS_ROOT = OAT / "src/main/java/fr/iamacat/optimizationsandtweaks/mixins"

ENTRY = re.compile(
    r"^\s{4}(?P<name>[a-z]\w+)\(\s*Side\.(?P<side>\w+)\s*,\s*(?P<cond>.*?)\s*,\s*\n?\s*\"(?P<path>[^\"]+)\"\s*\)\s*[,;]",
    re.MULTILINE | re.DOTALL)
JAVADOC = re.compile(r"/\*\*(.*?)\*/\s*(?:@\w+(?:\([^)]*\))?\s*)*public\s+(?:abstract\s+)?class", re.DOTALL)


def clean_condition(cond: str) -> str:
    cond = " ".join(cond.split())
    if cond == "always()":
        return "always"
    m = re.match(r"require\(TargetedMod\.(\w+)\)", cond)
    if m:
        return f"requires {m.group(1)}"
    m = re.search(r"OptimizationsandTweaksConfig\.(\w+)", cond)
    if m:
        return f"config: {m.group(1)}"
    return cond[:60]


def first_sentence(text: str) -> str:
    # strip javadoc stars/tags, keep the first sentence of the class doc
    lines = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("*").strip()
        if line.startswith("@"):  # tags — @reason is a fallback description
            if line.startswith("@reason") and not lines:
                lines.append(line[len("@reason"):].strip())
            continue
        if line in ("<p>", "</p>", ""):
            if lines:
                break  # first paragraph done
            continue
        lines.append(line)
    para = " ".join(lines)
    para = re.sub(r"\{@code ([^}]*)\}", r"`\1`", para)
    para = re.sub(r"\{@link ([^}]*)\}", r"`\1`", para)
    para = re.sub(r"<[^>]+>", "", para)
    m = re.match(r"(.+?\.)(\s|$)", para)
    return html.unescape((m.group(1) if m else para).strip())


def mixin_description(side: str, path: str) -> str:
    file = MIXINS_ROOT / side.lower() / (path.replace(".", "/") + ".java")
    if not file.is_file():
        return ""
    m = JAVADOC.search(file.read_text(encoding="utf-8", errors="replace"))
    return first_sentence(m.group(1)) if m else ""


def collect():
    entries = []
    for m in ENTRY.finditer(ENUM.read_text(encoding="utf-8")):
        side, path = m.group("side"), m.group("path")
        entries.append({
            "side": side,
            "path": path,
            "target": path.split(".")[0],
            "cls": path.split(".")[-1],
            "cond": clean_condition(m.group("cond")),
            "desc": mixin_description(side, path),
        })
    return entries


def render(entries) -> str:
    by_target = {}
    for e in entries:
        by_target.setdefault(e["target"], []).append(e)
    out = []
    out.append("> **AUTO-GENERATED — do not hand-edit.** Regenerated from `asm/Mixin.java` + each mixin's")
    out.append("> javadoc by `Cat-Pack-Utilities/wiki_sync.py` at every release. To toggle mixins, see")
    out.append("> [Config-File](Config-File) (MixinConfigResolver categories).")
    out.append("")
    common = sum(1 for e in entries if e["side"] == "COMMON")
    out.append(f"**{len(entries)} mixins** ({common} common, {len(entries) - common} client) "
               f"across **{len(by_target)}** target groups.")
    out.append("")
    for target in sorted(by_target, key=lambda t: (t != "core", t)):
        group = by_target[target]
        out.append(f"## {target} ({len(group)})")
        out.append("")
        out.append("| Mixin | Side | Enabled | What it does |")
        out.append("|---|---|---|---|")
        for e in sorted(group, key=lambda x: x["cls"]):
            desc = e["desc"].replace("|", "\\|") or "*(no javadoc yet)*"
            out.append(f"| `{e['cls']}` | {e['side'].lower()} | {e['cond']} | {desc} |")
        out.append("")
    return "\n".join(out) + "\n"


STUB = """> **This page was retired (2026-07-08).** It documented the old per-mixin boolean config
> (263 flags), removed in the MixinConfigResolver rework. The live, always-current list is
> **[Mixins](Mixins)** (auto-generated from the code at each release); configuration is
> documented in **[Config-File](Config-File)**.
"""


def main():
    ap = argparse.ArgumentParser(description="Generate the OaT wiki Mixins page from the code.")
    ap.add_argument("--wiki", type=Path, help="path to a local clone of OptimizationsAndTweaks.wiki.git")
    args = ap.parse_args()

    entries = collect()
    if not entries:
        sys.exit("parsed 0 entries from asm/Mixin.java — regex drift, fix wiki_sync.py")
    no_doc = [e for e in entries if not e["desc"]]
    print(f"[wiki-sync] parsed {len(entries)} mixins, {len(no_doc)} without a javadoc description")
    page = render(entries)
    if not args.wiki:
        print(page[:1500])
        print(f"... ({len(page)} chars total; pass --wiki <clone> to write)")
        return
    (args.wiki / "Mixins.md").write_text(page, encoding="utf-8")
    for stale in ("Optimizations.md", "Tweaks.md", "Fixe.md"):
        if (args.wiki / stale).is_file():
            (args.wiki / stale).write_text(STUB, encoding="utf-8")
    home = args.wiki / "Home.md"
    if home.is_file() and "Mixins" not in home.read_text(encoding="utf-8"):
        home.write_text(home.read_text(encoding="utf-8").rstrip()
                        + "\n\n- [Mixins](Mixins) — full auto-generated mixin list\n", encoding="utf-8")
    print(f"[wiki-sync] wrote Mixins.md (+ stubs for Optimizations/Tweaks) in {args.wiki}")
    print("[wiki-sync] review `git -C <wiki> diff`, then commit+push ONLY with explicit user go (public).")


if __name__ == "__main__":
    main()
