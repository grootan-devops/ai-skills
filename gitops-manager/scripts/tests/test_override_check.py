import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import override_check  # noqa: E402

CHART = """
global:
  releaseNameLength: 5
  routes:
    domain: ""
component: orders
containers:
  main:
    image:
      repository: group/orders
mounts:
  emptyDir:
    cache:
      enabled: true
      path: /cache
database:
  host: ""
"""


class OverrideCheckTests(unittest.TestCase):
    def _review(self, env: str, overlay: str = ""):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "chart").mkdir()
            (root / "chart" / "values.yaml").write_text(CHART, encoding="utf-8")
            if overlay:
                (root / "chart" / "values.worker.yaml").write_text(overlay, encoding="utf-8")
            envf = root / "env.yaml"
            envf.write_text(env, encoding="utf-8")
            chart_values = override_check.load(root / "chart" / "values.yaml")
            data = override_check.load(envf)
            multi = bool(overlay) or any(k in data for k in ("mode", "subComponent"))
            return override_check.review(data, chart_values, multi)

    def test_environment_categories_pass(self):
        env = """
global:
  routes:
    domain: dev.example.com
    ingressClass: private
  image:
    registry: registry.example.com
routes:
  default:
    host: "orders-dev.{{ .Values.global.routes.domain }}"
    httpRoute: false
containers:
  main:
    image:
      repository: group/orders/dev
    resources:
      limits:
        memory: 1Gi
database:
  host: db.example.svc
"""
        self.assertEqual(self._review(env), [])

    def test_structural_overrides_reported(self):
        env = """
global:
  releaseNameLength: 6
mounts:
  emptyDir:
    cache:
      enabled: true
routes:
  default:
    paths:
      - name: api
containers:
  main:
    configmapEnvs: |
      A: b
"""
        notes = "\n".join(self._review(env))
        self.assertIn("global.releaseNameLength", notes)
        self.assertIn("mounts.emptyDir.cache.enabled: mounts belong in the chart (same as the chart", notes)
        self.assertIn("routes.default.paths", notes)
        self.assertIn("containers.main.configmapEnvs", notes)

    def test_release_shape_only_for_multi_release(self):
        env = "mode: worker\nsubComponent: worker\nservice: ~\nroutes:\n  default:\n    enabled: false\n"
        self.assertEqual(self._review(env, overlay="mode: worker\n"), [])
        single = "containers:\n  main:\n    probes:\n      enabled: false\n"
        self.assertTrue(any("probe fixes belong in the chart" in n for n in self._review(single)))


if __name__ == "__main__":
    unittest.main()
