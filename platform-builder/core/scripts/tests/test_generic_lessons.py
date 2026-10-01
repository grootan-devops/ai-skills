"""Checks added from consumer onboarding field lessons (generic, no project names)."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_PLATFORM_DIR = Path(__file__).resolve().parents[3] / "platforms" / "gitlab"
sys.path.insert(0, str(_SCRIPTS))

import checks_common  # noqa: E402

spec = importlib.util.spec_from_file_location("gitlab_ci_checks_lessons_test", _PLATFORM_DIR / "ci_checks.py")
gitlab_ci_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitlab_ci_checks)

BASE_VALUES = """
global:
  releaseNameLength: 5
  partOf: "myapp"
  image:
    registry: "registry.example.com"
    pullSecrets: ["registry-pull"]
component: "orders"
subComponent: ""
pod:
  securityContext:
    runAsUser: 10001
    runAsGroup: 10001
containers:
  main:
    command: []
    args: []
    image:
      repository: "group/orders"
      tag: ""
    probes:
      enabled: true
      readiness:
        httpGet:
          path: /health
          port: http
service:
  default:
    spec:
      type: ClusterIP
      ports:
        - name: http
          port: 80
          targetPort: 8080
routes:
  default:
    enabled: true
    paths:
      - name: api
        port: http
"""


def _chart(root: Path, values: str = BASE_VALUES, manifest: str = '{{- include "tpl.deployment" . }}\n',
           overlays=None, schema=None) -> Path:
    chart = root / "chart"
    (chart / "templates").mkdir(parents=True)
    (chart / "templates" / "manifest.yaml").write_text(manifest, encoding="utf-8")
    (chart / "values.yaml").write_text(values, encoding="utf-8")
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: myapp-orders\nversion: 1.0.0\n", encoding="utf-8")
    for name, body in (overlays or {}).items():
        (chart / name).write_text(body, encoding="utf-8")
    if schema is not None:
        (chart / "values.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    return chart


def _cats(findings):
    return {f.category for f in findings}


class DockerfileImageArgTests(unittest.TestCase):
    def _df(self, text, ci=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            if ci:
                (root / ".gitlab-ci.yml").write_text(ci, encoding="utf-8")
            df = root / "Dockerfile"
            df.write_text(text, encoding="utf-8")
            checks_common.set_platform("gitlab")
            try:
                return checks_common.check_dockerfile(df, "service")
            finally:
                checks_common.set_platform("generic")

    def test_build_switch_args_are_not_images(self):
        found = self._df('ARG BUILD_TRANSLATIONS="false"\nARG DEV_MODE=build\nFROM ${APP_BASE_IMAGE}\n'
                         "COPY --chown=10001:10001 . /app\nUSER 10001\n")
        self.assertFalse(any(f.category == "Unproxied External Image" for f in found))

    def test_empty_proxy_prefix_and_ci_supplied_base_pass(self):
        found = self._df('ARG CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX=""\nARG APP_BASE_IMAGE\nFROM ${APP_BASE_IMAGE}\n'
                         "COPY --chown=10001:10001 . /app\nUSER 10001\n",
                         ci='variables:\n  DOCKER_BUILD_ARG_APP_BASE_IMAGE: "${CI_REGISTRY_IMAGE}/base:1.0"\n')
        cats = _cats(found)
        self.assertNotIn("Unproxied External Image", cats)
        self.assertNotIn("Missing Default Base Image in ARG", cats)

    def test_public_image_arg_still_flagged(self):
        found = self._df("ARG RUNTIME_IMAGE=python:3.12-slim\nFROM ${RUNTIME_IMAGE}\n"
                         "COPY --chown=10001:10001 . /app\nUSER 10001\n")
        self.assertIn("Unproxied External Image", _cats(found))


class RenovateTests(unittest.TestCase):
    def _pins(self, text):
        with tempfile.TemporaryDirectory() as td:
            df = Path(td) / "Dockerfile"
            df.write_text(text, encoding="utf-8")
            return [f for f in checks_common.check_dockerfile(df, "service") if f.category == "Unannotated Version Pin"]

    def test_public_registry_pin_needs_annotation(self):
        self.assertEqual(len(self._pins("ARG PYTHON_VERSION=3.12.14\nFROM python:${PYTHON_VERSION}-slim\n")), 1)

    def test_annotated_public_pin_passes(self):
        text = "# renovate: datasource=docker depName=python\nARG PYTHON_VERSION=3.12.14\nFROM python:${PYTHON_VERSION}-slim\n"
        self.assertEqual(self._pins(text), [])

    def test_private_registry_pin_needs_no_annotation(self):
        self.assertEqual(self._pins("ARG BASE_TAG=3.12.14\nFROM registry.example.com:5050/group/app/base:${BASE_TAG}\n"), [])

    def test_non_image_version_is_ignored(self):
        self.assertEqual(self._pins("ARG TOOL_VERSION=9.15.9\nFROM ${APP_BASE_IMAGE}\n"), [])


class ChartRuntimeTests(unittest.TestCase):
    def test_clean_chart_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(checks_common.check_chart_runtime(_chart(Path(td))), [])

    def test_service_without_ports(self):
        values = BASE_VALUES.replace(
            "      ports:\n        - name: http\n          port: 80\n          targetPort: 8080\n", "      ports: []\n")
        with tempfile.TemporaryDirectory() as td:
            cats = _cats(checks_common.check_chart_runtime(_chart(Path(td), values)))
        self.assertTrue({"Service Without Ports", "Probe Port Not Declared", "Route Port Not Declared"} <= cats)

    def test_pod_security_emptydir_and_claim(self):
        values = BASE_VALUES.replace(
            "pod:\n  securityContext:\n    runAsUser: 10001\n    runAsGroup: 10001\n", "pod:\n  securityContext: {}\n")
        values += ("mounts:\n  emptyDir:\n    cache:\n      path: /.cache\n  pvc:\n    data:\n"
                   "      path: /data\n      claimName: fixed-claim\npersistence:\n  data:\n    enabled: true\n")
        with tempfile.TemporaryDirectory() as td:
            cats = _cats(checks_common.check_chart_runtime(_chart(Path(td), values)))
        self.assertTrue({"Empty Pod securityContext", "emptyDir Not Enabled", "Hard-coded PVC Claim"} <= cats)

    def test_non_http_overlay(self):
        overlay = "mode: worker\nsubComponent: worker\nservice: ~\n"
        with tempfile.TemporaryDirectory() as td:
            cats = _cats(checks_common.check_chart_runtime(_chart(Path(td), overlays={"values.worker.yaml": overlay})))
        self.assertIn("Routes On Release Without Service", cats)
        self.assertIn("HTTP Probe Without Service", cats)


class SecretPlacementTests(unittest.TestCase):
    def test_credential_in_configmap_env_and_args(self):
        overlay = """
containers:
  main:
    configmapEnvs: |
      REDIS_URL: redis://{{ .Values.redis.auth.username }}:{{ .Values.redis.auth.password }}@redis:6379
jobs:
  seed:
    containers:
      seed:
        args: ["--admin-password={{ .Values.admin.password }}"]
"""
        with tempfile.TemporaryDirectory() as td:
            found = checks_common.check_secret_placement(_chart(Path(td), overlays={"values.job.yaml": overlay}))
        got = {(f.severity, f.category) for f in found}
        self.assertIn(("P0", "Secret Leak in ConfigMap"), got)
        self.assertIn(("P0", "Secret Leak in Container Args"), got)

    def test_secret_envs_are_fine(self):
        overlay = "containers:\n  main:\n    secretEnvs: |\n      DB_PASSWORD: {{ .Values.database.auth.password }}\n"
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(checks_common.check_secret_placement(_chart(Path(td), overlays={"values.x.yaml": overlay})), [])


class SchemaAndModeTests(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "properties": {
            "mode": {"enum": ["", "api", "worker"]},
            "containers": {"type": "object", "additionalProperties": {
                "type": "object", "properties": {"image": {"type": "object", "properties": {
                    "repository": {"type": "string", "minLength": 1}}}}}},
        },
    }

    def test_schema_rejects_empty_default_and_empty_enum(self):
        values = BASE_VALUES.replace('repository: "group/orders"', 'repository: ""') + "mode: api\n"
        overlays = {"values.worker.yaml": "mode: queue\n"}
        with tempfile.TemporaryDirectory() as td:
            found = checks_common.check_schema_contract(_chart(Path(td), values, overlays=overlays, schema=self.SCHEMA))
        got = {(f.severity, f.category) for f in found}
        self.assertIn(("P1", "Schema Rejects Default Value"), got)
        self.assertIn(("P2", "Empty Enum Value"), got)
        self.assertIn(("P1", "Value Not In Schema Enum"), got)

    def test_each_mode_needs_its_own_sub_component(self):
        values = BASE_VALUES + "mode: api\n"
        overlays = {"values.worker.yaml": "mode: worker\n", "values.api.yaml": "mode: api\nsubComponent: api\n"}
        with tempfile.TemporaryDirectory() as td:
            cats = _cats(checks_common.check_modes(_chart(Path(td), values, overlays=overlays)))
        self.assertIn("Missing subComponent For Mode", cats)

    def test_single_mode_needs_no_sub_component(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(checks_common.check_modes(_chart(Path(td), BASE_VALUES + "mode: api\n")), [])


class OptionalBlockTests(unittest.TestCase):
    def test_unused_blocks_flagged(self):
        values = BASE_VALUES + "jobs: {}\npersistence: {}\nmetrics:\n  endpoints: []\n"
        with tempfile.TemporaryDirectory() as td:
            found = checks_common.check_optional_blocks(_chart(Path(td), values))
        self.assertEqual({f.location.split(":")[1] for f in found}, {"jobs", "persistence", "metrics"})

    def test_used_block_passes(self):
        values = BASE_VALUES + "jobs: {}\n"
        manifest = '{{- include "tpl.deployment" . }}\n{{- include "tpl.job" . }}\n'
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(checks_common.check_optional_blocks(_chart(Path(td), values, manifest)), [])


class LeafDocTests(unittest.TestCase):
    def test_undocumented_leaf_and_blob_parent(self):
        values = BASE_VALUES + """
jobs:
  seed:
    # -- Run the seed job.
    # @section -- Job Settings
    enabled: true
    backoffLimit: 2
    # -- Containers of the job.
    # @section -- Job Settings
    containers:
      seed:
        # -- Environment for the job.
        # @section -- Job Container Settings
        configmapEnvs: |
          MODE: seed
          NOT_A_KEY: value
"""
        with tempfile.TemporaryDirectory() as td:
            found = {f.category: f for f in checks_common.check_values_leaf_docs(_chart(Path(td), values))}
        self.assertIn("jobs.seed.backoffLimit", found["Undocumented Values Leaf"].message)
        self.assertNotIn("MODE", found["Undocumented Values Leaf"].message)
        self.assertIn("jobs.seed.containers", found["Parent Map Documented"].message)

    def test_opaque_parent_passes(self):
        values = BASE_VALUES + """
jobs:
  seed:
    # -- Run the seed job.
    # @section -- Job Settings
    enabled: true
    # -- Containers of the job.
    # @section -- Job Settings
    # @default -- Check values.yaml
    containers:
      seed:
        image: {}
"""
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(checks_common.check_values_leaf_docs(_chart(Path(td), values)), [])


class CommentStyleTests(unittest.TestCase):
    def test_multi_line_comment_flagged_directives_and_licence_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Dockerfile").write_text(
                "# Licensed under the Apache License\n# Copyright someone\nFROM base\n"
                "# renovate: datasource=docker depName=x\nARG X_VERSION=1.0.0\n"
                "# restore the cache the build needs\nRUN true\n"
                "# ---- runtime ----\n# copy the app and set ownership\nCOPY . /app\n", encoding="utf-8")
            found = checks_common.check_comment_style(root, None)
        self.assertEqual(len(found), 1)
        self.assertIn("Dockerfile:8 (2 lines)", found[0].message)


class ImagePathTests(unittest.TestCase):
    def test_derived_repository_differs_from_ci_path(self):
        values = BASE_VALUES.replace('repository: "group/orders"', 'repository: ""').replace(
            'registry: "registry.example.com"', 'registry: "cr.io"')
        with tempfile.TemporaryDirectory() as td:
            chart = _chart(Path(td), values)
            found = checks_common.check_image_path_alignment(Path(td), chart, {"IMAGE_REPOSITORY": "group/orders"})
        got = {f.category for f in found}
        self.assertIn("Chart Image Differs From CI Push Path", got)
        self.assertIn("Library Placeholder Registry", got)

    def test_matching_repository_passes(self):
        with tempfile.TemporaryDirectory() as td:
            chart = _chart(Path(td))
            self.assertEqual(checks_common.check_image_path_alignment(Path(td), chart, {"IMAGE_REPOSITORY": "group/orders"}), [])


class HygieneTests(unittest.TestCase):
    def test_ai_tooling_reported_not_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
            (root / "CLAUDE.md").write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".claude", "CLAUDE.md"], check=True)
            (root / "AGENTS.md").write_text("x", encoding="utf-8")
            found = {f.category: f for f in checks_common.check_project_hygiene(root)}
            self.assertTrue((root / "CLAUDE.md").exists())
        self.assertIn(".claude/", found["AI/Agent Files Tracked"].message)
        self.assertIn("AGENTS.md", found["AI/Agent Files Present"].message)

    def test_changelog_must_start_with_h1(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "CHANGELOG.md").write_text("<!-- licence -->\n# Changelog\n", encoding="utf-8")
            self.assertEqual([f.category for f in checks_common.check_changelog(Path(td))], ["Changelog Not Starting With H1"])

    def test_requirements_files_are_not_judged(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("flask\n", encoding="utf-8")
            (Path(td) / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            self.assertEqual(checks_common.check_project_hygiene(Path(td)), [])


class GitLabAdapterTests(unittest.TestCase):
    def _ci(self, text, files=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, body in (files or {}).items():
                (root / name).write_text(body, encoding="utf-8")
            ci = root / ".gitlab-ci.yml"
            ci.write_text(text, encoding="utf-8")
            return gitlab_ci_checks.check_gitlab_ci(ci)

    def test_no_dependency_job_required_without_language_module(self):
        findings, _ = self._ci("include:\n  - remote: 'https://raw.githubusercontent.com/org/gitlab-ci-library/1.4.0/image/.gitlab-ci.yml'\n"
                               "variables:\n  PROJECT_CACHE_KEY: app\n")
        self.assertFalse(any("dependency-download" in f.message for f in findings))

    def test_remote_include_ref_is_read(self):
        _, data = self._ci("include:\n  - remote: 'https://raw.githubusercontent.com/org/gitlab-ci-library/1.4.0/common/.gitlab-ci.yml'\n"
                           "  - remote: 'https://gitlab.example.com/grp/gitlab-ci-library/-/raw/1.5.0/chart/.gitlab-ci.yml'\n")
        self.assertEqual(gitlab_ci_checks.library_refs(data), {"gitlab-ci-library": "1.4.0"})

    def test_non_semver_project_version(self):
        pom = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
               '<artifactId>x</artifactId><version>4.3.1.2</version></project>')
        findings, _ = self._ci("Project:Version:Init:\n  extends: .Java:Project:Version:Init\n", {"pom.xml": pom})
        self.assertIn("Non-SemVer Project Version", _cats(findings))

    def test_unknown_extends_target(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib"
            (lib / "nodejs").mkdir(parents=True)
            (lib / "nodejs" / ".gitlab-ci.yml").write_text(".Node:24:\n  image: x\n.Node:Dependency:Download:\n  stage: prepare\n", encoding="utf-8")
            data = {"Node:Dependency:Download": {"extends": [".Node:20", ".Node:Dependency:Download"]}}
            found = gitlab_ci_checks.check_extends_targets(data, Path(td) / ".gitlab-ci.yml", lib, "1.4.0")
        self.assertEqual(len(found), 1)
        self.assertIn(".Node:20", found[0].message)


class SiblingTests(unittest.TestCase):
    def test_stale_sibling_reported(self):
        sys.path.insert(0, str(_SCRIPTS))
        import verify_siblings  # noqa: E402
        with tempfile.TemporaryDirectory() as td:
            api = _chart(Path(td) / "api", BASE_VALUES.replace('subComponent: ""', 'subComponent: "queue"'))
            client_values = BASE_VALUES.replace('component: "orders"', 'component: "billing"') + (
                'orders:\n  internalUrl: \'{{ tpl .Values.orders.internalUrl $ | default (include '
                '"tpl.resource.siblingName" (merge (dict "name" "orders-backend") .)) }}\'\n')
            client = _chart(Path(td) / "client", client_values)
            self.assertEqual(verify_siblings.main([str(api), str(client)]), 1)
            fixed = client_values.replace('"orders-backend"', '"orders-queue"')
            (client / "values.yaml").write_text(fixed, encoding="utf-8")
            self.assertEqual(verify_siblings.main([str(api), str(client)]), 0)


if __name__ == "__main__":
    unittest.main()
