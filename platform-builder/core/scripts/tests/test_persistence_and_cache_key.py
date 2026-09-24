import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_PLATFORM_DIR = Path(__file__).resolve().parents[3] / "platforms" / "gitlab"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLATFORM_DIR))

import checks_common  # noqa: E402

spec = importlib.util.spec_from_file_location("gitlab_ci_checks_test", _PLATFORM_DIR / "ci_checks.py")
gitlab_ci_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitlab_ci_checks)


class PersistenceAndCacheKeyTests(unittest.TestCase):
    def test_custom_project_cache_key_chat_passes(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: "chat"
  IMAGE_REPOSITORY: "grootantech/chat"
  CHART_REPOSITORY: "grootantech/chart"
  WORKFLOW:
    value: "full-pipeline"
    options:
      - "full-pipeline"
include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
stages:
  - build
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".gitlab-ci.yml"
            p.write_text(ci, encoding="utf-8")
            findings, _ = gitlab_ci_checks.check_gitlab_ci(p)
            cache_findings = [f for f in findings if "PROJECT_CACHE_KEY" in f.category or "PROJECT_CACHE_KEY" in f.message]
            self.assertEqual(len(cache_findings), 0)

    def test_missing_project_cache_key_flagged(self):
        ci = """
variables:
  IMAGE_REPOSITORY: "grootantech/chat"
  CHART_REPOSITORY: "grootantech/chart"
include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/docs/1.2.0/common/.gitlab-ci.yml'
stages:
  - build
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".gitlab-ci.yml"
            p.write_text(ci, encoding="utf-8")
            findings, _ = gitlab_ci_checks.check_gitlab_ci(p)
            cache_findings = [f for f in findings if "PROJECT_CACHE_KEY" in f.category]
            self.assertEqual(len(cache_findings), 1)
            self.assertEqual(cache_findings[0].category, "Missing PROJECT_CACHE_KEY")

    def test_values_parity_omits_persistence_when_tpl_pvc_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chart_dir = root / "chart"
            templates_dir = chart_dir / "templates"
            templates_dir.mkdir(parents=True)

            manifest = templates_dir / "manifest.yaml"
            manifest.write_text('{{- include "tpl.deployment" . }}\n', encoding="utf-8")

            # Minimal library values with persistence
            lib_values_file = root / "lib_values.yaml"
            lib_values_file.write_text("""
containers:
  main:
    image:
      repository: ""
      tag: ""
persistence: {}
""", encoding="utf-8")

            # Consumer values without persistence
            consumer_values = chart_dir / "values.yaml"
            consumer_values.write_text("""
containers:
  main:
    image:
      repository: "app"
      tag: "v1.0.0"
""", encoding="utf-8")

            findings = checks_common.check_values_parity(chart_dir, lib_values_file)
            self.assertEqual(len(findings), 0)

    def test_values_parity_requires_persistence_when_tpl_pvc_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chart_dir = root / "chart"
            templates_dir = chart_dir / "templates"
            templates_dir.mkdir(parents=True)

            manifest = templates_dir / "manifest.yaml"
            manifest.write_text('{{- include "tpl.deployment" . }}\n---\n{{- include "tpl.pvc" . }}\n', encoding="utf-8")

            lib_values_file = root / "lib_values.yaml"
            lib_values_file.write_text("""
containers:
  main:
    image:
      repository: ""
      tag: ""
persistence: {}
""", encoding="utf-8")

            consumer_values = chart_dir / "values.yaml"
            consumer_values.write_text("""
containers:
  main:
    image:
      repository: "app"
      tag: "v1.0.0"
""", encoding="utf-8")

            findings = checks_common.check_values_parity(chart_dir, lib_values_file)
            self.assertEqual(len(findings), 1)
            self.assertIn("persistence", findings[0].message)

    def test_active_persistence_without_tpl_pvc_flags_p1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chart_dir = root / "chart"
            templates_dir = chart_dir / "templates"
            templates_dir.mkdir(parents=True)

            manifest = templates_dir / "manifest.yaml"
            manifest.write_text('{{- include "tpl.deployment" . }}\n', encoding="utf-8")

            lib_values_file = root / "lib_values.yaml"
            lib_values_file.write_text("""
containers:
  main:
    image:
      repository: ""
      tag: ""
persistence: {}
""", encoding="utf-8")

            consumer_values = chart_dir / "values.yaml"
            consumer_values.write_text("""
containers:
  main:
    image:
      repository: "app"
      tag: "v1.0.0"
persistence:
  data:
    enabled: true
    size: 10Gi
""", encoding="utf-8")

            findings = checks_common.check_values_parity(chart_dir, lib_values_file)
            pvc_findings = [f for f in findings if f.category == "Missing tpl.pvc in manifest.yaml"]
            self.assertEqual(len(pvc_findings), 1)


if __name__ == "__main__":
    unittest.main()
