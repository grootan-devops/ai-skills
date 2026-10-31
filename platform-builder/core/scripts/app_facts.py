#!/usr/bin/env python3
"""What an application project publishes, read from its own files, and whether it is published.

A project onboarded with `platform onboard` records nothing apart from its chart and CI files,
so this reads them the way the audit does:

    chart    the chart directory (a declared CHART_DIR, a root Chart.yaml, chart/, helm/,
             charts/), its name and version, and whether it is built on the service library
    values   partOf, component, subComponent, the image registry and repository; an empty
             repository is the library's default <partOf>/<component>[/<subComponent>]
    ci       the CI platform, the CI-library pin (ci_pin.py), and the registry and repository
             settings the project's CI passes to the library

Where CI publishes the chart and the image is the CI library's rule at the pin; the agent works
it out from the library docs and passes it to `--check`, which asks the registry, read-only and
with the user's existing login, whether that chart version and image tag exist.

    python3 app_facts.py <app-repo> [--json]
    python3 app_facts.py <app-repo> --check --chart-repo oci://<host>/<path> [--image <host>/<path>]
        [--channel rc|release | --version V] [--image-tag T] [--json]

    exit 0 facts read / published, 1 could not check the registry,
         2 not onboarded (run `platform onboard` first), 3 not published yet
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_pin  # noqa: E402
import gitops_library as library  # noqa: E402
import pins  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

#: CI settings that say where a project publishes; credentials are never read.
_PUBLISH_KEY = re.compile(r"(?i)(registry|repository|chart[-_]dir)")
_CREDENTIAL_KEY = re.compile(r"(?i)(user(name)?|password|token|secret|config)$")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:[+_][0-9A-Za-z.-]+)?$")
_TIMEOUT = 30


def chart_dir(root: Path, declared: str = "") -> Optional[Path]:
    """The chart directory the platform checks use (pins.chart_dir), or None without a Chart.yaml."""
    found = pins.chart_dir(root, declared)
    return found if (found / "Chart.yaml").is_file() else None


def _yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def publish_settings(repo: Path, platform: Optional[str]) -> Dict[str, str]:
    """Registry and repository settings the project's CI passes to the library, credentials left out."""
    found: Dict[str, str] = {}

    def keep(key: Any, value: Any) -> None:
        if (isinstance(key, str) and _PUBLISH_KEY.search(key) and not _CREDENTIAL_KEY.search(key)
                and isinstance(value, (str, int, float))):
            found.setdefault(key, str(value))

    if platform == "gitlab":
        data = ci_pin._load(repo / ".gitlab-ci.yml")
        variables = data.get("variables") if isinstance(data, dict) else None
        for key, value in (variables or {}).items() if isinstance(variables, dict) else ():
            keep(key, value.get("value") if isinstance(value, dict) else value)
    elif platform == "github":
        for wf in sorted((repo / ".github" / "workflows").glob("*.y*ml")):
            data = ci_pin._load(wf)
            jobs = data.get("jobs") if isinstance(data, dict) else None
            for job in jobs.values() if isinstance(jobs, dict) else ():
                uses = job.get("uses") if isinstance(job, dict) else None
                if isinstance(uses, str) and ci_pin.LIBRARIES["github"] in uses:
                    for key, value in (job.get("with") or {}).items():
                        keep(key, value)
    return found


def facts(repo: Path, platform: Optional[str] = None) -> Dict[str, Any]:
    """Everything the GitOps side needs from the application project's own files."""
    platform = ci_pin.detect(repo, platform)
    pin = ci_pin.pin(repo, platform) if platform else None
    settings = publish_settings(repo, platform)
    declared = next((v for k, v in settings.items() if re.search(r"(?i)chart[-_]dir", k)), "")
    cdir = chart_dir(repo, declared)

    chart: Optional[Dict[str, Any]] = None
    values: Dict[str, Any] = {}
    if cdir:
        meta = _yaml(cdir / "Chart.yaml")
        deps = [d.get("name") for d in meta.get("dependencies") or [] if isinstance(d, dict)]
        chart = {"dir": str(cdir), "name": meta.get("name"), "version": str(meta.get("version", "")),
                 "appVersion": str(meta.get("appVersion", "")),
                 "serviceLibrary": library.SERVICE_LIBRARY in deps}
        values = _yaml(cdir / "values.yaml")

    glob = values.get("global") if isinstance(values.get("global"), dict) else {}
    part_of = values.get("partOf") or glob.get("partOf") or ""
    component, sub = values.get("component") or "", values.get("subComponent") or ""
    main = ((values.get("containers") or {}).get("main") or {}) if isinstance(values.get("containers"), dict) else {}
    explicit = ((main.get("image") or {}).get("repository") or "") if isinstance(main, dict) else ""
    path = "/".join(p for p in (component, sub) if p)
    derived = f"{part_of}/{path}" if part_of and path else ""
    image = {"registry": ((glob.get("image") or {}).get("registry") or ""),
             "repository": explicit or derived, "derived": not explicit and bool(derived)}

    dockerfile = (repo / "Dockerfile").is_file()
    shape = ("service-with-chart" if chart and dockerfile else "chart-only" if chart
             else "image-only" if dockerfile else None)
    missing = []
    if not platform:
        missing.append("CI platform: the project has both or neither of .gitlab-ci.yml and .github/workflows")
    elif not pin["pin"]:
        missing.append(f"no {pin['library']} pin: the project is not onboarded to the CI library")
    if not shape:
        missing.append("no chart and no Dockerfile")
    if chart and not chart["name"]:
        missing.append(f"{cdir}/Chart.yaml has no name")
    return {"repository": str(repo), "shape": shape, "chart": chart,
            "values": {"partOf": part_of, "component": component, "subComponent": sub},
            "image": image, "ci": pin and {**pin, "publish": settings}, "missing": missing}


def version_key(tag: str) -> Optional[Tuple]:
    m = _VERSION.match(tag or "")
    if not m:
        return None
    pre = m.group(4)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.-]", pre)) if pre else ()
    return tuple(int(g) for g in m.groups()[:3]) + ((0,) + parts if pre else (1,))


def pick(tags: List[str], channel: str) -> Optional[str]:
    """The newest tag of a channel: rc takes pre-releases and releases, release only releases."""
    keep = [t for t in tags if version_key(t) and (channel == "rc" or not _VERSION.match(t).group(4))]
    return max(keep, key=version_key) if keep else None


def _run(cmd: List[str]) -> Tuple[int, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return out.returncode, (out.stdout if out.returncode == 0 else out.stderr).strip()


def list_tags(ref: str) -> Tuple[Optional[List[str]], str]:
    """Tags of an OCI repository with oras or crane; (None, why) when neither can list them."""
    ref = ref[len("oci://"):] if ref.startswith("oci://") else ref
    tried = []
    for tool, cmd in (("oras", ["oras", "repo", "tags", ref]), ("crane", ["crane", "ls", ref])):
        if not shutil.which(tool):
            continue
        code, out = _run(cmd)
        if code == 0:
            return [line.strip() for line in out.splitlines() if line.strip()], tool
        tried.append(f"{tool}: {out.splitlines()[-1] if out else 'failed'}")
    return None, "; ".join(tried) or "neither oras nor crane is installed"


def _exists(kind: str, ref: str, tag: str) -> Tuple[Optional[bool], str]:
    """Whether one chart version or image tag exists, with the first tool that is installed."""
    if kind == "chart":
        tools = [("helm", ["helm", "show", "chart", ref, "--version", tag])]
    else:
        tools = [("crane", ["crane", "manifest", f"{ref}:{tag}"]),
                 ("skopeo", ["skopeo", "inspect", "--raw", f"docker://{ref}:{tag}"]),
                 ("docker", ["docker", "manifest", "inspect", f"{ref}:{tag}"])]
    for tool, cmd in tools:
        if shutil.which(tool):
            code, out = _run(cmd)
            if code == 0:
                return True, tool
            if re.search(r"(?i)not found|manifest unknown|no such|404", out):
                return False, tool
            return None, f"{tool}: {out.splitlines()[-1] if out else 'failed'}"
    return None, f"none of {', '.join(t for t, _ in tools)} is installed"


def published(chart_name: str, chart_repo: str, image: Optional[str], channel: str = "release",
              version: Optional[str] = None, image_tag: Optional[str] = None) -> Dict[str, Any]:
    """Is the chart version (and the image tag) in the registry? Never writes to it."""
    chart_ref = f"{chart_repo.rstrip('/')}/{chart_name}"
    tags, how = list_tags(chart_ref)
    chosen = version or (pick(tags, channel) if tags is not None else None)
    chosen = chosen and chosen.replace("_", "+")  # OCI tags carry Helm's "+" as "_"
    chart: Dict[str, Any] = {"ref": chart_ref, "version": chosen, "tool": how}
    if tags is not None:
        chart["found"] = bool(chosen) and chosen.replace("+", "_") in tags
    elif chosen:
        chart["found"], chart["tool"] = _exists("chart", chart_ref, chosen)
    else:
        chart["found"] = None
        chart["tool"] = f"{how}: name the version with --version"
    result: Dict[str, Any] = {"chart": chart}
    if image:
        tag = image_tag or chosen
        img: Dict[str, Any] = {"ref": image, "tag": tag}
        if tag:
            img["found"], img["tool"] = _exists("image", image, tag)
        else:
            img["found"], img["tool"] = None, "no chart version to take the tag from: pass --image-tag"
        result["image"] = img
    states = [part["found"] for part in result.values()]
    result["status"] = ("missing" if False in states else "unknown" if None in states else "ready")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--platform", choices=sorted(ci_pin.LIBRARIES))
    ap.add_argument("--check", action="store_true", help="ask the registry whether the chart and image are published")
    ap.add_argument("--chart-repo", help="OCI repository CI publishes the chart to, without the chart name")
    ap.add_argument("--image", help="image repository CI pushes to, without a tag")
    ap.add_argument("--channel", choices=("rc", "release"), default="release")
    ap.add_argument("--version", help="the chart version to check instead of the newest of the channel")
    ap.add_argument("--image-tag", help="the image tag to check (default: the chart version)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    info = facts(args.repo.resolve(), args.platform)
    code = 2 if info["missing"] else 0
    if args.check and not code:
        if not (info["chart"] and args.chart_repo):
            ap.error("--check needs a chart in the project and --chart-repo")
        info["published"] = published(info["chart"]["name"], args.chart_repo, args.image,
                                      args.channel, args.version, args.image_tag)
        code = {"ready": 0, "missing": 3}.get(info["published"]["status"], 1)

    if args.json:
        print(json.dumps(info, indent=2))
        return code
    chart, image, ci = info["chart"], info["image"], info["ci"] or {}
    print(f"shape        {info['shape']}")
    if chart:
        print(f"chart        {chart['name']} {chart['version']}  ({chart['dir']}"
              f"{', service library' if chart['serviceLibrary'] else ''})")
    print(f"image        {image['registry'] or '<registry>'}/{image['repository'] or '<repository>'}"
          f"{'  (library default)' if image['derived'] else ''}")
    if ci:
        print(f"ci           {ci['platform']} {ci['library']} {ci['pin']}{'  (mixed pins)' if ci['mixed'] else ''}")
        for key, value in ci["publish"].items():
            print(f"publish      {key}={value}")
    for problem in info["missing"]:
        print(f"missing      {problem}")
    if code == 2:
        print(">> Not onboarded: run `platform onboard` on the project first, then come back.")
    for kind, part in (info.get("published") or {}).items():
        if kind == "status":
            continue
        state = {True: "published", False: "NOT published", None: "not checked"}[part["found"]]
        print(f"{kind:<12} {part['ref']}:{part.get('version') or part.get('tag')}  {state}  ({part['tool']})")
    if code == 3:
        print(">> Not published yet: offer to run the project's publishing pipeline (app-readiness.md).")
    return code


if __name__ == "__main__":
    sys.exit(main())
