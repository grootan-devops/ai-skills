import sys
import tempfile
import unittest
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parents[1]
_PLATFORM_DIR = Path(__file__).resolve().parents[3] / "platforms" / "gitlab"

sys.path.insert(0, str(_CORE_DIR))
sys.path.insert(0, str(_PLATFORM_DIR))

import importlib.util

spec = importlib.util.spec_from_file_location("gitlab_ci_checks", _PLATFORM_DIR / "ci_checks.py")
gitlab_ci_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitlab_ci_checks)


class GitlabDagNeedsTests(unittest.TestCase):
    def check_ci(self, ci_content: str):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / ".gitlab-ci.yml"
            p.write_text(ci_content, encoding="utf-8")
            findings, _ = gitlab_ci_checks.check_gitlab_ci(p)
            return findings

    def test_missing_sonarqube_needs_override_on_multiple_tests(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: "node"
  IMAGE_REPOSITORY: "myapp/backend"
  WORKFLOW:
    value: "full-pipeline"
    options: ["full-pipeline", "sonarqube"]

include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/sonarqube/.gitlab-ci.yml'

Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download

Project:Unit:Test:Frontend:
  extends:
    - .Node:24
    - .Node:Test:Unit

Project:Unit:Test:Backend:
  extends:
    - .Node:24
    - .Node:Test:Unit
"""
        findings = self.check_ci(ci)
        missing_sonar = [f for f in findings if f.category == "Missing SonarQube DAG Needs Override"]
        self.assertEqual(len(missing_sonar), 1)
        self.assertIn("Project:Unit:Test:Backend", missing_sonar[0].message)
        self.assertIn("Project:Unit:Test:Frontend", missing_sonar[0].message)

    def test_incomplete_sonarqube_needs_override(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: "node"
  IMAGE_REPOSITORY: "myapp/backend"
  WORKFLOW:
    value: "full-pipeline"
    options: ["full-pipeline", "sonarqube"]

include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/sonarqube/.gitlab-ci.yml'

Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download

Project:Unit:Test:Frontend:
  extends:
    - .Node:24
    - .Node:Test:Unit

Project:Unit:Test:Backend:
  extends:
    - .Node:24
    - .Node:Test:Unit

Sonarqube:
  needs:
    - job: Common:Init
      artifacts: true
      optional: true
    - job: Project:Unit:Test:Frontend
      artifacts: true
      optional: true
"""
        findings = self.check_ci(ci)
        incomplete = [f for f in findings if f.category == "Incomplete SonarQube DAG Needs"]
        self.assertEqual(len(incomplete), 1)
        self.assertIn("Project:Unit:Test:Backend", incomplete[0].message)

    def test_valid_sonarqube_needs_override(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: "node"
  IMAGE_REPOSITORY: "myapp/backend"
  WORKFLOW:
    value: "full-pipeline"
    options: ["full-pipeline", "sonarqube"]

include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/sonarqube/.gitlab-ci.yml'

Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download

Project:Unit:Test:Frontend:
  extends:
    - .Node:24
    - .Node:Test:Unit

Project:Unit:Test:Backend:
  extends:
    - .Node:24
    - .Node:Test:Unit

Sonarqube:
  needs:
    - job: Common:Init
      artifacts: true
      optional: true
    - job: Project:Unit:Test:Frontend
      artifacts: true
      optional: true
    - job: Project:Unit:Test:Backend
      artifacts: true
      optional: true
"""
        findings = self.check_ci(ci)
        sonar_findings = [f for f in findings if "SonarQube" in f.category]
        self.assertEqual(len(sonar_findings), 0)

    def test_missing_image_build_needs_override_on_multiple_builds(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: "node"
  IMAGE_REPOSITORY: "myapp/backend"
  WORKFLOW:
    value: "full-pipeline"
    options: ["full-pipeline", "image-build-and-push"]

include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/image/.docker.gitlab-ci.yml'

Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download

Project:Build:Frontend:
  extends:
    - .Node:24
    - .Node:Build

Project:Build:Backend:
  extends:
    - .Node:24
    - .Node:Build

Project:Unit:Test:
  extends:
    - .Node:24
    - .Node:Test:Unit
"""
        findings = self.check_ci(ci)
        missing_image = [f for f in findings if f.category == "Missing Image:Build DAG Needs Override"]
        self.assertEqual(len(missing_image), 1)
        self.assertIn("Project:Build:Frontend", missing_image[0].message)


if __name__ == "__main__":
    unittest.main()
