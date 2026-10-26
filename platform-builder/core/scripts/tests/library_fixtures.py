"""Library releases the tests measure against.

The skill reads each library's data file (workflow map, chart contract) from the library at
the release a project pins. These fixtures are synthetic files of the same shape, so the
tests exercise the reading without depending on any real release.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checks_common  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GITLAB_LIBRARY = FIXTURES / "gitlab-ci-library"
GITHUB_LIBRARY = FIXTURES / "github-ci-library"
HELM_LIBRARY = FIXTURES / "helm-tpl-library"


def gitlab_map() -> dict:
    return json.loads((GITLAB_LIBRARY / "docs" / "workflow-map.json").read_text())


def github_map() -> dict:
    return json.loads((GITHUB_LIBRARY / "docs" / "workflow-map.json").read_text())


def chart_library(values_text: str = None) -> checks_common.ChartLibrary:
    """The fixture tpl-library, optionally with a test's own library values.yaml."""
    lib = checks_common.ChartLibrary.load(HELM_LIBRARY)
    if values_text is not None:
        lib = replace(lib, values=yaml.safe_load(values_text) or {}, values_text=values_text)
    return lib


def use_chart_library(values_text: str = None) -> None:
    checks_common.set_chart_library(chart_library(values_text))
