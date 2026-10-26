import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_PLATFORMS = Path(__file__).resolve().parents[3] / "platforms"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import library_fixtures  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load("audit_terraform_test", _SCRIPTS / "audit.py")
gitlab_ci_checks = _load("gitlab_ci_checks_terraform_test", _PLATFORMS / "gitlab" / "ci_checks.py")
github_ci_checks = _load("github_ci_checks_terraform_test", _PLATFORMS / "github" / "ci_checks.py")

_LIB = "https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/1.10.0"
_GITLAB_TERRAFORM = f"""
include:
  - remote: '{_LIB}/common/.gitlab-ci.yml'
  - remote: '{_LIB}/terraform/.gitlab-ci.yml'
  - remote: '{_LIB}/secret-scanning/.gitlab-ci.yml'
  - remote: '{_LIB}/release/.gitlab-ci.yml'

variables:
  PROJECT_CACHE_KEY: terraform
  RELEASE_VERSION: 0.3.0
  TF_STATE_NAME: network
  WORKFLOW:
    value: "full-pipeline"
    options:
{{options}}
"""


class TerraformShapeTests(unittest.TestCase):
    def shape(self, *files: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for f in files:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_text("", encoding="utf-8")
            return audit.detect_shape(root)

    def test_root_terraform_is_terraform(self):
        self.assertEqual(self.shape("main.tf"), "terraform")
        self.assertEqual(self.shape("versions.tf", "variables.tf"), "terraform")

    def test_terraform_beside_an_artifact_keeps_the_artifact_shape(self):
        self.assertEqual(self.shape("main.tf", "Dockerfile"), "image-only")
        self.assertEqual(self.shape("main.tf", "Chart.yaml"), "chart-only")
        self.assertEqual(self.shape("main.tf", "package.json"), "app-no-artifact")

    def test_terraform_only_in_a_subdirectory_is_unrecognised(self):
        self.assertIsNone(self.shape("modules/vpc/main.tf"))

    def gitlab_findings(self, options, wmap=None):
        ci = _GITLAB_TERRAFORM.replace("{options}", "\n".join(f'      - "{o}"' for o in options))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".gitlab-ci.yml"
            p.write_text(ci, encoding="utf-8")
            findings, _ = gitlab_ci_checks.check_gitlab_ci(p, wmap or library_fixtures.gitlab_map())
        return [f for f in findings if f.category in ("Unreachable Module", "Workflow Mismatch")]

    def test_gitlab_terraform_options_follow_the_library_map(self):
        # The fixture map gives the terraform module lint and check; declaring exactly the
        # included modules' options leaves nothing unreachable.
        self.assertEqual(self.gitlab_findings(["full-pipeline", "check", "lint", "secret-scanning"]), [])

    def test_gitlab_options_change_with_the_library_map(self):
        # A release whose map says terraform also provides `build` makes `build` reachable
        # only when declared: the skill follows the map, not a copy of its own.
        wmap = library_fixtures.gitlab_map()
        wmap["modules"]["terraform/.gitlab-ci.yml"]["provides"] = ["build", "lint", "check"]
        findings = self.gitlab_findings(["full-pipeline", "check", "lint", "secret-scanning"], wmap)
        self.assertTrue(any("terraform/.gitlab-ci.yml" in f.message and "build" in f.message for f in findings))

    def test_gitlab_terraform_without_lint_is_unreachable(self):
        findings = self.gitlab_findings(["full-pipeline", "check", "secret-scanning"])
        self.assertTrue(any("terraform/.gitlab-ci.yml" in f.message and "lint" in f.message for f in findings))

    def test_github_terraform_shape_follows_the_library_map(self):
        # The map's terraform scenarios, plus terraform-test.yml and config-scan.yml, which
        # the map does not list and so are not shape-checked.
        present = ["pr.yml", "release.yml", "check.yml", "lint.yml", "secret-scan.yml",
                   "sonarqube.yml", "terraform-test.yml", "config-scan.yml"]
        with tempfile.TemporaryDirectory() as td:
            findings = github_ci_checks.check_shape(Path(td), present, "terraform", library_fixtures.github_map())
        self.assertEqual([f.message for f in findings], [])
        with tempfile.TemporaryDirectory() as td:
            findings = github_ci_checks.check_shape(Path(td), present[:-3], "terraform", library_fixtures.github_map())
        self.assertEqual([f.category for f in findings], ["Missing workflow"])


if __name__ == "__main__":
    unittest.main()
