import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_PLATFORM_DIR = Path(__file__).resolve().parents[3] / "platforms" / "github"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLATFORM_DIR))

spec = importlib.util.spec_from_file_location("github_ci_checks", _PLATFORM_DIR / "ci_checks.py")
github_ci_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github_ci_checks)


class GitHubPRRunNameTests(unittest.TestCase):
    def check_workflow_content(self, filename: str, content: str):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wf_dir = repo / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / filename).write_text(content, encoding="utf-8")
            findings, _ = github_ci_checks.ci_checks(repo)
            return findings

    def test_missing_run_name_flagged(self):
        wf = """
name: CI · PR Verification
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""
        findings = self.check_workflow_content("pr.yml", wf)
        categories = [f.category for f in findings]
        self.assertIn("No run name", categories)

    def test_non_directional_pr_run_name_flagged(self):
        wf = """
name: CI · PR Verification
run-name: "CI · ${{ github.event_name }} · ${{ github.sha }}"
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""
        findings = self.check_workflow_content("pr.yml", wf)
        categories = [f.category for f in findings]
        self.assertIn("Non-directional PR run-name", categories)

    def test_directional_pr_run_name_passes(self):
        wf = """
name: CI · PR Verification
run-name: >-
  ${{ github.event_name == 'pull_request'
      && format('PR #{0}: {1} -> {2} ({3})', github.event.pull_request.number, github.head_ref, github.base_ref, github.sha)
      || format('Verify · {0}', github.ref_name) }}
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""
        findings = self.check_workflow_content("pr.yml", wf)
        run_name_findings = [f for f in findings if "run-name" in f.location]
        self.assertEqual(len(run_name_findings), 0)

    def test_standard_non_pr_run_name_passes(self):
        wf = """
name: CD · Production Release
run-name: "CD · ${{ github.event_name }} · ${{ github.sha }}"
on:
  push:
    tags: ['v*']
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - run: echo release
"""
        findings = self.check_workflow_content("release.yml", wf)
        run_name_findings = [f for f in findings if "run-name" in f.location]
        self.assertEqual(len(run_name_findings), 0)


if __name__ == "__main__":
    unittest.main()
