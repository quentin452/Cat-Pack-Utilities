#!/usr/bin/env python3
"""Triage open issues + PRs across the forks/mods in memory/repos.json (the auto-generated
inventory). Uses `gh` (already authed). Answers "what's waiting on my fork repos?" without
hand-maintaining a fork list.

Scope (default = forks only):
  python3 triage_forks.py                 # all forks (is_fork=true)
  python3 triage_forks.py --all           # every owned repo
  python3 triage_forks.py --manifest      # only repos in release-manifest.json
  python3 triage_forks.py --repo NotEnoughItems FileDirector
  python3 triage_forks.py --mine          # only PRs I authored (mine to land) + all issues
  python3 triage_forks.py --json          # machine output
"""
import json
import os
import subprocess
import sys

REPOS = os.path.expanduser("~/Documents/GitHub/Mod-Sandbox/memory/repos.json")
ME = "quentin452"


def gh_json(slug, kind, extra=()):
    # kind = "issue" | "pr"
    cmd = ["gh", kind, "list", "-R", slug, "--state", "open", "--limit", "50",
           "--json", "number,title,author,updatedAt,isDraft" if kind == "pr"
           else "number,title,author,updatedAt", *extra]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None  # repo may not exist on the remote / no access
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def main():
    argv = sys.argv[1:]
    data = json.load(open(REPOS))
    repos = data["repos"]
    if "--all" in argv:
        sel = repos
    elif "--manifest" in argv:
        sel = [r for r in repos if r["in_release_manifest"]]
    elif "--repo" in argv:
        names = argv[argv.index("--repo") + 1:]
        names = [n for n in names if not n.startswith("--")]
        sel = [r for r in repos if r["name"] in names]
    else:
        sel = [r for r in repos if r["is_fork"]]

    mine_only = "--mine" in argv
    out = []
    for r in sel:
        slug = r["slug"]
        if not slug:
            continue
        issues = gh_json(slug, "issue") or []
        prs = gh_json(slug, "pr") or []
        if mine_only:
            prs = [p for p in prs if (p.get("author") or {}).get("login") == ME]
        if issues or prs:
            out.append({"name": r["name"], "slug": slug, "issues": issues, "prs": prs})

    if "--json" in argv:
        print(json.dumps(out, indent=2))
        return

    if not out:
        print("No open issues/PRs across the selected repos.")
        return
    total_i = sum(len(o["issues"]) for o in out)
    total_p = sum(len(o["prs"]) for o in out)
    print(f"=== {len(out)} repo(s) with activity — {total_i} open issues, {total_p} open PRs ===\n")
    for o in sorted(out, key=lambda x: -(len(x["issues"]) + len(x["prs"]))):
        print(f"### {o['name']}  ({o['slug']})  — {len(o['issues'])} issues, {len(o['prs'])} PRs")
        for p in o["prs"]:
            who = (p.get("author") or {}).get("login", "?")
            draft = " [draft]" if p.get("isDraft") else ""
            mine = " (MINE)" if who == ME else ""
            print(f"  PR  #{p['number']}{draft}{mine}  {p['title']}  — @{who}  {p['updatedAt'][:10]}")
        for i in o["issues"]:
            who = (i.get("author") or {}).get("login", "?")
            print(f"  ISS #{i['number']}  {i['title']}  — @{who}  {i['updatedAt'][:10]}")
        print()


if __name__ == "__main__":
    main()
