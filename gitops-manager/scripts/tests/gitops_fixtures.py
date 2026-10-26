"""Vendored library packages and a stand-in renderer for the gitops-manager tests.

The scripts read their rules from the library packages an environment or a service chart
vendors, and take names from `helm template`. These fixtures build small packages of the
same shape, and `fake_render` stands in for the library's templates by following the naming
table its documentation publishes, so the tests run without helm or a library checkout.
"""
import io
import json
import tarfile
from pathlib import Path
from typing import Any, Dict, List

import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GITOPS = "argocd-gitops-tpl-library"
SERVICE = "tpl-library"


def package(chart_dir: Path, name: str, version: str, files: Dict[str, bytes]) -> Path:
    """Write charts/<name>-<version>.tgz into chart_dir, holding `files` under <name>/."""
    out = chart_dir / "charts" / f"{name}-{version}.tgz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for rel, data in {"Chart.yaml": f"name: {name}\nversion: {version}\n".encode(), **files}.items():
            info = tarfile.TarInfo(f"{name}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out


def depend(chart_dir: Path, chart_name: str, library: str, version: str, files: Dict[str, bytes]) -> None:
    """A chart named chart_name depending on library@version, with the package vendored."""
    (chart_dir / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: {chart_name}\nversion: 1.0.0\n"
        f"dependencies:\n  - name: {library}\n    version: {version}\n    repository: oci://registry.example.com\n",
        encoding="utf-8")
    package(chart_dir, library, version, files)


def environment_files(env_map: Dict[str, Any] = None) -> Dict[str, bytes]:
    data = env_map if env_map is not None else json.loads((FIXTURES / "environment-map.json").read_text())
    return {"docs/environment-map.json": json.dumps(data).encode()}


def service_files() -> Dict[str, bytes]:
    return {"values.schema.json": (FIXTURES / "tpl-library-values.schema.json").read_bytes(),
            "docs/chart-contract.json": (FIXTURES / "tpl-library-chart-contract.json").read_bytes()}


def fake_render(chart_dir: Path) -> List[Dict[str, Any]]:
    """Stand-in for the library's templates: Applications named as its docs' table says."""
    values = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8")) or {}
    chart = yaml.safe_load((chart_dir / "Chart.yaml").read_text(encoding="utf-8"))["name"]
    cluster, env = str(values.get("cluster") or ""), str(values.get("environment") or "")
    base_options = [str(o) for o in ((values.get("sync") or {}).get("options") or [])]
    docs: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], path: List[str], group_ns: str = "") -> None:
        for key, entry in node.items():
            if not isinstance(entry, dict) or entry.get("enabled") is False:
                continue
            if "chart" not in entry:
                walk(entry, path + [key], entry.get("namespace") or group_ns)
                continue
            segments = [s for i, s in enumerate(path + [key]) if not (i == 0 and s == chart and len(path) > 0)]
            name = "-".join([chart] + ([cluster] if cluster else []) + segments + ([env] if env else []))[:253]
            spec_chart = entry["chart"]
            values_file = chart_dir / "values" / ("/".join(path + [key]) + ".yaml")
            options = {o.split("=", 1)[0]: o for o in base_options}
            options.update({o.split("=", 1)[0]: o for o in (entry.get("sync") or {}).get("options") or []})
            docs.append({"kind": "Application", "metadata": {"name": name.rstrip("-")}, "spec": {
                "destination": {"namespace": entry.get("namespace") or group_ns or values.get("namespace")
                                or (f"{chart}-{env}" if env else chart)},
                "source": {"chart": spec_chart.get("name"), "targetRevision": spec_chart.get("version"),
                           "helm": {"releaseName": entry.get("releaseName") or spec_chart.get("name") or key,
                                    "values": values_file.read_text() if values_file.exists() else ""}},
                "syncPolicy": {"syncOptions": list(options.values())}}})

    walk(values.get("apps") or {}, [])
    return docs
