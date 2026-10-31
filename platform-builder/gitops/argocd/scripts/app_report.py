#!/usr/bin/env python3
"""What a developer keeps once an app is live: its URL, its Argo CD apps, and links to its files.

Run on the environment branch after the apps are merged, synced and healthy. For each app it
prints:

    url        where the app answers: Argo CD's live `externalURLs` when the Argo CD URL is
               given and reachable, else the hosts the app's own files set (`routes.*.host(s)`
               in its values file, `hostnames` / `rules[].host` in its manifests), with simple
               `{{ .Values.a.b }}` references resolved from that values file
    argocd     each Application and its page in the Argo CD UI
    values     the app's values file, or its manifests folder
    entry      the line in the root `values.yaml` (or `extras/values.yaml`) that declares it

then the root Application, and the `argocd app sync` commands to run later, in the order the
environment syncs. Application names are what the pinned library renders, never built by hand:
a Helm app's are the ones that disappear when its entry is left out of a copy of the branch, an
extras folder's is the one whose source path is the folder.

    python3 app_report.py <env-root> --gitops-url <url> --branch <b> [--argocd-url <url>]
        [--app <key> | --app <group>/<key> ...] [--extras-folder <dir> ...] [--root-app <name>]
        [--platform gitlab|github] [--json]

Nothing is written. The only remote call is a read-only `argocd app get`, and only with
--argocd-url.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "scripts"))

import argocd  # noqa: E402
import gitops_library as library  # noqa: E402
import review_links  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

_REF = re.compile(r"\{\{\s*\.Values\.([A-Za-z0-9_.]+)\s*\}\}")


def _applications(docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {(d.get("metadata") or {}).get("name"): d for d in docs if d.get("kind") == "Application"}


def _path(app: Dict[str, Any]) -> str:
    return str(((app.get("spec") or {}).get("source") or {}).get("path") or "")


def entry_line(text: str, keys: List[str]) -> Optional[int]:
    """1-based line of `apps.<keys...>` in a values file, or None."""
    want, depth, parent, child = ["apps", *keys], 0, -1, 0
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current = len(line) - len(line.lstrip())
        if depth and current <= parent:
            return None                           # left the block without finding the key
        if child is None:
            child = current                       # the indent of the block's own keys
        if current == child and re.match(rf"^{re.escape(want[depth])}:(\s|$)", stripped):
            depth, parent, child = depth + 1, current, None
            if depth == len(want):
                return no
    return None


def helm_applications(root: Path, keys: List[str]) -> List[Dict[str, Any]]:
    """The Applications this entry renders: present as the branch is, gone without the entry."""
    library.vendored(root, argocd.GITOPS_LIBRARY)
    now = _applications(library.render(root))
    values = yaml.safe_load((root / "values.yaml").read_text(encoding="utf-8")) or {}
    parent = values.get("apps") or {}
    for key in keys[:-1]:
        parent = parent.get(key) or {}
    if keys[-1] not in parent:
        raise SystemExit(f"apps.{'.'.join(keys)} is not in {root / 'values.yaml'}")
    del parent[keys[-1]]
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / root.name
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / "values.yaml").write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        without = _applications(library.render(copy))
    return [app for name, app in now.items() if name not in without]


def resolve(text: str, values: Dict[str, Any]) -> str:
    def lookup(m: re.Match) -> str:
        node: Any = values
        for part in m.group(1).split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return str(node) if isinstance(node, (str, int)) else m.group(0)
    return _REF.sub(lookup, text)


def values_hosts(values: Dict[str, Any]) -> List[str]:
    hosts: List[str] = []
    for route in (values.get("routes") or {}).values():
        if isinstance(route, dict) and route.get("enabled", True) is not False:
            hosts += [route["host"]] if route.get("host") else []
            hosts += [h for h in route.get("hosts") or [] if isinstance(h, str)]
    return [resolve(h, values) for h in hosts]


def manifest_hosts(folder: Path) -> List[str]:
    hosts: List[str] = []
    for path in sorted(folder.rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError:
            continue
        for doc in (d for d in docs if isinstance(d, dict)):
            spec = doc.get("spec") or {}
            if doc.get("kind") == "HTTPRoute":
                hosts += [h for h in spec.get("hostnames") or [] if isinstance(h, str)]
            elif doc.get("kind") == "Ingress":
                hosts += [r["host"] for r in spec.get("rules") or [] if isinstance(r, dict) and r.get("host")]
    return hosts


def live_urls(name: str, argocd_url: Optional[str]) -> Optional[List[str]]:
    """Argo CD's external URLs for an Application, or None when it can't be asked."""
    if not argocd_url or not shutil.which("argocd"):
        return None
    server = re.sub(r"^https?://", "", argocd_url).rstrip("/")
    try:
        out = subprocess.run(["argocd", "app", "get", f"argo-cd/{name}", "--server", server, "-o", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return list(((json.loads(out.stdout).get("status") or {}).get("summary") or {}).get("externalURLs") or [])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _urls(names: List[str], hosts: List[str], argocd_url: Optional[str]) -> Dict[str, Any]:
    live = [u for n in names for u in (live_urls(n, argocd_url) or [])]
    if live:
        return {"urls": list(dict.fromkeys(live)), "source": "Argo CD (live)"}
    found = [h if h.startswith("http") else f"https://{h}" for h in dict.fromkeys(hosts)]
    unresolved = any("{{" in h for h in found)
    return {"urls": found, "source": "the app's files" + (" (a template is left unresolved)" if unresolved else "")}


def root_name(root: Path, given: Optional[str]) -> str:
    if given:
        return given
    chart = yaml.safe_load((root / "Chart.yaml").read_text(encoding="utf-8"))["name"]
    values = yaml.safe_load((root / "values.yaml").read_text(encoding="utf-8")) or {}
    parts = [str(values.get("cluster") or ""), chart, str(values.get("environment") or ""), "root"]
    return "-".join(p for p in parts if p)    # the library's root Application guide: [cluster-]chart[-env]-root


def report(root: Path, apps: List[str], folders: List[str], url: str, branch: str, kind: str,
           argocd_url: Optional[str], root_app: Optional[str]) -> Dict[str, Any]:
    def ui(name: str) -> Optional[str]:
        return f"{argocd_url.rstrip('/')}/applications/argo-cd/{name}" if argocd_url else None

    root_text = (root / "values.yaml").read_text(encoding="utf-8")
    out: Dict[str, Any] = {"root": {"application": root_name(root, root_app)}, "apps": [], "extras": []}
    out["root"]["argocd"] = ui(out["root"]["application"])
    out["root"]["values"] = review_links.link(url, kind, branch, "values.yaml")
    rendered_root = _applications(library.render(root))
    parent = next((n for n, a in rendered_root.items() if _path(a) == "extras"), None)

    for spec in apps:
        keys = spec.split("/")
        names = sorted((a.get("metadata") or {}).get("name") for a in helm_applications(root, keys))
        rel = f"values/{'/'.join(keys)}.yaml"
        values = yaml.safe_load((root / rel).read_text(encoding="utf-8")) if (root / rel).exists() else {}
        out["apps"].append({
            "app": ".".join(keys), "applications": [{"name": n, "argocd": ui(n)} for n in names],
            **_urls(names, values_hosts(values or {}), argocd_url),
            "values": review_links.link(url, kind, branch, rel) if (root / rel).exists() else None,
            "entry": review_links.link(url, kind, branch, "values.yaml", entry_line(root_text, keys))})

    if folders:
        extras_text = (root / "extras" / "values.yaml").read_text(encoding="utf-8") \
            if (root / "extras" / "values.yaml").exists() else ""
        rendered = _applications(library.render(root / "extras"))
        for folder in folders:
            name = next((n for n, a in rendered.items() if _path(a).rstrip("/") == f"extras/manifests/{folder}"), None)
            if name is None:
                raise SystemExit(f"no Application renders extras/manifests/{folder}: is the folder disabled or empty?")
            key = folder.replace("/", "-")
            line = entry_line(extras_text, [key])
            out["extras"].append({
                "folder": folder, "applications": [{"name": name, "argocd": ui(name)}],
                **_urls([name], manifest_hosts(root / "extras" / "manifests" / folder), argocd_url),
                "manifests": review_links.link(url, kind, branch, f"extras/manifests/{folder}", tree=True),
                "entry": review_links.link(url, kind, branch, "extras/values.yaml", line) if line else None})

    order = [out["root"]["application"]] + ([parent] if out["extras"] and parent else [])
    order += [a["name"] for e in out["extras"] for a in e["applications"]]
    order += [a["name"] for e in out["apps"] for a in e["applications"]]
    out["sync"] = [f"argocd app sync argo-cd/{n}" for n in order]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="the environment branch's checkout, after the merge")
    ap.add_argument("--gitops-url", required=True)
    ap.add_argument("--branch", required=True, help="the environment branch")
    ap.add_argument("--argocd-url", help="Argo CD's URL, for UI links and the live app URLs")
    ap.add_argument("--app", action="append", default=[], help="apps key, or group/key")
    ap.add_argument("--extras-folder", action="append", default=[], help="folder under extras/manifests/")
    ap.add_argument("--root-app", help="the root Application's name, when it isn't [cluster-]<chart>[-env]-root")
    ap.add_argument("--platform", choices=(review_links.GITLAB, review_links.GITHUB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.app and not args.extras_folder:
        ap.error("name at least one --app or --extras-folder")
    base = review_links.web_base(args.gitops_url)
    kind = review_links.host_kind(base, args.platform) if base else None
    if not kind:
        print(f"{args.gitops_url}: the host name says neither gitlab nor github. Ask the user and pass --platform.")
        return 2

    result = report(args.root.resolve(), args.app, args.extras_folder, args.gitops_url, args.branch, kind,
                    args.argocd_url, args.root_app)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for item in result["apps"] + result["extras"]:
        print(f"{'app' if 'app' in item else 'extras':<11}{item.get('app') or item.get('folder')}")
        for url in item["urls"]:
            print(f"  url      {url}")
        print(f"  urls     from {item['source']}" if item["urls"] else "  url      none: no route exposes it")
        for app in item["applications"]:
            print(f"  argocd   {app['name']}" + (f"  {app['argocd']}" if app["argocd"] else ""))
        if item.get("values"):
            print(f"  values   {item['values']}")
        if item.get("manifests"):
            print(f"  files    {item['manifests']}")
        if item.get("entry"):
            print(f"  entry    {item['entry']}")
    root = result["root"]
    print(f"root       {root['application']}" + (f"  {root['argocd']}" if root["argocd"] else ""))
    print(f"  values   {root['values']}")
    print("sync later, in this order:")
    for command in result["sync"]:
        print(f"  {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
