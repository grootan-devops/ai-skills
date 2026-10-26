"""The library releases a repository pins, and what the audit does with them."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402
import library_fixtures  # noqa: E402
import pins  # noqa: E402

_GITLAB_CI = """
include:
  - project: platform/gitlab-ci-library
    ref: 1.10.0
    file: common/.gitlab-ci.yml
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/1.9.0/chart/.gitlab-ci.yml'
  - remote: 'https://gitlab.example.com/platform/gitlab-ci-library/-/raw/1.10.0/image/.gitlab-ci.yml'
variables:
  CHART_DIR: deploy/chart
Image:Build:
  before_script:
    - !reference [.image-registry-login, before_script]
"""

_GITHUB_PR = """
on: pull_request
jobs:
  init:
    uses: grootan-devops/github-ci-library/.github/workflows/init.yml@1.7.0
  lint:
    uses: grootan-devops/github-ci-library/.github/workflows/lint.yml@1.7.0
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
"""


class PinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)

    def test_gitlab_includes_and_the_declared_chart(self):
        (self.repo / ".gitlab-ci.yml").write_text(_GITLAB_CI)
        chart = self.repo / "deploy" / "chart"
        chart.mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            "name: api\ndependencies:\n  - name: tpl-library\n    version: 1.5.0\n    repository: oci://r\n")
        found, cdir = pins.repo_pins(self.repo, "gitlab", {"helm-tpl-library": "tpl-library"})
        self.assertEqual(found, {"gitlab-ci-library": ["1.10.0", "1.9.0"], "helm-tpl-library": ["1.5.0"]})
        self.assertEqual(cdir, chart.resolve())

    def test_github_job_level_workflows_only(self):
        wf = self.repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "pr.yml").write_text(_GITHUB_PR)
        found, _ = pins.repo_pins(self.repo, "github", {})
        self.assertEqual(found, {"github-ci-library": ["1.7.0"]})

    def test_the_oldest_release_is_the_one_measured(self):
        self.assertEqual(pins.pick(["1.10.0", "1.9.0", "v1.9.5"]), "1.9.0")
        self.assertEqual(pins.pick(["dev", "1.2.0-rc.1"]), "dev")       # no release among them
        self.assertIsNone(pins.release_key("1.2.0-rc.1"))


class AuditLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".gitlab-ci.yml").write_text(
            "include:\n"
            "  - project: platform/gitlab-ci-library\n    ref: 1.10.0\n    file: common/.gitlab-ci.yml\n"
            "  - project: platform/gitlab-ci-library\n    ref: 1.9.0\n    file: chart/.gitlab-ci.yml\n"
            "variables:\n  PROJECT_CACHE_KEY: api\n")

    def run_audit(self, library: Path):
        out = io.StringIO()
        with redirect_stdout(out):
            code = audit.run(str(self.repo), "gitlab", as_json=True,
                             lib_overrides={"gitlab-ci-library": str(library)})
        return code, json.loads(out.getvalue())

    def test_a_release_without_the_map_stops_before_any_check(self):
        old = Path(self.temp.name) / "old-library"
        (old / "docs").mkdir(parents=True)
        (old / "README.md").write_text("# Library\n")
        code, report = self.run_audit(old)
        self.assertEqual(code, 2)
        self.assertIn("ships no docs/workflow-map.json", report["error"])
        self.assertNotIn("findings", report)

    def test_mixed_pins_are_reported_and_the_map_is_read(self):
        code, report = self.run_audit(library_fixtures.GITLAB_LIBRARY)
        cats = {f["category"]: f for f in report["findings"]}
        self.assertIn("Mixed Library Pins", cats)
        self.assertIn("1.10.0, 1.9.0", cats["Mixed Library Pins"]["message"])
        self.assertEqual(report["migrations"][0]["current"], "1.9.0")


if __name__ == "__main__":
    unittest.main()
