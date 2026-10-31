#!/usr/bin/env python3
"""Is this an Argo CD GitOps repository built on argocd-gitops-tpl-library? Run once, first.

Every environment command starts from the GitOps repository link the user gives. Before any
other step reads or writes it, this checks the link is what the commands expect:

    reachable     the repository answers `git ls-remote`
    environments  at least one branch is an environment: its root Chart.yaml and its
                  extras/Chart.yaml depend on argocd-gitops-tpl-library at the same release,
                  both charts have the same name, and values.yaml parses with an `apps` map
    pattern       each environment branch is compared with its siblings, not with an
                  example: the library releases they pin, the top-level values keys most of
                  them set, the files most of them carry, and the `branch` value they record
    default       the default branch's README and the cluster label it records

Nothing is written. A URL is cloned bare into a temporary directory; a local clone is read
through its `origin/*` refs as last fetched (or its own branches when it is bare), so pass the
URL, or fetch first, to check what the remote holds now.

    python3 repo_check.py <gitops-url | clone-dir> [--json]

    exit 0 a GitOps repository that follows its pattern
         1 a GitOps repository with problems on some branches (show them; ask before going on)
         2 not reachable, or no branch is an argocd-gitops-tpl-library environment
         3 an empty repository: nothing to learn the pattern from
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "scripts"))

import argocd  # noqa: E402
import naming  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

_TIMEOUT = 120
#: A key or file most environment branches have is part of the repository's pattern.
_MAJORITY = 0.5


def _git(repo: Optional[Path], *args: str) -> Optional[str]:
    cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
    try:
        # A link that needs credentials the helper lacks fails instead of prompting.
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


class Repo:
    """Branches and files of a repository, read without a checkout."""

    def __init__(self, path: Path):
        self.path = path
        bare = (_git(path, "rev-parse", "--is-bare-repository") or "").strip() == "true"
        pattern = "refs/heads" if bare else "refs/remotes/origin"
        refs = (_git(path, "for-each-ref", "--format=%(refname)", pattern) or "").split()
        prefix = pattern + "/"
        self.refs = {r[len(prefix):]: r for r in refs if not r.endswith("/HEAD")}
        head = (_git(path, "symbolic-ref", "--quiet", "HEAD" if bare else "refs/remotes/origin/HEAD") or "").strip()
        self.default = head[len(prefix):] if head.startswith(prefix) else None
        if self.default not in self.refs:  # an empty repository's HEAD names a branch that has no commit
            self.default = None
        if not self.default:
            self.default = next((b for b in ("main", "master") if b in self.refs), None)

    def files(self, branch: str) -> List[str]:
        return (_git(self.path, "ls-tree", "-r", "--name-only", self.refs[branch]) or "").splitlines()

    def read(self, branch: str, rel: str) -> Optional[str]:
        return _git(self.path, "show", f"{self.refs[branch]}:{rel}")


def _yaml(text: Optional[str]) -> Any:
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return "unparsable"


def _dependency(chart: Any) -> Optional[str]:
    for dep in (chart or {}).get("dependencies") or [] if isinstance(chart, dict) else []:
        if isinstance(dep, dict) and dep.get("name") == argocd.GITOPS_LIBRARY:
            return str(dep.get("version") or "")
    return None


def branch_facts(repo: Repo, branch: str) -> Dict[str, Any]:
    """What one branch is: an environment of the library, or something else."""
    root, extras = _yaml(repo.read(branch, "Chart.yaml")), _yaml(repo.read(branch, "extras/Chart.yaml"))
    facts: Dict[str, Any] = {"branch": branch, "environment": False, "problems": []}
    pin = _dependency(root)
    if pin is None:
        facts["kind"] = "no Chart.yaml" if root is None else f"Chart.yaml does not depend on {argocd.GITOPS_LIBRARY}"
        return facts
    facts.update({"environment": True, "kind": "environment", "chart": root.get("name"), "pin": pin,
                  "files": sorted(repo.files(branch))})
    problems = facts["problems"]
    extras_pin = _dependency(extras)
    if extras is None:
        problems.append("extras/Chart.yaml is missing")
    elif extras_pin is None:
        problems.append(f"extras/Chart.yaml does not depend on {argocd.GITOPS_LIBRARY}")
    elif extras_pin != pin:
        problems.append(f"the root pins {pin} but extras/ pins {extras_pin}")
    if isinstance(extras, dict) and extras.get("name") != root.get("name"):
        problems.append(f"the root chart is {root.get('name')} but extras/ is {extras.get('name')}")
    values = _yaml(repo.read(branch, "values.yaml"))
    if values is None:
        problems.append("values.yaml is missing")
    elif not isinstance(values, dict):
        problems.append("values.yaml does not parse to a mapping")
    else:
        apps = values.get("apps")
        if apps is not None and not isinstance(apps, dict):
            problems.append("values.yaml `apps` is not a map")
        facts.update({"keys": sorted(values), "apps": sorted(apps) if isinstance(apps, dict) else [],
                      "environmentName": values.get("environment"), "recordedBranch": values.get("branch")})
        recorded = values.get("branch")
        if recorded and recorded != branch:
            problems.append(f"values.yaml records branch {recorded!r}, not {branch!r}")
    return facts


def compare(envs: List[Dict[str, Any]]) -> None:
    """Drift from the pattern the environment branches share, added to each branch's notes."""
    def common(key: str) -> set:
        counts = Counter(item for env in envs for item in env.get(key) or [])
        return {item for item, n in counts.items() if n / len(envs) > _MAJORITY}

    keys, files = common("keys"), common("files")
    pins = Counter(env["pin"] for env in envs)
    usual = pins.most_common(1)[0][0] if pins else None
    for env in envs:
        notes = env.setdefault("notes", [])
        if len(envs) > 1 and env["pin"] != usual:
            notes.append(f"pins {env['pin']}; most branches pin {usual}")
        missing_keys = sorted(keys - set(env.get("keys") or []))
        if missing_keys and len(envs) > 2:
            notes.append(f"values.yaml lacks keys most branches set: {', '.join(missing_keys)}")
        missing_files = sorted(f for f in files - set(env.get("files") or []) if not f.startswith(("values/", "extras/manifests/")))
        if missing_files and len(envs) > 2:
            notes.append(f"lacks files most branches carry: {', '.join(missing_files)}")


def check(repo: Repo, url: str = "") -> Dict[str, Any]:
    branches = [branch_facts(repo, b) for b in sorted(repo.refs)]
    envs = [b for b in branches if b["environment"]]
    if envs:
        compare(envs)
    readme = repo.read(repo.default, "README.md") if repo.default else None
    result: Dict[str, Any] = {
        "repository": url or str(repo.path), "defaultBranch": repo.default,
        "readme": readme is not None,
        "clusterLabel": naming.readme_label(readme) if readme else None,
        "pins": sorted({e["pin"] for e in envs}),
        "branches": [{k: v for k, v in b.items() if k not in ("files", "keys")} for b in branches],
    }
    if not branches:
        result["status"] = "empty"
    elif not envs:
        result["status"] = "not-gitops"
    else:
        result["status"] = "problems" if any(e["problems"] for e in envs) else "ok"
    return result


def open_repo(source: str, scratch: Path) -> Optional[Repo]:
    """A local clone as is, or a URL cloned bare into `scratch`; None when it cannot be read."""
    path = Path(source).expanduser()
    if path.is_dir():
        return Repo(path) if _git(path, "rev-parse", "--git-dir") is not None else None
    if _git(None, "ls-remote", "--heads", source) is None:
        return None
    target = scratch / "gitops.git"
    if _git(None, "clone", "--quiet", "--bare", source, str(target)) is None:
        return None
    return Repo(target)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="the GitOps repository URL, or a local clone of it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as td:
        repo = open_repo(args.source, Path(td))
        result = check(repo, args.source) if repo else {"repository": args.source, "status": "unreachable"}
    code = {"ok": 0, "problems": 1, "empty": 3}.get(result["status"], 2)
    if args.json:
        print(json.dumps(result, indent=2))
        return code
    if result["status"] == "unreachable":
        print(f"{args.source}: cannot be read (wrong link, or no access with the user's git credentials)")
        return code
    print(f"repository   {result['repository']}")
    print(f"default      {result['defaultBranch']}  (README {'found' if result['readme'] else 'missing'}"
          f"{', cluster label ' + result['clusterLabel'] if result['clusterLabel'] else ''})")
    print(f"library      {argocd.GITOPS_LIBRARY} {', '.join(result['pins']) or 'not used on any branch'}")
    for b in result["branches"]:
        if not b["environment"]:
            print(f"branch       {b['branch']}  not an environment: {b['kind']}")
            continue
        print(f"branch       {b['branch']}  {b['chart']} @ {b['pin']}, environment {b.get('environmentName')!r}, "
              f"{len(b.get('apps') or [])} app(s)")
        for problem in b["problems"]:
            print(f"  problem    {problem}")
        for note in b.get("notes") or []:
            print(f"  drift      {note}")
    print({
        "ok": ">> A GitOps repository that follows its own pattern: go on.",
        "problems": ">> Show the problems to the user and ask before going on; never use a branch with problems as a template.",
        "not-gitops": f">> No branch is an {argocd.GITOPS_LIBRARY} environment: this is not the GitOps repository the "
                      "commands work on. Ask the user for the right link.",
        "empty": ">> The repository is empty: there is no pattern to follow. Ask the user whether this is the right "
                 "link; bootstrapping a new GitOps repository is not supported yet.",
    }[result["status"]])
    return code


if __name__ == "__main__":
    sys.exit(main())
