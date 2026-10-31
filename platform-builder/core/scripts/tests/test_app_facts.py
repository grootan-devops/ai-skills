import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_facts  # noqa: E402

GITLAB_CI = """include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/2.1.0/common/.gitlab-ci.yml'
variables:
  CHART_DIR: deploy/chart
  CHART_REGISTRY: registry.example.com
  CHART_REPOSITORY: helm
  CHART_REGISTRY_PASSWORD: never-read
  IMAGE_REPOSITORY:
    value: oneuptime/api
    description: where the image goes
"""
GITHUB_RELEASE = """on: push
jobs:
  release:
    uses: grootan-devops/github-ci-library/.github/workflows/release.yml@3.0.0
    with:
      image-registry: ghcr.io
      chart-repository: oneuptime/charts
      registry-token: never-read
"""
CHART = """apiVersion: v2
name: oneuptime-api
version: 1.4.0
appVersion: 1.4.0
dependencies:
  - name: tpl-library
    version: 0.0.0
    repository: oci://registry.example.com/helm
"""
VALUES = """global:
  partOf: oneuptime
  image:
    registry: registry.example.com
component: api
containers:
  main:
    image:
      repository: ""
"""


class FactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def onboarded(self, chart_at="deploy/chart"):
        self.write(".gitlab-ci.yml", GITLAB_CI)
        self.write("Dockerfile", "FROM scratch\n")
        self.write(f"{chart_at}/Chart.yaml", CHART)
        self.write(f"{chart_at}/values.yaml", VALUES)

    def test_chart_dir_follows_platform_builders_order(self):
        self.assertIsNone(app_facts.chart_dir(self.repo))
        for rel in ("charts", "helm", "chart", ""):
            self.write(f"{rel}/Chart.yaml" if rel else "Chart.yaml", CHART)
            self.assertEqual(app_facts.chart_dir(self.repo), self.repo / rel if rel else self.repo)
        self.write("deploy/chart/Chart.yaml", CHART)
        self.assertEqual(app_facts.chart_dir(self.repo, "deploy/chart"), (self.repo / "deploy/chart").resolve())

    def test_an_onboarded_gitlab_project(self):
        self.onboarded()
        info = app_facts.facts(self.repo)
        self.assertEqual(info["missing"], [])
        self.assertEqual(info["shape"], "service-with-chart")
        self.assertEqual((info["chart"]["name"], info["chart"]["version"]), ("oneuptime-api", "1.4.0"))
        self.assertTrue(info["chart"]["serviceLibrary"])
        self.assertEqual(info["image"], {"registry": "registry.example.com", "repository": "oneuptime/api",
                                         "derived": True})
        self.assertEqual(info["ci"]["pin"], "2.1.0")
        self.assertEqual(info["ci"]["publish"], {"CHART_DIR": "deploy/chart", "CHART_REGISTRY": "registry.example.com",
                                                 "CHART_REPOSITORY": "helm", "IMAGE_REPOSITORY": "oneuptime/api"})

    def test_an_explicit_image_repository_wins_over_the_library_default(self):
        self.onboarded()
        self.write("deploy/chart/values.yaml", VALUES.replace('repository: ""', "repository: team/api-server")
                   .replace("component: api", "component: api\nsubComponent: worker"))
        self.assertEqual(app_facts.facts(self.repo)["image"]["repository"], "team/api-server")
        self.write("deploy/chart/values.yaml", VALUES.replace("component: api", "component: api\nsubComponent: worker"))
        self.assertEqual(app_facts.facts(self.repo)["image"]["repository"], "oneuptime/api/worker")

    def test_github_settings_come_from_library_job_inputs_without_credentials(self):
        self.write(".github/workflows/release.yml", GITHUB_RELEASE)
        self.write("chart/Chart.yaml", CHART)
        info = app_facts.facts(self.repo)
        self.assertEqual(info["shape"], "chart-only")
        self.assertEqual(info["ci"]["pin"], "3.0.0")
        self.assertEqual(info["ci"]["publish"], {"image-registry": "ghcr.io", "chart-repository": "oneuptime/charts"})

    def test_a_project_without_a_pin_or_artifacts_is_handed_to_platform_builder(self):
        self.write(".gitlab-ci.yml", "stages: [test]\n")
        info = app_facts.facts(self.repo)
        self.assertIsNone(info["shape"])
        self.assertEqual(len(info["missing"]), 2)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(app_facts.main([str(self.repo)]), 2)
        self.assertIn("platform onboard", out.getvalue())


def which(*installed):
    return lambda tool: f"/usr/bin/{tool}" if tool in installed else None


class PublishedTests(unittest.TestCase):
    def test_channels_pick_the_newest_tag_they_allow(self):
        tags = ["1.3.0", "1.4.0-rc.9-1", "1.4.0-rc.10-2", "latest", "1.3.1"]
        self.assertEqual(app_facts.pick(tags, "release"), "1.3.1")
        self.assertEqual(app_facts.pick(tags, "rc"), "1.4.0-rc.10-2")
        self.assertIsNone(app_facts.pick(["latest"], "release"))

    def test_ready_when_the_listed_chart_and_its_image_tag_exist(self):
        with patch.object(app_facts.shutil, "which", which("crane")), \
                patch.object(app_facts, "_run", side_effect=[(0, "1.3.0\n1.4.0"), (0, "{}")]) as run:
            got = app_facts.published("oneuptime-api", "oci://registry.example.com/helm",
                                      "registry.example.com/oneuptime/api")
        self.assertEqual(got["status"], "ready")
        self.assertEqual((got["chart"]["version"], got["image"]["tag"]), ("1.4.0", "1.4.0"))
        self.assertEqual(run.call_args_list[0].args[0], ["crane", "ls", "registry.example.com/helm/oneuptime-api"])
        self.assertEqual(run.call_args_list[1].args[0], ["crane", "manifest", "registry.example.com/oneuptime/api:1.4.0"])

    def test_missing_before_the_first_publish(self):
        with patch.object(app_facts.shutil, "which", which("oras", "docker")), \
                patch.object(app_facts, "_run", side_effect=[(0, ""), ]):
            got = app_facts.published("oneuptime-api", "oci://registry.example.com/helm", None)
        self.assertEqual((got["status"], got["chart"]["found"]), ("missing", False))
        with patch.object(app_facts.shutil, "which", which("helm", "docker")), \
                patch.object(app_facts, "_run", side_effect=[(0, "{}"), (1, "manifest unknown")]):
            got = app_facts.published("oneuptime-api", "oci://registry.example.com/helm",
                                      "registry.example.com/oneuptime/api", version="1.4.0")
        self.assertEqual((got["status"], got["chart"]["found"], got["image"]["found"]), ("missing", True, False))

    def test_without_tools_or_a_version_it_could_not_check(self):
        with patch.object(app_facts.shutil, "which", which()):
            got = app_facts.published("oneuptime-api", "oci://registry.example.com/helm", None)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("--version", got["chart"]["tool"])

    def test_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "chart").mkdir()
            (repo / "chart" / "Chart.yaml").write_text(CHART, encoding="utf-8")
            (repo / "chart" / "values.yaml").write_text(VALUES, encoding="utf-8")
            (repo / ".gitlab-ci.yml").write_text(GITLAB_CI.replace("  CHART_DIR: deploy/chart\n", ""), encoding="utf-8")
            argv = [str(repo), "--check", "--chart-repo", "oci://registry.example.com/helm", "--json"]
            for status, code in (("ready", 0), ("missing", 3), ("unknown", 1)):
                with patch.object(app_facts, "published", return_value={"status": status}), \
                        redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(app_facts.main(argv), code)
                self.assertEqual(json.loads(out.getvalue())["published"]["status"], status)


if __name__ == "__main__":
    unittest.main()
