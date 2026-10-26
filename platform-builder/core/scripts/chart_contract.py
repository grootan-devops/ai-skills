#!/usr/bin/env python3
"""Read the chart contract a chart's own tpl-library dependency ships.

    python3 chart_contract.py <chart-dir> helm-docs-args

The vendored dependency, charts/<library chart>-<version>.tgz, is exactly the version the
chart renders with, so its docs/chart-contract.json holds the rules for this chart. It is
built with `helm dependency build` when missing. A version that ships no contract, or one
with a schema this skill does not know, stops here with what to upgrade.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

import libraries as libs  # noqa: E402

CONTRACT = "docs/chart-contract.json"


def vendored_package(chart_dir: Path) -> Path:
    """The packaged library dependency of `chart_dir`, building dependencies when absent."""
    names = list(libs.chart_dependencies().values())

    def found():
        return sorted(p for n in names for p in (chart_dir / "charts").glob(f"{n}-*.tgz"))

    if not found():
        if not shutil.which("helm"):
            raise SystemExit(f"helm is not installed, and {chart_dir}/charts holds no {' or '.join(names)} "
                             "package to read the chart contract from")
        build = subprocess.run(["helm", "dependency", "build", str(chart_dir)], capture_output=True, text=True)
        if build.returncode != 0:
            raise SystemExit(f"helm dependency build {chart_dir} failed: {build.stderr.strip()[:400]}")
    packages = found()
    if not packages:
        raise SystemExit(f"{chart_dir} depends on no {' or '.join(names)} chart")
    return packages[-1]


def load_contract(package: Path) -> Dict[str, Any]:
    with tarfile.open(package) as tar:
        member = next((m for m in tar.getmembers() if m.name.split("/", 1)[-1] == CONTRACT), None)
        if member is None:
            raise SystemExit(f"{package.name} ships no {CONTRACT}: this skill needs a tpl-library release "
                             "that ships it. Upgrade the chart's dependency to the latest release "
                             "(platform update).")
        contract = json.load(tar.extractfile(member))
    if contract.get("schema") not in libs.SUPPORTED_SCHEMAS:
        raise SystemExit(f"{package.name}: {CONTRACT} has schema {contract.get('schema')!r}, which this "
                         "skill does not know. Upgrade the skills to the latest release.")
    return contract


def main(argv) -> int:
    if len(argv) != 2 or argv[1] != "helm-docs-args":
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    for arg in load_contract(vendored_package(Path(argv[0]))).get("helmDocs", []):
        print(arg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
