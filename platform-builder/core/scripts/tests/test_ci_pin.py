import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ci_pin  # noqa: E402

GITLAB_REMOTE = """include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/2.1.0/common/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/2.1.0/build/.node.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/other/tools/9.9.9/x.yml'
"""
GITLAB_PROJECT = """include:
  - project: 'devops/gitlab-ci-library'
    ref: '2.2.0'
    file: '/common/.gitlab-ci.yml'
  - remote: 'https://git.example.com/devops/gitlab-ci-library/-/raw/2.0.5/build/.node.gitlab-ci.yml'
job:
  script: !reference [.x, script]
"""
GITHUB_PR = """on: pull_request
jobs:
  init:
    uses: grootan-devops/github-ci-library/.github/workflows/init.yml@3.0.0
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
"""


class CiPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_gitlab_raw_remote_pin(self):
        self.write(".gitlab-ci.yml", GITLAB_REMOTE)
        self.assertEqual(ci_pin.detect(self.repo), "gitlab")
        info = ci_pin.pin(self.repo, "gitlab")
        self.assertEqual((info["pin"], info["refs"], info["mixed"]), ("2.1.0", ["2.1.0"], False))

    def test_gitlab_project_pin_and_several_releases_use_the_oldest(self):
        self.write(".gitlab-ci.yml", GITLAB_PROJECT)
        info = ci_pin.pin(self.repo, "gitlab")
        self.assertEqual(info["refs"], ["2.2.0", "2.0.5"])
        self.assertEqual(info["pin"], "2.0.5")
        self.assertTrue(info["mixed"])

    def test_github_job_level_uses_pin(self):
        self.write(".github/workflows/pr.yml", GITHUB_PR)
        self.assertEqual(ci_pin.detect(self.repo), "github")
        self.assertEqual(ci_pin.pin(self.repo, "github")["pin"], "3.0.0")

    def test_no_pin_hands_over_to_platform_builder(self):
        self.write(".gitlab-ci.yml", "stages: [build]\n")
        self.assertIsNone(ci_pin.pin(self.repo, "gitlab")["pin"])
        self.assertEqual(ci_pin.main([str(self.repo)]), 2)

    def test_both_or_neither_platform_is_asked(self):
        self.assertIsNone(ci_pin.detect(self.repo))
        self.write(".gitlab-ci.yml", GITLAB_REMOTE)
        self.write(".github/workflows/pr.yml", GITHUB_PR)
        self.assertIsNone(ci_pin.detect(self.repo))
        self.assertEqual(ci_pin.detect(self.repo, "github"), "github")

    def test_a_release_without_the_template_stops(self):
        self.write(".gitlab-ci.yml", GITLAB_REMOTE)
        info = ci_pin.pin(self.repo, "gitlab")
        checkout = self.repo / "cache" / f"{info['library']}-{info['pin']}"
        (checkout / "deploy" / "gitops").mkdir(parents=True)
        with self.assertRaises(SystemExit) as stop:
            ci_pin.fetch(info, "argocd", self.repo / "cache")
        self.assertIn("platform update", str(stop.exception))
        (checkout / "deploy" / "gitops" / ".argocd.gitlab-ci.yml").write_text("spec: {}\n", encoding="utf-8")
        self.assertTrue(ci_pin.fetch(info, "argocd", self.repo / "cache").is_file())


if __name__ == "__main__":
    unittest.main()
