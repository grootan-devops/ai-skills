#!/usr/bin/env python3
"""The names a new environment gets, from the GitOps repository itself.

The cluster label is the one the repository's default-branch README records, else the
repository's own name (`.../edge-east.git` -> `edge-east`), else the user is asked. It prefixes
the chart name (`<label>-<product>`); the library's optional `cluster` value stays unset, so
generated names carry the label once. The README format is the library's (its root Application
guide); this only reads the two forms that guide shows. Existing environments keep the names
their Chart.yaml and values.yaml already have: this is for new ones.

    python3 naming.py --repo-url <url> [--readme README.md] --product <p> [--env <e>] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional

#: Repository names that say what the repository is, not which cluster it serves.
GENERIC_NAMES = {"gitops", "argocd", "argo-cd", "argo", "gitops-repo", "deploy", "deployments",
                 "environments", "envs", "infra", "infrastructure", "k8s", "kubernetes", "helm",
                 "manifests", "charts", "platform", "cluster", "clusters"}
_DNS = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_HEADING = re.compile(r"^#\s+(.+?)\s*\(cluster name\)\s*$", re.I)
_LABEL_LINE = re.compile(r"^[-*]?\s*cluster(?: label)?\s*:\s*(.+?)\s*$", re.I)
_DESTINATION_LINE = re.compile(r"^[-*]?\s*argo\s?cd (?:server|cluster) name\s*:\s*(.+?)\s*$", re.I)


def dns_safe(value: str) -> str:
    """Lowercase, runs of anything else turned into one hyphen, trimmed."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _value(raw: str) -> Optional[str]:
    raw = raw.strip().strip("`\"'")
    return None if not raw or raw.startswith("<") else raw


def readme_label(text: str) -> Optional[str]:
    """The cluster label the README records, DNS-safe, or None."""
    for line in text.splitlines():
        m = _HEADING.match(line.strip()) or _LABEL_LINE.match(line.strip())
        if m and _value(m.group(1)):
            return dns_safe(_value(m.group(1)))
    return None


def readme_destination(text: str) -> Optional[str]:
    """The Argo CD cluster name the README records, exactly as written, or None."""
    for line in text.splitlines():
        m = _DESTINATION_LINE.match(line.strip())
        if m and _value(m.group(1)):
            return _value(m.group(1))
    return None


def repo_label(url: str) -> Optional[str]:
    """The repository name as a label, or None when it is generic or not DNS-safe."""
    name = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    return name if _DNS.match(name) and name not in GENERIC_NAMES else None


def derive(repo_url: str, readme: Optional[str], product: str, env: str = "") -> Dict[str, Optional[str]]:
    """Every name a new `<product>/<env>` environment gets; `label` is None when it must be asked."""
    label, source = (readme_label(readme), "README") if readme and readme_label(readme) else (repo_label(repo_url), "repository name")
    if not label:
        source = None
    product = dns_safe(product)
    chart = f"{label}-{product}" if label else None
    env = dns_safe(env) if env else ""
    return {
        "label": label,
        "labelSource": source,
        "destination": readme_destination(readme) if readme else None,
        "chart": chart,
        "branch": f"{product}/{env}" if env else product,
        "namespace": f"{product}-{env}" if env else product,
        "rootApplication": (f"{chart}-{env}-root" if env else f"{chart}-root") if chart else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo-url", required=True)
    ap.add_argument("--readme", type=Path, help="the GitOps repository's default-branch README.md")
    ap.add_argument("--product", required=True)
    ap.add_argument("--env", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    readme = args.readme.read_text(encoding="utf-8") if args.readme and args.readme.is_file() else None
    names = derive(args.repo_url, readme, args.product, args.env)
    if args.json:
        print(json.dumps(names, indent=2))
    else:
        for key, value in names.items():
            print(f"{key}: {value if value is not None else '<ask>'}")
    if not names["label"]:
        print("no cluster label: the README records none and the repository name is generic; ask the user",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
