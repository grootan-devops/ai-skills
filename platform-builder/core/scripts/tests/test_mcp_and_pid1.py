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

spec = importlib.util.spec_from_file_location("gitlab_ci_checks_mcp_test", _PLATFORM_DIR / "ci_checks.py")
gitlab_ci_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitlab_ci_checks)

_LIB_VALUES = """
global:
  releaseNameLength: 6
  metrics:
    enabled: false
    additionalLabels: {}
  tracing:
    enabled: false
    endpoint: http://localhost:4317
  partOf: ""
containers:
  main:
    command: []
    args: []
    image:
      repository: ""
      tag: ""
metrics:
  jobLabel: "app_kubernetes_io_instance"
  endpoints: []
"""

_CONSUMER_VALUES = """
global:
  releaseNameLength: 5
  partOf: "myapp"
component: "orders"
subComponent: "mcp"
containers:
  main:
    command: []
    args: []
    image:
      repository: "myapp/orders-mcp"
      tag: ""
"""


def _chart(root: Path, values: str, manifest: str = '{{- include "tpl.deployment" . }}\n',
           name: str = "myapp-orders-mcp") -> Path:
    chart_dir = root / "chart"
    (chart_dir / "templates").mkdir(parents=True)
    (chart_dir / "templates" / "manifest.yaml").write_text(manifest, encoding="utf-8")
    (chart_dir / "values.yaml").write_text(values, encoding="utf-8")
    (chart_dir / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: {name}\nversion: 0.1.0\n"
        "description: Orders MCP server exposing order tools to AI agents.\n"
        "type: application\nappVersion: \"0.1.0\"\n",
        encoding="utf-8")
    return chart_dir


class NamingTests(unittest.TestCase):
    def _cats(self, values: str, name: str = "myapp-orders-mcp"):
        with tempfile.TemporaryDirectory() as td:
            chart_dir = _chart(Path(td), values, name=name)
            return {f.category: f for f in checks_common.check_helm_chart(chart_dir)}

    def test_any_sub_component_accepted(self):
        cats = self._cats(_CONSUMER_VALUES.replace('"mcp"', '"api"'), name="myapp-orders-api")
        self.assertFalse({"Sub-Component Standard", "Missing subComponent"} & set(cats))
        self.assertNotIn("Chart Name Suggestion", cats)

    def test_sub_component_optional(self):
        values = _CONSUMER_VALUES.replace('subComponent: "mcp"\n', "")
        cats = self._cats(values, name="myapp-orders")
        self.assertNotIn("Missing subComponent", cats)
        self.assertNotIn("Chart Name Suggestion", cats)

    def test_component_and_part_of_mandatory(self):
        values = _CONSUMER_VALUES.replace('component: "orders"', 'component: ""').replace('partOf: "myapp"', 'partOf: ""')
        cats = self._cats(values)
        self.assertEqual(cats["Missing component"].severity, "P1")
        self.assertEqual(cats["Missing partOf"].severity, "P1")

    def test_other_chart_name_is_only_a_suggestion(self):
        cats = self._cats(_CONSUMER_VALUES, name="orders-tools")
        self.assertEqual(cats["Chart Name Suggestion"].severity, "P2")


class OptionalGlobalSectionTests(unittest.TestCase):
    def _parity(self, manifest: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chart_dir = _chart(root, _CONSUMER_VALUES, manifest)
            lib = root / "lib_values.yaml"
            lib.write_text(_LIB_VALUES, encoding="utf-8")
            return checks_common.check_values_parity(chart_dir, lib)

    def test_global_metrics_and_tracing_may_be_omitted_without_monitor(self):
        self.assertEqual(self._parity('{{- include "tpl.deployment" . }}\n'), [])

    def test_global_metrics_required_when_monitor_included(self):
        findings = self._parity('{{- include "tpl.deployment" . }}\n---\n{{ include "tpl.servicemonitor" . }}\n')
        self.assertEqual(len(findings), 1)
        self.assertIn("global.metrics", findings[0].message)
        self.assertNotIn("global.tracing", findings[0].message)


class GitLabReferenceTagTests(unittest.TestCase):
    def test_reference_tag_parses(self):
        ci = """
variables:
  PROJECT_CACHE_KEY: python
Image:Build:
  before_script:
    - !reference [.image-registry-login, before_script]
    - mkdir -p .uv
"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".gitlab-ci.yml"
            p.write_text(ci, encoding="utf-8")
            findings, data = gitlab_ci_checks.check_gitlab_ci(p)
            self.assertNotIn("YAML Syntax Error", {f.category for f in findings})
            self.assertEqual(data["Image:Build"]["before_script"][0],
                             "!reference [.image-registry-login, before_script]")


class ContainerOverrideTests(unittest.TestCase):
    def test_command_and_args_flagged_in_overlays_and_jobs(self):
        overlay = """
containers:
  main:
    command: ["/bin/sh", "-c"]
    args: [". /app/bootstrap.sh; exec /usr/bin/run-server.sh"]
jobs:
  migrate:
    containers:
      migrate:
        command: []
        args: ["migrate", "up"]
"""
        with tempfile.TemporaryDirectory() as td:
            chart_dir = _chart(Path(td), _CONSUMER_VALUES)
            (chart_dir / "values.worker.yaml").write_text(overlay, encoding="utf-8")
            findings = checks_common.check_container_overrides(chart_dir)
            got = {(f.severity, f.category, f.location) for f in findings}
            self.assertIn(("P1", "Chart Overrides Image Entrypoint", "values.worker.yaml:containers.main"), got)
            self.assertIn(("P1", "Chart Overrides Image Command", "values.worker.yaml:containers.main"), got)
            self.assertIn(("P1", "Chart Overrides Image Command", "values.worker.yaml:jobs.migrate.containers.migrate"), got)
            self.assertFalse(any(f.location.startswith("values.yaml") for f in findings))


class Pid1InitTests(unittest.TestCase):
    def _findings(self, dockerfile: str, files=None, categories=("Missing PID 1 Init", "Non-Canonical PID 1", "Missing CMD")):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, body in (files or {}).items():
                (root / name).write_text(body, encoding="utf-8")
            df = root / "Dockerfile"
            df.write_text(dockerfile, encoding="utf-8")
            return [f for f in checks_common.check_dockerfile(df, "service") if f.category in categories]

    def test_missing_entrypoint_is_p1(self):
        df = "FROM base\nUSER 10001:10001\nEXPOSE 8000\nCMD [\"python\", \"main.py\"]\n"
        found = self._findings(df)
        self.assertEqual([(f.severity, f.category) for f in found], [("P1", "Missing PID 1 Init")])

    def test_canonical_entrypoint_passes(self):
        df = ("FROM base\nUSER 10001:10001\nEXPOSE 8000\n"
              "ENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"]\nCMD [\"python\", \"main.py\"]\n")
        self.assertEqual(self._findings(df), [])

    def test_script_inside_entrypoint_is_non_canonical(self):
        df = ("FROM base\nUSER 10001:10001\nEXPOSE 8080\n"
              "ENTRYPOINT [\"/usr/bin/dumb-init\", \"--\", \"./docker-entrypoint.sh\"]\n")
        found = self._findings(df)
        self.assertEqual([(f.severity, f.category) for f in found], [("P1", "Non-Canonical PID 1")])
        self.assertIn("./docker-entrypoint.sh", found[0].message)

    def test_shim_that_execs_dumb_init_is_non_canonical(self):
        df = ("FROM base\nCOPY --chown=10001:10001 entrypoint.sh /app/entrypoint.sh\n"
              "USER 10001:10001\nEXPOSE 8080\nENTRYPOINT [\"/app/entrypoint.sh\"]\nCMD [\"nginx\"]\n")
        shim = "#!/bin/sh\nexec /usr/bin/dumb-init -- \"$@\"\n"
        found = self._findings(df, {"entrypoint.sh": shim})
        self.assertEqual([f.category for f in found], ["Missing PID 1 Init"])

    def test_shell_form_entrypoint_flagged(self):
        df = "FROM base\nUSER 10001:10001\nEXPOSE 8080\nENTRYPOINT /usr/bin/dumb-init --\nCMD [\"app\"]\n"
        self.assertEqual([f.category for f in self._findings(df)], ["Non-Canonical PID 1"])

    def test_dumb_init_without_cmd(self):
        df = "FROM base\nUSER 10001:10001\nEXPOSE 8080\nENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"]\n"
        self.assertEqual([f.category for f in self._findings(df)], ["Missing CMD"])

    def test_dispatcher_branch_without_exec_flagged(self):
        df = ("FROM base\nCOPY --chown=10001:10001 docker-entrypoint.sh /app/docker-entrypoint.sh\n"
              "USER 10001:10001\nEXPOSE 8080\nENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"]\n"
              "CMD [\"/app/docker-entrypoint.sh\"]\n")
        script = ("#!/bin/sh\nset -e\ncase \"${MODE:-api}\" in\n"
                  "  api) exec python main.py ;;\n"
                  "  worker)\n    python worker.py\n    ;;\n"
                  "  *) echo \"invalid MODE\" >&2; exit 1 ;;\nesac\n")
        found = self._findings(df, {"docker-entrypoint.sh": script}, categories=("Start Script Without exec",))
        self.assertEqual(len(found), 1)
        self.assertIn("worker", found[0].message)

    def test_dispatcher_with_exec_everywhere_passes(self):
        df = ("FROM base\nCOPY --chown=10001:10001 docker-entrypoint.sh /app/docker-entrypoint.sh\n"
              "USER 10001:10001\nEXPOSE 8080\nENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"]\n"
              "CMD [\"/app/docker-entrypoint.sh\"]\n")
        script = ("#!/bin/sh\ncase \"${MODE:-api}\" in\n  api) exec python main.py ;;\n"
                  "  worker) exec python worker.py ;;\n  *) echo invalid >&2; exit 1 ;;\nesac\n")
        self.assertEqual(self._findings(df, {"docker-entrypoint.sh": script}, categories=("Start Script Without exec",)), [])


if __name__ == "__main__":
    unittest.main()
