#!/usr/bin/env python3
"""The library release an environment or a service chart pins, and the data it ships.

An environment's root chart depends on argocd-gitops-tpl-library, and a service chart on
helm-tpl-library (published as `tpl-library`). `helm dependency build` vendors exactly the
pinned version into charts/, and that package is what the chart renders with. So names,
namespaces and release names come from `helm template`, and the rules the scripts need from
the data file inside the same package: docs/environment-map.json of the GitOps library,
values.schema.json and docs/chart-contract.json of the service library. A version that ships
no data file, or one with a schema these scripts do not know, stops here with what to upgrade.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

GITOPS_LIBRARY = "argocd-gitops-tpl-library"
SERVICE_LIBRARY = "tpl-library"
ENVIRONMENT_MAP = "docs/environment-map.json"
CHART_CONTRACT = "docs/chart-contract.json"
#: Where an upgrade comes from, for the messages only; the release itself is the pin.
SOURCES = {GITOPS_LIBRARY: "https://github.com/grootan-devops/argocd-gitops-tpl-library",
           SERVICE_LIBRARY: "https://github.com/grootan-devops/helm-tpl-library",
           "skills": "https://github.com/grootan-devops/ai-skills"}
SUPPORTED_SCHEMAS = (1,)


def _release_key(tag: str):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", tag)
    return tuple(int(g) for g in m.groups()) if m else None


def latest_release(name: str) -> Optional[str]:
    """The highest release tag of a library, best effort: None offline."""
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", "--refs", SOURCES[name]],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    tags = [ln.split("refs/tags/", 1)[1] for ln in out.stdout.splitlines() if "refs/tags/" in ln]
    releases = sorted((t for t in tags if _release_key(t)), key=_release_key)
    return releases[-1] if out.returncode == 0 and releases else None


def dependency_version(chart_dir: Path, chart_name: str) -> Optional[str]:
    data = yaml.safe_load((chart_dir / "Chart.yaml").read_text(encoding="utf-8")) if (chart_dir / "Chart.yaml").exists() else None
    for dep in (data or {}).get("dependencies") or []:
        if isinstance(dep, dict) and dep.get("name") == chart_name:
            return str(dep.get("version") or "")
    return None


def vendored(chart_dir: Path, chart_name: str) -> Path:
    """charts/<chart_name>-<version>.tgz of `chart_dir`, built with helm when it is missing."""
    def found() -> List[Path]:
        return sorted((chart_dir / "charts").glob(f"{chart_name}-*.tgz"))

    if not found():
        if not shutil.which("helm"):
            raise SystemExit(f"helm is not installed, and {chart_dir}/charts holds no {chart_name} package: "
                             f"run `helm dependency build {chart_dir}` where helm is available")
        build = subprocess.run(["helm", "dependency", "build", str(chart_dir)], capture_output=True, text=True)
        if build.returncode != 0:
            raise SystemExit(f"helm dependency build {chart_dir} failed: {build.stderr.strip()[:400]}")
    packages = found()
    if not packages:
        raise SystemExit(f"{chart_dir}/Chart.yaml has no {chart_name} dependency")
    return packages[-1]


def read_member(package: Path, rel: str) -> Optional[bytes]:
    """A file of a packaged chart, by its path inside the chart (`docs/x.json`)."""
    with tarfile.open(package) as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and m.name.split("/", 1)[-1] == rel), None)
        return tar.extractfile(member).read() if member else None


def _skills_version() -> Optional[str]:
    """The release of these skills, from the repository's VERSION file when it is installed."""
    version = Path(__file__).resolve().parents[2] / "VERSION"
    return version.read_text(encoding="utf-8").strip() if version.is_file() else None


def load_data(package: Path, rel: str, library: str) -> Dict[str, Any]:
    """A data file the scripts need, from the vendored release; stops when it cannot be used."""
    raw = read_member(package, rel)
    if raw is None:
        latest = latest_release(library)
        pinned = package.name[len(library) + 1:-len(".tgz")]
        if latest and latest == pinned:
            raise SystemExit(f"{package.name} ships no {rel}, which this skill reads, and it is {library}'s "
                             "latest release: no release ships the file yet. Keep the skills at the "
                             "release you used before until one does.")
        to = f" to {latest}" if latest else " to the latest release"
        raise SystemExit(f"{package.name} ships no {rel}, which this skill reads: it needs a {library} "
                         f"release that ships it. Upgrade the chart's {library} dependency{to}.")
    data = json.loads(raw)
    if data.get("schema") not in SUPPORTED_SCHEMAS:
        latest, own = latest_release("skills"), _skills_version()
        known = f"{package.name}: {rel} has schema {data.get('schema')!r}, which this skill does not know."
        if latest and own and _release_key(own) and _release_key(latest) <= _release_key(own):
            raise SystemExit(f"{known} No skills release reads it yet ({own} is the latest).")
        raise SystemExit(f"{known} Upgrade the skills to the latest release{f' ({latest})' if latest else ''}.")
    return data


def environment_map(root: Path) -> Dict[str, Any]:
    """The environment map of the GitOps library release the root and extras charts pin."""
    pin = dependency_version(root, GITOPS_LIBRARY)
    if pin is None:
        raise SystemExit(f"{root}/Chart.yaml does not depend on {GITOPS_LIBRARY}")
    extras = dependency_version(root / "extras", GITOPS_LIBRARY) if (root / "extras" / "Chart.yaml").exists() else None
    if extras is not None and extras != pin:
        raise SystemExit(f"the root chart pins {GITOPS_LIBRARY} {pin} but extras/ pins {extras}: "
                         "both charts must pin the same release")
    return load_data(vendored(root, GITOPS_LIBRARY), ENVIRONMENT_MAP, GITOPS_LIBRARY)


def service_root_keys(chart_dir: Path) -> List[str]:
    """Root keys of the values contract of the service library release a chart pins.

    Its values.schema.json properties, plus the keys its templates read without declaring
    them (the chart contract's `values.templateKeys`). Anything else at the root of a
    service's values is that application's own configuration.
    """
    package = vendored(chart_dir, SERVICE_LIBRARY)
    contract = load_data(package, CHART_CONTRACT, SERVICE_LIBRARY)
    schema = json.loads(read_member(package, "values.schema.json") or b"{}")
    return sorted(set(schema.get("properties") or {}) | set((contract.get("values") or {}).get("templateKeys") or []))


def path_matcher(patterns: Iterable[str], containers: Iterable[str] = ()) -> Callable[[str], bool]:
    """A test for dotted value paths: `*` is one segment, a trailing `**` any depth (or none).

    `{container}` stands for each of `containers` (themselves patterns, e.g. `jobs.*.containers.*`).
    """
    def segments(pattern: str) -> str:
        parts = pattern.split(".")
        tail = parts[-1] == "**"
        body = r"\.".join("[^.]+" if p == "*" else re.escape(p) for p in (parts[:-1] if tail else parts))
        return body + (r"(\..+)?" if tail else "")

    expanded: List[str] = []
    for pattern in patterns:
        if "{container}" in pattern:
            expanded += [pattern.replace("{container}", c) for c in containers]
        else:
            expanded.append(pattern)
    rx = re.compile("^(?:" + "|".join(segments(p) for p in expanded) + ")$") if expanded else None
    return lambda path: bool(rx and rx.match(path))


def render(chart_dir: Path) -> List[Dict[str, Any]]:
    """`helm template` of a chart whose dependencies are vendored: its documents."""
    if not shutil.which("helm"):
        raise SystemExit("helm is not installed: the Application names, namespaces and release "
                         "names are what the pinned library renders, so this needs helm")
    out = subprocess.run(["helm", "template", str(chart_dir)], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"helm template {chart_dir} failed: {out.stderr.strip()[:400]}")
    return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)]
