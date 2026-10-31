#!/usr/bin/env python3
"""Is this skill the latest ai-skills release? Run at the start of every invocation.

A skill is installed one of two ways:

    git checkout   the skill sits in a clone of ai-skills: its tag, or else the release in the
                   clone's root VERSION file, and the commit it is on
    copied folder  `npx skills add` copied only the skill, and no release with it: the
                   installed release is unknown, so the reinstall command is printed

The latest release is the highest release tag of the ai-skills repository. A checkout on a
branch is also compared with that branch on the remote, so unreleased commits show up too.
Nothing is updated here: the result says what to run, and the agent asks the user first.
Offline or without git, it says it could not check and the skill carries on.

    python3 update_check.py [--json]       exit 0 current, 1 update available,
                                           2 not checked, or the installed release is unknown

Every skill ships an identical copy of this file; a test keeps them in step.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPOSITORY = "https://github.com/grootan-devops/ai-skills"
_RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TIMEOUT = 8


def release_key(tag: Optional[str]) -> Optional[Tuple[int, int, int]]:
    m = _RELEASE.match((tag or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def skill_root(start: Path) -> Path:
    """The skill folder: the nearest parent with a SKILL.md that is not an alias."""
    for parent in [start] + list(start.parents):
        if (parent / "SKILL.md").is_file() and parent.parent.name != "aliases":
            return parent
    raise SystemExit(f"no SKILL.md above {start}")


def _git(*args: str, cwd: Optional[Path] = None) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def remote_refs(url: str = REPOSITORY) -> Optional[Dict[str, str]]:
    """{ref name: commit} of the ai-skills remote, or None when it cannot be reached."""
    out = _git("ls-remote", "--tags", "--heads", url)
    if out is None:
        return None
    refs: Dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.endswith("^{}"):  # an annotated tag's commit
            refs[ref[:-3]] = sha
        else:
            refs.setdefault(ref, sha)
    return refs


def latest_release(refs: Dict[str, str]) -> Optional[str]:
    tags = [r[len("refs/tags/"):] for r in refs if r.startswith("refs/tags/")]
    releases = sorted((t for t in tags if release_key(t)), key=release_key)
    return releases[-1] if releases else None


def checkout(root: Path) -> Optional[Dict[str, Optional[str]]]:
    """The git checkout of ai-skills the skill sits in, or None for a copied folder."""
    top = _git("rev-parse", "--show-toplevel", cwd=root)
    if not top:
        return None
    origin = _git("config", "--get", "remote.origin.url", cwd=root) or ""
    if "ai-skills" not in origin:
        return None
    return {"path": top,
            "commit": _git("rev-parse", "HEAD", cwd=root),
            "tag": _git("describe", "--tags", "--exact-match", "HEAD", cwd=root),
            "branch": _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=root)}


def check(root: Path, refs: Optional[Dict[str, str]]) -> Dict[str, object]:
    """What is installed, what is latest, and the command that updates it."""
    name = root.name
    repo = checkout(root)
    version_file = Path(repo["path"]) / "VERSION" if repo else None
    version = version_file.read_text(encoding="utf-8").strip() if version_file and version_file.is_file() else None
    result: Dict[str, object] = {"skill": name, "installed": version, "install": "checkout" if repo else "copied"}
    if repo:
        result.update({"checkout": repo["path"], "commit": repo["commit"], "tag": repo["tag"], "branch": repo["branch"]})
    if refs is None:
        result.update({"status": "not-checked", "reason": f"{REPOSITORY} could not be reached"})
        return result
    latest = latest_release(refs)
    result["latest"] = latest
    notes: List[str] = []
    installed = (repo or {}).get("tag") or version
    behind = bool(latest and release_key(installed) and release_key(installed) < release_key(latest))
    if repo and repo.get("branch"):
        remote = refs.get(f"refs/heads/{repo['branch']}")
        if remote and remote != repo.get("commit"):
            behind = True
            notes.append(f"branch {repo['branch']} has newer commits on the remote")
        update = f"git -C {repo['path']} pull --ff-only"
    elif repo:
        update = f"git -C {repo['path']} fetch --tags && git -C {repo['path']} checkout {latest}"
    else:
        update = (f"npx skills add {REPOSITORY} --skill {name}"
                  "  (add --global if it was installed globally)")
    unknown = bool(latest) and not behind and not release_key(installed) and not (repo and repo.get("branch"))
    if unknown:
        notes.append("the installed release is unknown: a copied skill does not record it")
    result.update({"status": "unknown" if unknown else "update-available" if behind else "current",
                   "update": update, "notes": notes})
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = check(skill_root(Path(__file__).resolve().parent), remote_refs())
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["status"] == "not-checked":
        print(f"{result['skill']}: could not check for a newer ai-skills release ({result['reason']}); continuing")
    elif result["status"] == "unknown":
        print(f"{result['skill']}: the installed release is unknown (a copied skill does not record it); "
              f"latest {result['latest']}")
        print(f"  to be sure it is the latest: {result['update']}")
        print("  >> Tell the user, and ask whether to reinstall now. Never run it without a yes.")
    elif result["status"] == "current":
        print(f"{result['skill']}: up to date ({result.get('tag') or result['installed']}, latest {result['latest']})")
    else:
        installed = result.get("tag") or result["installed"] or (result.get("commit") or "")[:12] or "unknown"
        print(f"{result['skill']}: an update is available: installed {installed}, latest {result['latest']}")
        for note in result["notes"]:
            print(f"  {note}")
        print(f"  update with: {result['update']}")
        print("  >> Ask the user whether to update now. Never run it without a yes.")
    return {"current": 0, "update-available": 1}.get(str(result["status"]), 2)


if __name__ == "__main__":
    sys.exit(main())
