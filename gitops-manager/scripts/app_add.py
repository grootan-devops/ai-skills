#!/usr/bin/env python3
"""Add a service to a GitOps environment built on argocd-gitops-tpl-library.

    python3 app_add.py <gitops-root> --app <key> [--group <group>]
        (--chart-repo URL --chart-name NAME --chart-version VERSION --service-values <chart dir | values.yaml>
         | --chart-path PATH [--values-file F ...])
        [-f <chart overlay>] [--namespace NS] [--release-name NAME] [--sync auto|manual]
        [--dry-run] [--verify]

Writes the two pieces an environment needs for one service:

  * the `apps.<key>` entry (or `apps.<group>.<key>` for one release of a multi-release chart)
    in the root chart's values.yaml, and
  * for a registry chart, its values file -- values/<key>.yaml (or values/<group>/<key>.yaml) --
    which the library inlines into the service's Argo CD Application.

The values file is scaffolded from the service chart: environment settings as `<ask: ...>`
placeholders, the chart's application blocks in full, and -- for a group release -- the chart
overlay's release shape. Every placeholder must be completed from the user's answers. An
existing entry or file is never overwritten; nothing is committed, pushed or synced.
"""

from __future__ import annotations

import argparse
import copy
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import override_check as oc  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RELEASE_SHAPE_KEYS = ("mode", "component", "subComponent", "service", "strategy")


def ask(what: str) -> str:
    return f"<ask: {what}>"


def kebab(name: str) -> str:
    """util.kebabcase from the library."""
    step = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    step = re.sub(r"([A-Z])([A-Z][a-z])", r"\1-\2", step)
    return step.lower()


def chart_name(root: Path) -> str:
    chart = root / "Chart.yaml"
    if not chart.exists():
        raise SystemExit(f"{root} is not a chart: no Chart.yaml")
    m = re.search(r"^name:\s*['\"]?([^'\"\s#]+)", chart.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"no chart name in {chart}")
    return m.group(1)


def resolve(args: argparse.Namespace, root: Path, values: Dict[str, Any]) -> Dict[str, str]:
    """Names and paths the library derives for this entry (templates/argocd/*.tpl)."""
    env = str(values.get("environment") or "")
    cluster = str(values.get("cluster") or "")          # library >= 1.2.0 names include it
    cname = chart_name(root)
    app, group = args.app, args.group
    segments = ([kebab(group)] if group and group != cname else []) + [kebab(app)]
    parts = [cname] + ([cluster] if cluster else []) + segments + ([env] if env else [])
    apps = values.get("apps") if isinstance(values.get("apps"), dict) else {}
    group_ns = (apps.get(group) or {}).get("namespace") if group else None
    namespace = args.namespace or group_ns or values.get("namespace") or (f"{cname}-{env}" if env else cname)
    return {
        "application": "-".join(parts)[:253].rstrip("-"),
        "values_path": "" if args.chart_path else
                       (f"values/{group}/{kebab(app)}.yaml" if group else f"values/{kebab(app)}.yaml"),
        "namespace": str(namespace),
        "release": str(args.release_name or (app if args.chart_path else args.chart_name)),
        "yq": f".apps.{group}.{app}" if group else f".apps.{app}",
        "entry": f"apps.{group}.{app}" if group else f"apps.{app}",
    }


def _scalar(val: str) -> str:
    """Write a string plainly unless YAML would read it back as something else."""
    try:
        loaded = yaml.safe_load(val)
    except yaml.YAMLError:
        loaded = None
    if isinstance(loaded, str) and loaded == val:
        return val
    return yaml.safe_dump(val, default_style='"').strip()


def entry_lines(args: argparse.Namespace, indent: int) -> List[str]:
    p = " " * indent
    out = [f"{p}{args.app}:", f"{p}  chart:"]
    if args.chart_path:
        out.append(f"{p}    path: {_scalar(args.chart_path)}")
        if args.values_file:
            out.append(f"{p}    valuesFiles:")
            out += [f"{p}      - {_scalar(f)}" for f in args.values_file]
    else:
        out += [f"{p}    repoURL: {_scalar(args.chart_repo)}",
                f"{p}    version: {_scalar(args.chart_version)}",
                f"{p}    name: {_scalar(args.chart_name)}"]
    if args.release_name:
        out.append(f"{p}  releaseName: {_scalar(args.release_name)}")
    if args.namespace:
        out.append(f"{p}  namespace: {_scalar(args.namespace)}")
    out.append(f"{p}  sync:")
    if args.sync == "auto":
        out += [f"{p}    automated:", f"{p}      prune: true", f"{p}      selfHeal: true"]
    out += [f"{p}    options: []", f"{p}    retry: {{}}"]
    return out


def _is_content(line: str) -> bool:
    return bool(line.strip()) and not line.strip().startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_end(lines: List[str], start: int, parent_indent: int) -> int:
    """Index just after the last content line of the block opened at `start`."""
    last = start
    for i in range(start + 1, len(lines)):
        if _is_content(lines[i]):
            if _indent(lines[i]) <= parent_indent:
                break
            last = i
    return last + 1


def insert_entry(text: str, args: argparse.Namespace) -> str:
    """values.yaml text with the new apps entry added; every other line untouched."""
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines) if re.match(r"^apps:\s*(\{\s*\})?\s*(#.*)?$", ln)), None)
    block = entry_lines(args, 4 if args.group else 2)
    if args.group:
        block = [f"  {args.group}:"] + block

    if idx is None:
        new = lines + ([""] if lines and lines[-1].strip() else []) + ["apps:"] + block
    elif re.match(r"^apps:\s*\{\s*\}", lines[idx]):
        new = lines[:idx] + ["apps:"] + block + lines[idx + 1:]
    else:
        apps_end = _block_end(lines, idx, 0)
        group_idx = None
        if args.group:
            group_idx = next((i for i in range(idx + 1, apps_end)
                              if re.match(rf"^  {re.escape(args.group)}:\s*(#.*)?$", lines[i])), None)
        if group_idx is not None:
            group_end = _block_end(lines, group_idx, 2)
            new = lines[:group_end] + [""] + entry_lines(args, 4) + lines[group_end:]
        else:
            new = lines[:apps_end] + [""] + block + lines[apps_end:]
    return "\n".join(new) + ("\n" if text.endswith("\n") or not text else "")


def expected_entry(args: argparse.Namespace) -> Dict[str, Any]:
    if args.chart_path:
        chart: Dict[str, Any] = {"path": args.chart_path}
        if args.values_file:
            chart["valuesFiles"] = list(args.values_file)
    else:
        chart = {"repoURL": args.chart_repo, "version": args.chart_version, "name": args.chart_name}
    entry: Dict[str, Any] = {"chart": chart}
    if args.release_name:
        entry["releaseName"] = args.release_name
    if args.namespace:
        entry["namespace"] = args.namespace
    sync: Dict[str, Any] = {"automated": {"prune": True, "selfHeal": True}} if args.sync == "auto" else {}
    sync.update({"options": [], "retry": {}})
    entry["sync"] = sync
    return entry


def verify_insert(old_text: str, new_text: str, args: argparse.Namespace) -> Optional[str]:
    """None when the only change is the expected new entry; otherwise why not."""
    try:
        old = yaml.safe_load(old_text) or {}
        new = yaml.safe_load(new_text) or {}
    except yaml.YAMLError as exc:
        return f"values.yaml would not parse: {exc}"
    if {k: v for k, v in new.items() if k != "apps"} != {k: v for k, v in old.items() if k != "apps"}:
        return "keys outside apps changed"
    old_apps = copy.deepcopy(old.get("apps") or {})
    new_apps = copy.deepcopy(new.get("apps") or {})
    if args.group:
        grp = new_apps.get(args.group) if isinstance(new_apps.get(args.group), dict) else {}
        got = grp.pop(args.app, None)
        if not grp and args.group not in old_apps:
            new_apps.pop(args.group, None)
    else:
        got = new_apps.pop(args.app, None)
    if new_apps != old_apps:
        return "existing apps entries changed"
    if got != expected_entry(args):
        return f"new entry parsed as {got!r}"
    return None


def scaffold(service: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """The environment values file for one release, from the service chart's values."""
    merged = oc.merge(copy.deepcopy(service), overlay) if overlay else copy.deepcopy(service)
    out: Dict[str, Any] = {}
    for key in RELEASE_SHAPE_KEYS:                 # release shape, only as the overlay sets it
        if key in overlay:
            out[key] = copy.deepcopy(overlay[key])

    routes = merged.get("routes") if isinstance(merged.get("routes"), dict) else {}
    enabled = sorted(k for k, r in routes.items() if isinstance(r, dict) and r.get("enabled", True) is not False)
    if enabled:
        out["global"] = {"routes": {"domain": ask("environment domain"),
                                    "ingressClass": ask("ingress class"),
                                    "tlsSecretName": ask("TLS secret name")}}
        out["routes"] = {k: {"host": ask(f"host for routes.{k} in this environment")} for k in enabled}
    for key, route in (overlay.get("routes") or {}).items():
        if isinstance(route, dict) and "enabled" in route:
            out.setdefault("routes", {}).setdefault(key, {})["enabled"] = route["enabled"]

    containers = merged.get("containers") if isinstance(merged.get("containers"), dict) else {}
    if containers:
        out["containers"] = {}
        for cname in containers:
            out["containers"][cname] = {
                "image": {"repository": ask(f"image repository for {cname} in this environment"),
                          "tag": ask('image tag, or "" to follow the chart appVersion')},
                "resources": {"requests": {"memory": ask("memory request")},
                              "limits": {"memory": ask("memory limit")}},
            }
            probes = ((overlay.get("containers") or {}).get(cname) or {}).get("probes")
            if probes is not None:
                out["containers"][cname]["probes"] = copy.deepcopy(probes)
    for block in ("jobs", "cronjobs", "persistence"):
        for name, item in (overlay.get(block) or {}).items():
            if isinstance(item, dict) and "enabled" in item:
                out.setdefault(block, {}).setdefault(name, {})["enabled"] = item["enabled"]

    for key, val in merged.items():                # application blocks, carried in full
        if key not in oc.LIBRARY_ROOT_KEYS and key not in RELEASE_SHAPE_KEYS and key not in out:
            out[key] = _ask_for_secrets(copy.deepcopy(val), key)
    return out


_SECRET_KEY = re.compile(r"(password|passwd|secret|token|api_?key|private_?key|encryption_?key)$", re.I)


def _ask_for_secrets(node: Any, path: str) -> Any:
    """Chart defaults for credentials are never carried into an environment file."""
    if isinstance(node, dict):
        return {k: (ask(f"{path}.{k} -- a credential: use the environment's secret mechanism")
                    if _SECRET_KEY.search(str(k)) and not isinstance(v, (dict, list)) else _ask_for_secrets(v, f"{path}.{k}"))
                for k, v in node.items()}
    return node


def load_service(path: Path) -> Dict[str, Any]:
    target = path / "values.yaml" if path.is_dir() else path
    if not target.exists():
        raise SystemExit(f"service values not found: {target}")
    return oc.load(target)


def render_check(root: Path, info: Dict[str, str], args: argparse.Namespace) -> List[str]:
    """helm template the root chart and check the new Application (needs a vendored library)."""
    if not shutil.which("helm") or not list((root / "charts").glob("*.tgz")):
        return [f"render       run `helm dependency build {root}` then `helm template {root}` "
                f"and check Application {info['application']}"]
    out = subprocess.run(["helm", "template", str(root)], capture_output=True, text=True)
    if out.returncode != 0:
        return [f"render       helm template failed: {out.stderr.strip()[:400]}"]
    app = next((d for d in yaml.safe_load_all(out.stdout)
                if isinstance(d, dict) and d.get("kind") == "Application"
                and (d.get("metadata") or {}).get("name") == info["application"]), None)
    if not app:
        return [f"render       no Application named {info['application']} was rendered"]
    spec = app.get("spec") or {}
    src, dest = spec.get("source") or {}, spec.get("destination") or {}
    helm = src.get("helm") or {}
    problems = []
    if not args.chart_path and (src.get("chart") != args.chart_name
                                or str(src.get("targetRevision")) != str(args.chart_version)):
        problems.append(f"chart rendered as {src.get('chart')}@{src.get('targetRevision')}")
    if dest.get("namespace") != info["namespace"]:
        problems.append(f"namespace rendered as {dest.get('namespace')}")
    if helm.get("releaseName") != info["release"]:
        problems.append(f"releaseName rendered as {helm.get('releaseName')}")
    if not args.chart_path and not str(helm.get("values") or "").strip():
        problems.append(f"helm.values is empty: {info['values_path']} was not picked up")
    return [f"render       {p}" for p in problems] or [f"render       Application {info['application']} OK"]


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Add a service to an argocd-gitops-tpl-library environment.")
    ap.add_argument("root", type=Path, help="the environment's root chart directory")
    ap.add_argument("--app", required=True, help="apps key (lowercase kebab-case)")
    ap.add_argument("--group", help="group key when this is one release of a multi-release chart")
    ap.add_argument("--chart-repo")
    ap.add_argument("--chart-name")
    ap.add_argument("--chart-version")
    ap.add_argument("--chart-path", help="chart inside the GitOps repository instead of a registry chart")
    ap.add_argument("--values-file", action="append", default=[], help="values file of a --chart-path chart")
    ap.add_argument("--service-values", type=Path, help="service chart directory or its values.yaml")
    ap.add_argument("-f", "--overlay", type=Path, help="chart overlay of this release (values.<release>.yaml)")
    ap.add_argument("--namespace")
    ap.add_argument("--release-name")
    ap.add_argument("--sync", choices=("auto", "manual"), default="auto")
    ap.add_argument("--dry-run", action="store_true", help="print what would change and write nothing")
    ap.add_argument("--verify", action="store_true", help="render the root chart and check the new Application")
    args = ap.parse_args(argv)

    for label, val in (("--app", args.app), ("--group", args.group)):
        if val is not None and not NAME_RE.match(val):
            ap.error(f"{label} '{val}' must be lowercase kebab-case")
    if args.chart_path:
        if args.chart_repo or args.chart_version or args.chart_name:
            ap.error("use either --chart-path or --chart-repo/--chart-name/--chart-version")
    else:
        if not (args.chart_repo and args.chart_name and args.chart_version):
            ap.error("--chart-repo, --chart-name and --chart-version are required (or --chart-path)")
        if not args.service_values:
            ap.error("--service-values is required to scaffold the values file (the chart directory or a pulled copy)")

    root = args.root
    values_file = root / "values.yaml"
    text = values_file.read_text(encoding="utf-8")
    values = yaml.safe_load(text) or {}
    apps = values.get("apps") if isinstance(values.get("apps"), dict) else {}
    if args.group:
        group = apps.get(args.group)
        if isinstance(group, dict) and "chart" in group:
            raise SystemExit(f"apps.{args.group} is a single application, not a group")
        if isinstance(group, dict) and args.app in group:
            raise SystemExit(f"apps.{args.group}.{args.app} already exists; edit it instead")
    elif args.app in apps:
        raise SystemExit(f"apps.{args.app} already exists; edit it instead")

    info = resolve(args, root, values)
    target = root / info["values_path"] if info["values_path"] else None
    if target is not None and target.exists():
        raise SystemExit(f"{info['values_path']} already exists; it is never overwritten")

    new_text = insert_entry(text, args)
    problem = verify_insert(text, new_text, args)
    if problem:
        raise SystemExit(f"refusing to edit values.yaml: {problem}")

    body, placeholders, notes = "", 0, []
    if target is not None:
        service = load_service(args.service_values)
        overlay = oc.load(args.overlay) if args.overlay else {}
        env_values = scaffold(service, overlay)
        body = yaml.safe_dump(env_values, sort_keys=False, default_flow_style=False, width=1000)
        placeholders = body.count("<ask:")
        chart_values = oc.merge(service, overlay) if overlay else service
        notes = oc.review(yaml.safe_load(body) or {}, chart_values, bool(args.group or overlay))

    options = [str(o) for o in ((values.get("sync") or {}).get("options") or [])]
    print(f"entry        {info['entry']}  (in {values_file})")
    if target is not None:
        print(f"values file  {info['values_path']}  ({placeholders} placeholder(s) to fill from the user's answers)")
    else:
        print("values file  none: a --chart-path chart reads values.yaml and its valuesFiles from its own directory")
    print(f"application  {info['application']}")
    print(f"namespace    {info['namespace']}"
          + ("  (must already exist: CreateNamespace=false)" if "CreateNamespace=false" in options else ""))
    print(f"releaseName  {info['release']}")
    print(f"CI yq path   {info['yq']}")
    for note in notes:
        print(f"check        {note}")

    if args.dry_run:
        new_group = bool(args.group) and not isinstance(apps.get(args.group), dict)
        print("\n--- values.yaml entry ---")
        print("\n".join(([f"  {args.group}:"] if new_group else []) + entry_lines(args, 4 if args.group else 2)))
        if body:
            print(f"--- {info['values_path']} ---\n{body}")
        print("dry run: nothing written")
        return 0

    values_file.write_text(new_text, encoding="utf-8")
    written = [str(values_file)]
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(str(target))
    print(f"wrote        {', '.join(written)}")
    if args.verify:
        for line in render_check(root, info, args):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
