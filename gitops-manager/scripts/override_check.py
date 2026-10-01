#!/usr/bin/env python3
"""Review a GitOps environment values file against the service chart it deploys.

    python3 override_check.py <env-values.yaml> <chart-dir> [-f values.<release>.yaml] [--strict]

An environment file overrides what differs per environment and nothing else. Accepted:

  * environment routing   global.routes.*, routes.<r>.host/hosts/httpRoute/ingress/ingressClass/tlsSecretName
  * image and pull        global.image.*, <any container>.image.* (jobs and cronjobs included)
  * resources and storage <any container>.resources.*, persistence.<p>.storageClass/size
  * application config    the chart's own top-level blocks (database, auth, URLs, keys), in full
  * release shape         mode, component, subComponent, service, probes, strategy,
                          routes.<r>.enabled, jobs/persistence enabled -- only when one chart
                          runs several releases

Everything else -- mounts, route paths, container env, probes on a single-release app,
releaseNameLength -- is reported: if production needs it, it belongs in the chart's
values.yaml. The categories follow argocd-gitops-tpl-library's documentation of what a
values file overrides. The check only reports; it never edits either file. Exit code is
0 unless --strict is given and something was reported.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

#: Root keys of the helm-tpl-library values contract. Anything else at the root of a chart's
#: values.yaml is that application's own configuration.
LIBRARY_ROOT_KEYS = {
    "global", "nameOverride", "fullnameOverride", "partOf", "component", "subComponent",
    "replicas", "revisionHistoryLimit", "restartPolicy", "strategy", "initContainers",
    "containers", "jobs", "cronjobs", "serviceAccount", "hostAliases", "pod", "scheduling",
    "service", "routes", "networkPolicy", "persistence", "mounts", "pdb", "autoscaling", "metrics",
}

_CONTAINER = r"(?:containers|initContainers|(?:jobs|cronjobs)\.[^.]+\.(?:containers|initContainers))\.[^.]+"
ACCEPTED = [
    ("environment routing", re.compile(r"^global\.routes(\.|$)")),
    ("environment routing", re.compile(r"^routes\.[^.]+\.(host|hosts|httpRoute|ingress|ingressClass|tlsSecretName)$")),
    ("image and pull", re.compile(r"^global\.image(\.|$)")),
    ("image and pull", re.compile(rf"^{_CONTAINER}\.image(\.|$)")),
    ("resources", re.compile(rf"^{_CONTAINER}\.resources(\.|$)")),
    ("storage", re.compile(r"^persistence\.[^.]+\.(storageClass|size)$")),
]
RELEASE_SHAPE = [
    re.compile(r"^(mode|component|subComponent)$"),
    re.compile(r"^service(\.|$)"),
    re.compile(rf"^{_CONTAINER}\.probes(\.|$)"),
    re.compile(r"^strategy(\.|$)"),
    re.compile(r"^routes\.[^.]+\.enabled$"),
    re.compile(r"^(jobs|cronjobs|persistence)\.[^.]+\.enabled$"),
]
HINTS = [
    (re.compile(r"^mounts(\.|$)"), "mounts belong in the chart"),
    (re.compile(r"^routes\.[^.]+\.(paths|matches)(\.|$)"), "route paths belong in the chart"),
    (re.compile(rf"^{_CONTAINER}\.(configmapEnvs|secretEnvs|env|additionalConfigmapEnvs|additionalSecretEnvs)(\.|$)"),
     "container environment belongs in the chart"),
    (re.compile(rf"^{_CONTAINER}\.probes(\.|$)"), "probe fixes belong in the chart"),
    (re.compile(r"^global\.releaseNameLength$"), "naming is fixed by the chart"),
]


def load(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return data if isinstance(data, dict) else {}


def merge(base: Any, over: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    out = dict(base)
    for key, val in over.items():
        if val is None:
            out.pop(key, None)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], val)
        else:
            out[key] = val
    return out


def leaves(node: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    if isinstance(node, dict) and node:
        for key, val in node.items():
            yield from leaves(val, f"{prefix}.{key}" if prefix else str(key))
    else:
        yield prefix, node


def lookup(node: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return ("<absent>",)
        node = node[part]
    return node


def review(env: Dict[str, Any], chart_values: Dict[str, Any], multi_release: bool) -> List[str]:
    notes: List[str] = []
    for path, value in leaves(env):
        root = path.split(".", 1)[0]
        if root not in LIBRARY_ROOT_KEYS:
            continue                                   # the application's own configuration
        if any(rx.match(path) for _, rx in ACCEPTED):
            continue
        if multi_release and any(rx.match(path) for rx in RELEASE_SHAPE):
            continue
        hint = next((h for rx, h in HINTS if rx.match(path)), "not an environment setting")
        same = lookup(chart_values, path) == value
        notes.append(f"{path}: {hint}{' (same as the chart -- drop it)' if same else ''}")
    return notes


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Review a GitOps environment override against its chart.")
    ap.add_argument("env_values", type=Path)
    ap.add_argument("chart_dir", type=Path)
    ap.add_argument("-f", "--overlay", type=Path, action="append", default=[],
                    help="chart overlay this release uses (values.<release>.yaml)")
    ap.add_argument("--multi-release", action="store_true",
                    help="the chart runs several releases (detected when the chart has overlays)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    args = ap.parse_args(argv)

    chart_values = load(args.chart_dir / "values.yaml")
    for overlay in args.overlay:
        chart_values = merge(chart_values, load(overlay))
    overlays = [p for p in args.chart_dir.glob("values*.yaml") if p.name != "values.yaml"]
    env = load(args.env_values)
    multi = args.multi_release or bool(overlays) or any(k in env for k in ("mode", "subComponent"))

    notes = review(env, chart_values, multi)
    for note in notes:
        print(f"WARN  {args.env_values.name}  {note}")
    print(f"{len(notes)} override(s) outside the environment categories" if notes
          else "only environment-level overrides")
    return 1 if (notes and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
