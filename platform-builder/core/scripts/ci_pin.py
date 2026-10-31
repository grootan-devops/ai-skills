#!/usr/bin/env python3
"""The CI platform of an application project and the CI-library release it pins.

A deploy job is wired at the release the project already runs, so its other includes and
reusable workflows and the deploy template come from one release. The pin is read from the
project's own files, the same way pins.py reads it for the audit:

    GitLab   include: `project` + `ref`, or a raw `remote:` URL carrying the ref in its path
    GitHub   a job-level `uses: <owner>/github-ci-library/.github/workflows/<file>@<ref>`

With several releases the oldest is the one the project runs; a project with no pin has not
been onboarded yet (`platform onboard`). `--fetch DIR` clones the library at the pin and
checks the backend's deploy template is in that release.

    python3 ci_pin.py <app-repo> [--platform gitlab|github] [--backend argocd] [--fetch DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml

    class _AnyTagLoader(yaml.SafeLoader):
        """SafeLoader that reads any `!tag` (GitLab's `!reference`) as plain data."""

    _AnyTagLoader.add_multi_constructor(
        "!", lambda loader, suffix, node: (loader.construct_sequence(node)
                                           if isinstance(node, yaml.SequenceNode)
                                           else loader.construct_scalar(node)
                                           if isinstance(node, yaml.ScalarNode)
                                           else loader.construct_mapping(node)))
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

LIBRARIES = {"gitlab": "gitlab-ci-library", "github": "github-ci-library"}
SOURCES = {name: f"https://github.com/grootan-devops/{name}" for name in LIBRARIES.values()}
#: Where each library keeps a backend's GitOps deploy template.
TEMPLATES = {"gitlab": "deploy/gitops/.{backend}.gitlab-ci.yml",
             "github": ".github/workflows/deploy-{backend}-gitops.yml"}
_REMOTE_REF = (
    re.compile(r"^https?://raw\.githubusercontent\.com/[^/]+/(?P<repo>[^/]+)/(?P<ref>[^/]+)/"),
    re.compile(r"^https?://[^/]+/(?:[^/]+/)*(?P<repo>[^/]+)/-/raw/(?P<ref>[^/]+)/"),
    re.compile(r"^https?://[^/]+/(?:[^/]+/)*(?P<repo>[^/-][^/]*)/raw/(?P<ref>[^/]+)/"),
)
_RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def release_key(ref: str) -> Optional[Tuple[int, int, int]]:
    m = _RELEASE.match(ref or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _load(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8", errors="replace"), Loader=_AnyTagLoader)
    except (OSError, yaml.YAMLError):
        return None


def gitlab_refs(data: Any, library: str) -> List[str]:
    refs: List[str] = []
    inc = data.get("include") if isinstance(data, dict) else None
    for entry in inc if isinstance(inc, list) else [inc] if inc else []:
        if not isinstance(entry, dict):
            continue
        project, ref = entry.get("project"), entry.get("ref")
        if isinstance(project, str) and project.rstrip("/").split("/")[-1] == library and isinstance(ref, str) and ref:
            refs.append(ref)
        remote = entry.get("remote")
        for rx in _REMOTE_REF if isinstance(remote, str) else ():
            m = rx.match(remote)
            if m:
                if m.group("repo") == library:
                    refs.append(m.group("ref"))
                break
    return list(dict.fromkeys(refs))


def github_refs(workflows: Dict[str, Any], library: str) -> List[str]:
    refs: List[str] = []
    for wf in workflows.values():
        jobs = wf.get("jobs") if isinstance(wf, dict) else None
        for job in jobs.values() if isinstance(jobs, dict) else ():
            uses = job.get("uses") if isinstance(job, dict) else None
            if isinstance(uses, str) and "@" in uses and "/.github/workflows/" in uses:
                path, _, ref = uses.rpartition("@")
                if path.split("/")[1:2] == [library] and ref:
                    refs.append(ref)
    return list(dict.fromkeys(refs))


def detect(repo: Path, explicit: Optional[str] = None) -> Optional[str]:
    """gitlab, github, or None when the files say neither or both (ask the user)."""
    if explicit:
        return explicit
    gitlab = (repo / ".gitlab-ci.yml").is_file()
    github = any((repo / ".github" / "workflows").glob("*.y*ml")) if (repo / ".github" / "workflows").is_dir() else False
    return "gitlab" if gitlab and not github else "github" if github and not gitlab else None


def pin(repo: Path, platform: str) -> Dict[str, Any]:
    library = LIBRARIES[platform]
    if platform == "gitlab":
        refs = gitlab_refs(_load(repo / ".gitlab-ci.yml"), library)
    else:
        wf_dir = repo / ".github" / "workflows"
        refs = github_refs({p.name: _load(p) for p in sorted(wf_dir.glob("*.y*ml"))}, library)
    releases = sorted((r for r in refs if release_key(r)), key=release_key)
    return {"platform": platform, "library": library, "source": SOURCES[library], "refs": refs,
            "pin": releases[0] if releases else (refs[0] if refs else None),
            "mixed": len(refs) > 1}


def fetch(info: Dict[str, Any], backend: str, into: Path) -> Path:
    """Clone the library at the pin into `into` and return the deploy template; stops if absent."""
    target = into / f"{info['library']}-{info['pin']}"
    if not target.is_dir():
        out = subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", info["pin"],
                              info["source"], str(target)], capture_output=True, text=True)
        if out.returncode != 0:
            raise SystemExit(f"cannot clone {info['source']} at {info['pin']}: {out.stderr.strip()[:300]}")
    template = target / TEMPLATES[info["platform"]].format(backend=backend)
    if not template.is_file():
        raise SystemExit(f"{info['library']} {info['pin']} ships no {template.relative_to(target)}: the project's "
                         "CI library must be upgraded first (`platform update`)")
    return template


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--platform", choices=sorted(LIBRARIES))
    ap.add_argument("--backend", default="argocd")
    ap.add_argument("--fetch", type=Path, help="clone the library at the pin here and check the deploy template")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    platform = detect(args.repo.resolve(), args.platform)
    if not platform:
        print("the project has both or neither of .gitlab-ci.yml and .github/workflows: ask which CI platform "
              "the deploy job goes to and pass --platform", file=sys.stderr)
        return 2
    info = pin(args.repo.resolve(), platform)
    if not info["pin"]:
        print(f"no {info['library']} pin in the project: it is not onboarded to the CI library; run "
              "`platform onboard` before wiring a deploy job", file=sys.stderr)
        return 2
    if args.fetch:
        info["template"] = str(fetch(info, args.backend, args.fetch))
        info["libraryCheckout"] = str(Path(info["template"]).parents[len(Path(TEMPLATES[platform]).parts) - 1])
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        for key, value in info.items():
            print(f"{key}: {value}")
        if info["mixed"]:
            print(f"note: the project pins several {info['library']} refs {info['refs']}; the deploy job uses "
                  f"{info['pin']}, the oldest — align the pins with `platform update` first if the user agrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
