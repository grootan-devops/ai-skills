#!/usr/bin/env python3
"""The library releases a repository pins, read from its own files.

    GitLab   include: `project` + `ref`, or a raw `remote:` URL carrying the ref in its path
    GitHub   a job-level `uses: <owner>/<repo>/.github/workflows/<file>@<ref>`
    Helm     a chart's Chart.yaml dependency on a library chart

The pin is what the project runs today, so it is the release whose docs and data describe it.
A leaf module -- standard library plus optional PyYAML -- so libraries.py and both platform
adapters share one parser without importing each other.
"""

from __future__ import annotations

import re
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
    HAVE_YAML = True
except ImportError:  # pragma: no cover
    HAVE_YAML = False

#: Raw-file URLs that carry a repository name and ref: GitHub raw, GitHub /raw/, GitLab /-/raw/.
_REMOTE_REF = (
    re.compile(r"^https?://raw\.githubusercontent\.com/[^/]+/(?P<repo>[^/]+)/(?P<ref>[^/]+)/"),
    re.compile(r"^https?://[^/]+/(?:[^/]+/)*(?P<repo>[^/]+)/-/raw/(?P<ref>[^/]+)/"),
    re.compile(r"^https?://[^/]+/(?:[^/]+/)*(?P<repo>[^/-][^/]*)/raw/(?P<ref>[^/]+)/"),
)

#: A release tag: `1.2.3`, optionally `v`-prefixed. Pre-releases and builds are not releases.
RELEASE_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def release_key(ref: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Sort key of a release tag, or None for anything else (a branch, a SHA, a candidate)."""
    m = RELEASE_RE.match((ref or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def load_yaml(path: Path) -> Any:
    if not HAVE_YAML or not path.is_file():
        return None
    try:
        return yaml.load(path.read_text(encoding="utf-8", errors="replace"), Loader=_AnyTagLoader)
    except yaml.YAMLError:
        return None


def _add(found: Dict[str, List[str]], name: str, ref: str) -> None:
    refs = found.setdefault(name, [])
    if ref not in refs:
        refs.append(ref)


def gitlab_refs(data: Any) -> Dict[str, List[str]]:
    """{library repository name: [refs]} for every include of a parsed `.gitlab-ci.yml`."""
    found: Dict[str, List[str]] = {}
    inc = data.get("include") if isinstance(data, dict) else None
    for entry in (inc if isinstance(inc, list) else [inc] if inc else []):
        if not isinstance(entry, dict):
            continue
        project, ref = entry.get("project"), entry.get("ref")
        if isinstance(project, str) and isinstance(ref, str) and ref:
            _add(found, project.rstrip("/").split("/")[-1], ref)
        remote = entry.get("remote")
        if isinstance(remote, str):
            for rx in _REMOTE_REF:
                m = rx.match(remote)
                if m:
                    _add(found, m.group("repo"), m.group("ref"))
                    break
    return found


def github_refs(workflows: Dict[str, Any]) -> Dict[str, List[str]]:
    """{library repository name: [refs]} for every reusable workflow the parsed workflows call.

    A library module is invoked with a JOB-level `uses:`, which step-level pinning checks
    never look at.
    """
    found: Dict[str, List[str]] = {}
    for wf in workflows.values():
        jobs = wf.get("jobs") if isinstance(wf, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job in jobs.values():
            uses = job.get("uses") if isinstance(job, dict) else None
            if not isinstance(uses, str) or "@" not in uses or "/.github/workflows/" not in uses:
                continue
            path, _, ref = uses.rpartition("@")
            parts = path.split("/")
            if len(parts) >= 2 and ref:
                _add(found, parts[1], ref)
    return found


def chart_dir(root: Path, declared: str = "") -> Path:
    """The chart location, honouring a declared CHART_DIR.

    A root-level Chart.yaml is normal for library and umbrella charts; assuming ./chart
    produces a spurious blocking finding on exactly those repositories.
    """
    if declared:
        return (root / declared).resolve()
    if (root / "Chart.yaml").exists():
        return root
    for candidate in ("chart", "helm", "charts"):
        if (root / candidate / "Chart.yaml").exists():
            return root / candidate
    return root / "chart"


def chart_refs(chart: Path, dependencies: Dict[str, str]) -> Dict[str, List[str]]:
    """{library name: [versions]} for the library charts a Chart.yaml depends on.

    `dependencies` maps a library name to the chart name it is published as.
    """
    found: Dict[str, List[str]] = {}
    data = load_yaml(chart / "Chart.yaml")
    deps = data.get("dependencies") if isinstance(data, dict) else None
    by_chart = {chart_name: lib for lib, chart_name in dependencies.items()}
    for dep in deps if isinstance(deps, list) else []:
        if isinstance(dep, dict) and dep.get("name") in by_chart and dep.get("version"):
            _add(found, by_chart[dep["name"]], str(dep["version"]))
    return found


def _github_workflows(repo: Path) -> Dict[str, Any]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return {}
    return {p.name: load_yaml(p) for p in sorted(wf_dir.glob("*.y*ml"))}


def repo_pins(repo: Path, platform: Optional[str],
              chart_dependencies: Dict[str, str]) -> Tuple[Dict[str, List[str]], Path]:
    """({library: [refs]}, chart dir) for everything the repository pins.

    Reads the CI files of `platform` (both when it is unknown) and the chart's Chart.yaml.
    """
    found: Dict[str, List[str]] = {}
    declared = ""
    if platform in (None, "gitlab"):
        data = load_yaml(repo / ".gitlab-ci.yml")
        for name, refs in gitlab_refs(data).items():
            for ref in refs:
                _add(found, name, ref)
        variables = data.get("variables") if isinstance(data, dict) else None
        if isinstance(variables, dict) and isinstance(variables.get("CHART_DIR"), str):
            declared = variables["CHART_DIR"]
    if platform in (None, "github"):
        for name, refs in github_refs(_github_workflows(repo)).items():
            for ref in refs:
                _add(found, name, ref)
    cdir = chart_dir(repo, declared)
    for name, refs in chart_refs(cdir, chart_dependencies).items():
        for ref in refs:
            _add(found, name, ref)
    return found, cdir


def pick(refs: List[str]) -> Optional[str]:
    """The ref a project with these pins is measured against: its oldest release.

    Includes pinned to different releases run the oldest one's contract somewhere, so the
    docs and the migration chain start there. Without a release among them, the first ref.
    """
    releases = [r for r in refs if release_key(r)]
    if releases:
        return min(releases, key=release_key)
    return refs[0] if refs else None
