#!/usr/bin/env python3
"""Print every name tpl-library derives for one release of a chart.

    python3 names.py <chart_dir> [-f overlay.yaml ...] [--release NAME]

Resource, container, Service, Job, PVC and env ConfigMap names, the image repository and
the route hosts -- computed the way helm-tpl-library's templates/_name.tpl computes them,
so a rename, a mode split or a sibling URL can be checked before anything is rendered.
Values merge the Helm way (maps merge, null deletes). The release name defaults to the
chart name.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks_common as cc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("chart_dir")
    ap.add_argument("-f", "--values", action="append", default=[], help="overlay values file (repeatable)")
    ap.add_argument("--release", help="release name (default: the chart name)")
    args = ap.parse_args()

    chart = Path(args.chart_dir)
    vals = cc._load_yaml(chart / "values.yaml") or {}
    for extra in args.values:
        vals = cc._merge_values(vals, cc._load_yaml(Path(extra)) or {})
    m = re.search(r"^name:\s*(\S+)", (chart / "Chart.yaml").read_text(encoding="utf-8"), re.M)
    chart_name = m.group(1).strip("'\"") if m else chart.name
    release = args.release or chart_name

    glob_ = vals.get("global") or {}
    prefix = release[: int(glob_.get("releaseNameLength", 6) or 6)]
    comp, sub = vals.get("component") or "", vals.get("subComponent") or ""
    cname = f"{comp}-{sub}" if sub else comp
    resource = f"{prefix}-{cname}"

    print(f"release            {release}  (prefix '{prefix}', releaseNameLength {glob_.get('releaseNameLength', 6)})")
    print(f"component name     {cname}")
    print(f"resource name      {resource}")

    for group, init in (("containers", ""), ("initContainers", "init-")):
        for key, cont in (vals.get(group) or {}).items():
            explicit = (cont or {}).get("name") if isinstance(cont, dict) else ""
            budget = 63 - (len(init) + len(key) + 1)
            name = explicit or f"{init}{cname[:budget].rstrip('-')}-{key}"
            print(f"container          {name}  ({group}.{key})")
            if group == "containers":
                print(f"env ConfigMap      {resource}-{key}-env")

    service = vals.get("service")
    if isinstance(service, dict):
        for key in service:
            print(f"Service            {resource if key == 'default' else f'{resource}-{key}'}")
    for key in (vals.get("jobs") or {}):
        print(f"Job                {resource}-{key}")
    for key in (vals.get("persistence") or {}):
        print(f"PVC                {resource}-{key}")

    main_c = (vals.get("containers") or {}).get("main") or {}
    explicit_repo = ((main_c.get("image") or {}).get("repository")) if isinstance(main_c, dict) else ""
    part_of = vals.get("partOf") or glob_.get("partOf") or ""
    path = f"{comp}/{sub}" if comp and sub else (comp or sub)
    derived = f"{part_of}/{path}" if part_of and path else (path or chart_name)
    registry = (glob_.get("image") or {}).get("registry", "")
    print(f"image              {registry}/{explicit_repo or derived}"
          f"{'' if explicit_repo else '  (derived: repository is empty)'}")

    domain = ((glob_.get("routes") or {}).get("domain")) or "<global.routes.domain>"
    for key, route in (vals.get("routes") or {}).items():
        if not isinstance(route, dict) or route.get("enabled", True) is False:
            continue
        hosts = route.get("hosts") or ([route["host"]] if route.get("host") else [f"{comp}.{domain}"])
        for host in hosts:
            print(f"route host         {host}  (routes.{key})")

    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in [chart / "values.yaml", *[Path(v) for v in args.values]] if p.exists())
    for sibling in sorted(set(re.findall(r'siblingName"\s*\(merge\s*\(dict\s*"name"\s*"([^"]+)"', text))):
        print(f"sibling            {prefix}-{sibling}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
