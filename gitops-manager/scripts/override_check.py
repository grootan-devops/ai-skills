#!/usr/bin/env python3
"""Review a GitOps environment values file against the service chart it deploys.

    python3 override_check.py <env-values.yaml> <chart-dir> [-f values.<release>.yaml] [--strict]

An environment file overrides what differs per environment and nothing else. What that
is comes from the libraries, at the releases the charts pin:

  * the environment's argocd-gitops-tpl-library: its docs/environment-map.json lists the
    paths an environment may set (routing, image, resources, storage), the ones only a
    release of a multi-release chart may set, and the ones that belong in the chart;
  * the service chart's tpl-library: its root keys separate library settings from the
    application's own configuration, which an environment carries in full.

Everything outside the categories is reported: if production needs it, it belongs in the
chart's values.yaml. The check only reports; it never edits either file. Exit code is 0
unless --strict is given and something was reported.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import library  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

@dataclass
class Rules:
    """What an environment values file may override, read from the libraries' releases."""
    root_keys: Set[str]
    environment: Callable[[str], bool]
    release_shape: Callable[[str], bool]
    chart_only: List[Tuple[Callable[[str], bool], str]]

    @classmethod
    def load(cls, overrides: Dict[str, Any], root_keys: Iterable[str]) -> "Rules":
        containers = overrides.get("containers") or []
        environment = [p for paths in (overrides.get("environment") or {}).values() for p in paths]
        return cls(set(root_keys),
                   library.path_matcher(environment, containers),
                   library.path_matcher(overrides.get("releaseShape") or [], containers),
                   [(library.path_matcher(c.get("paths") or [], containers), str(c.get("hint") or ""))
                    for c in overrides.get("chartOnly") or []])


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


def review(env: Dict[str, Any], chart_values: Dict[str, Any], multi_release: bool,
           rules: Rules) -> List[str]:
    notes: List[str] = []
    for path, value in leaves(env):
        root = path.split(".", 1)[0]
        if root not in rules.root_keys:
            continue                                   # the application's own configuration
        if rules.environment(path):
            continue
        if multi_release and rules.release_shape(path):
            continue
        hint = next((h for match, h in rules.chart_only if match(path)), "not an environment setting")
        same = lookup(chart_values, path) == value
        notes.append(f"{path}: {hint}{' (same as the chart -- drop it)' if same else ''}")
    return notes


def environment_root(env_values: Path) -> Optional[Path]:
    """The root chart an environment values file belongs to: the nearest one up its path."""
    for parent in list(env_values.resolve().parents)[:4]:
        if library.dependency_version(parent, library.GITOPS_LIBRARY) is not None:
            return parent
    return None


def load_rules(env_root: Path, chart_dir: Path) -> Rules:
    return Rules.load(library.environment_map(env_root).get("overrides") or {},
                      library.service_root_keys(chart_dir))


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Review a GitOps environment override against its chart.")
    ap.add_argument("env_values", type=Path)
    ap.add_argument("chart_dir", type=Path, help="the service chart, its tpl-library dependency vendored or buildable")
    ap.add_argument("--root", type=Path, help="the environment's root chart (default: found above env_values)")
    ap.add_argument("-f", "--overlay", type=Path, action="append", default=[],
                    help="chart overlay this release uses (values.<release>.yaml)")
    ap.add_argument("--multi-release", action="store_true",
                    help="the chart runs several releases (detected when the chart has overlays)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    args = ap.parse_args(argv)

    env_root = args.root or environment_root(args.env_values)
    if env_root is None:
        ap.error(f"no root chart depending on {library.GITOPS_LIBRARY} above {args.env_values}: pass --root")
    rules = load_rules(env_root, args.chart_dir)
    chart_values = load(args.chart_dir / "values.yaml")
    for overlay in args.overlay:
        chart_values = merge(chart_values, load(overlay))
    overlays = [p for p in args.chart_dir.glob("values*.yaml") if p.name != "values.yaml"]
    env = load(args.env_values)
    multi = args.multi_release or bool(overlays) or any(k in env for k in ("mode", "subComponent"))

    notes = review(env, chart_values, multi, rules)
    for note in notes:
        print(f"WARN  {args.env_values.name}  {note}")
    print(f"{len(notes)} override(s) outside the environment categories" if notes
          else "only environment-level overrides")
    return 1 if (notes and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
