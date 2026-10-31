import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "core" / "scripts"))

import app_report  # noqa: E402
import gitops_fixtures as fx  # noqa: E402
import gitops_library as library  # noqa: E402

ROOT_VALUES = """enabled: true
cluster: lab
environment: dev
server: in-cluster

apps:
  cache:
    chart:
      repoURL: oci://registry.example.com/charts
      version: 1.2.0
      name: cache

  orders:
    api:
      chart:
        repoURL: oci://registry.example.com/charts
        version: 2.0.0
        name: orders
"""

ORDERS_VALUES = """global:
  routes:
    domain: dev.example.com

routes:
  default:
    host: "orders.{{ .Values.global.routes.domain }}"
  admin:
    enabled: false
    host: admin.example.com
"""

ROUTE = """apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: docs
spec:
  hostnames:
    - docs-dev.example.com
"""

EXTRAS_VALUES = """apps:
  docs:
    sync: {}
"""


def render(chart_dir: Path):
    """The root through the fixtures' renderer, plus the extras parent; extras folders by path."""
    if chart_dir.name == "extras":
        return [{"kind": "Application", "metadata": {"name": f"platform-lab-extras-{p.name}-dev"},
                 "spec": {"source": {"path": f"extras/manifests/{p.name}"}}}
                for p in sorted((chart_dir / "manifests").iterdir())]
    return fx.fake_render(chart_dir) + [{"kind": "Application", "metadata": {"name": "platform-lab-extras-dev"},
                                         "spec": {"source": {"path": "extras"}}}]


class AppReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "env"
        self.root.mkdir()
        fx.depend(self.root, "platform", fx.GITOPS, "1.9.0", fx.environment_files())
        (self.root / "values.yaml").write_text(ROOT_VALUES, encoding="utf-8")
        (self.root / "values" / "orders").mkdir(parents=True)
        (self.root / "values" / "orders" / "api.yaml").write_text(ORDERS_VALUES, encoding="utf-8")
        (self.root / "extras" / "manifests" / "docs").mkdir(parents=True)
        (self.root / "extras" / "manifests" / "docs" / "httproute.yaml").write_text(ROUTE, encoding="utf-8")
        (self.root / "extras" / "values.yaml").write_text(EXTRAS_VALUES, encoding="utf-8")
        for name, value in (("render", render), ("vendored", lambda *a: None)):
            p = patch.object(library, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = app_report.main([str(self.root), "--gitops-url", "git@gitlab.example.com:devops/gitops.git",
                                    "--branch", "platform/dev", *extra])
        return code, out.getvalue()

    def test_a_helm_app_and_an_extras_folder(self):
        code, out = self.run_report("--app", "orders/api", "--extras-folder", "docs",
                                    "--argocd-url", "https://argocd.example.com")
        self.assertEqual(code, 0, out)
        blob = "https://gitlab.example.com/devops/gitops/-/blob/platform/dev"
        self.assertIn("  url      https://orders.dev.example.com\n", out)
        self.assertNotIn("admin.example.com", out)                    # a disabled route is not a URL
        self.assertIn("  argocd   platform-lab-orders-api-dev  "
                      "https://argocd.example.com/applications/argo-cd/platform-lab-orders-api-dev", out)
        self.assertIn(f"  values   {blob}/values/orders/api.yaml", out)
        self.assertIn(f"  entry    {blob}/values.yaml#L14", out)
        self.assertIn("  url      https://docs-dev.example.com", out)
        self.assertIn("https://gitlab.example.com/devops/gitops/-/tree/platform/dev/extras/manifests/docs", out)
        self.assertIn(f"  entry    {blob}/extras/values.yaml#L2", out)
        self.assertIn("root       lab-platform-dev-root", out)
        sync = out.split("sync later, in this order:\n")[1].split()
        self.assertEqual([w for w in sync if w.startswith("argo-cd/")],
                         ["argo-cd/lab-platform-dev-root", "argo-cd/platform-lab-extras-dev",
                          "argo-cd/platform-lab-extras-docs-dev", "argo-cd/platform-lab-orders-api-dev"])

    def test_live_urls_win_when_argocd_answers(self):
        with patch.object(app_report, "live_urls", return_value=["https://orders.live.example.com"]):
            code, out = self.run_report("--app", "orders/api", "--argocd-url", "https://argocd.example.com")
        self.assertIn("  url      https://orders.live.example.com", out)
        self.assertIn("from Argo CD (live)", out)
        self.assertNotIn("extras-dev", out)                           # no extras parent without extras

    def test_an_app_without_a_route(self):
        code, out = self.run_report("--app", "cache")
        self.assertEqual(code, 0, out)
        self.assertIn("  url      none: no route exposes it", out)
        self.assertIn("#L7", out)
        self.assertNotIn("values/cache", out)                         # cache has no values file

    def test_an_unknown_app_stops(self):
        with self.assertRaises(SystemExit):
            self.run_report("--app", "missing")

    def test_entry_lines_match_only_the_entry_level(self):
        self.assertEqual(app_report.entry_line(ROOT_VALUES, ["orders", "api"]), 14)
        self.assertIsNone(app_report.entry_line(ROOT_VALUES, ["api"]))
        self.assertIsNone(app_report.entry_line(ROOT_VALUES, ["chart"]))


if __name__ == "__main__":
    unittest.main()
