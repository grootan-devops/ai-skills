#!/usr/bin/env python3
"""Check that every tpl.resource.siblingName target is a release some chart renders.

    python3 verify_siblings.py <chart_dir> [<chart_dir> ...]

A sibling default names another release of the product as `<component>[-<subComponent>]`.
After a rename or a mode split (an API release renamed from `backend` to `queue`), defaults in
the other charts keep pointing at a Service that no longer exists. Pass every chart of the
product; each chart's release overlays (values.<release>.yaml) are read automatically.
Exit code 1 when a reference does not resolve.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks_common as cc  # noqa: E402

_SIBLING = re.compile(r'siblingName"\s*\(merge\s*\(dict\s*"name"\s*"([^"]+)"')


def rendered_names(chart: Path) -> Set[str]:
    names: Set[str] = set()
    for _fname, vals, _raw in cc._variants(chart):
        comp, sub = vals.get("component") or "", vals.get("subComponent") or ""
        base = f"{comp}-{sub}" if sub else comp
        if not base:
            continue
        names.add(base)
        service = vals.get("service")
        if isinstance(service, dict):
            names |= {f"{base}-{key}" for key in service if key != "default"}
        persistence = vals.get("persistence")
        if isinstance(persistence, dict):
            names |= {f"{base}-{key}" for key in persistence}   # PVC claims tpl.pvc creates
    return names


def references(chart: Path) -> List[Tuple[str, str]]:
    files = [chart / "values.yaml", *cc._overlay_files(chart)]
    tdir = chart / "templates"
    if tdir.is_dir():
        files += sorted(list(tdir.glob("*.yaml")) + list(tdir.glob("*.tpl")))
    refs: List[Tuple[str, str]] = []
    for f in files:
        if f.exists():
            refs += [(f.name, n) for n in _SIBLING.findall(f.read_text(encoding="utf-8", errors="ignore"))]
    return refs


def main(argv: List[str]) -> int:
    charts = [Path(a) for a in argv]
    if not charts:
        print(__doc__)
        return 2
    known: Dict[str, Path] = {}
    for chart in charts:
        for name in rendered_names(chart):
            known.setdefault(name, chart)
    bad = 0
    for chart in charts:
        for fname, name in references(chart):
            if name not in known:
                bad += 1
                print(f"UNRESOLVED  {chart}/{fname}: sibling '{name}' -- no scanned chart renders it")
    print(f"known releases: {', '.join(sorted(known)) or 'none'}")
    print("all sibling references resolve" if not bad else f"{bad} unresolved sibling reference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
