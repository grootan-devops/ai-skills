import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_add  # noqa: E402
import gitops_fixtures as fx  # noqa: E402
import library  # noqa: E402

ROOT_VALUES = """enabled: true
cluster: lab
environment: dev
project: developer
server: in-cluster
repoURL: https://git.example.com/team/platform-gitops.git
branch: ""
namespace: ""

sync:
  options:
    - Validate=true
    - CreateNamespace=false
  retry:
    limit: 5

apps:
  cache:
    chart:
      repoURL: oci://registry.example.com/charts
      version: 1.2.0
      name: cache
    sync:
      automated:
        prune: true
        selfHeal: true
      options: []
      retry: {}

  queue:
    namespace: jobs
    api:
      chart:
        repoURL: oci://registry.example.com/charts
        version: 2.0.0
        name: queue
      sync:
        options: []
        retry: {}
"""

SERVICE_VALUES = """
global:
  releaseNameLength: 5
  routes:
    domain: ""
component: orders
subComponent: api
mode: api
containers:
  main:
    image:
      repository: group/orders
      tag: ""
service:
  default:
    spec:
      ports:
        - name: http
          port: 80
routes:
  default:
    enabled: true
database:
  host: ""
  name: orders
oauth:
  clientId: orders
"""

WORKER_OVERLAY = """
mode: worker
subComponent: worker
service: ~
routes:
  default:
    enabled: false
containers:
  main:
    probes:
      enabled: false
"""


class AppAddTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "env"
        self.root.mkdir()
        fx.depend(self.root, "platform", fx.GITOPS, "1.9.0", fx.environment_files())
        (self.root / "values.yaml").write_text(ROOT_VALUES, encoding="utf-8")
        self.svc = Path(self.tmp.name) / "service"
        self.svc.mkdir()
        fx.depend(self.svc, "orders", fx.SERVICE, "1.5.0", fx.service_files())
        (self.svc / "values.yaml").write_text(SERVICE_VALUES, encoding="utf-8")
        (self.svc / "values.worker.yaml").write_text(WORKER_OVERLAY, encoding="utf-8")
        render = patch.object(library, "render", fx.fake_render)
        render.start()
        self.addCleanup(render.stop)
        latest = patch.object(library, "latest_release", return_value="9.9.9")
        latest.start()
        self.addCleanup(latest.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_add(self, *extra):
        argv = [str(self.root), *extra]
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = app_add.main(argv)
        return code, buf.getvalue()

    def chart_args(self, name="orders", version="1.0.0"):
        return ["--chart-repo", "oci://registry.example.com/charts", "--chart-name", name,
                "--chart-version", version, "--service-values", str(self.svc)]

    def test_single_app_added_without_touching_the_rest(self):
        code, out = self.run_add("--app", "orders", *self.chart_args())
        self.assertEqual(code, 0)
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        old = yaml.safe_load(ROOT_VALUES)
        self.assertEqual(new["apps"]["orders"]["chart"],
                         {"repoURL": "oci://registry.example.com/charts", "version": "1.0.0", "name": "orders"})
        self.assertEqual(new["apps"]["cache"], old["apps"]["cache"])
        self.assertEqual({k: v for k, v in new.items() if k != "apps"}, {k: v for k, v in old.items() if k != "apps"})
        self.assertIn("application  platform-lab-orders-dev", out)
        self.assertIn("namespace    platform-dev  (must already exist: CreateNamespace=false)", out)
        self.assertIn("CI yq path   .apps.orders", out)

    def test_values_file_scaffold(self):
        self.run_add("--app", "orders", *self.chart_args())
        body = yaml.safe_load((self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["database"], {"host": "", "name": "orders"})
        self.assertEqual(body["oauth"], {"clientId": "orders"})
        self.assertTrue(body["global"]["routes"]["domain"].startswith("<ask:"))
        self.assertTrue(body["containers"]["main"]["image"]["repository"].startswith("<ask:"))
        self.assertNotIn("mode", body)
        self.assertNotIn("service", body)

    def test_group_release_carries_release_shape(self):
        code, out = self.run_add("--app", "worker", "--group", "orders", *self.chart_args(),
                                 "-f", str(self.svc / "values.worker.yaml"), "--release-name", "orders-worker")
        self.assertEqual(code, 0)
        body = yaml.safe_load((self.root / "values" / "orders" / "worker.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["mode"], "worker")
        self.assertEqual(body["subComponent"], "worker")
        self.assertIsNone(body["service"])
        self.assertEqual(body["routes"]["default"]["enabled"], False)
        self.assertEqual(body["containers"]["main"]["probes"], {"enabled": False})
        self.assertNotIn("check ", out.replace("check        ", "check "))  # release shape is accepted
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(new["apps"]["orders"]["worker"]["releaseName"], "orders-worker")
        self.assertIn("application  platform-lab-orders-worker-dev", out)

    def test_child_added_inside_existing_group(self):
        code, out = self.run_add("--app", "worker", "--group", "queue", *self.chart_args("queue", "2.0.0"),
                                 "--sync", "manual")
        self.assertEqual(code, 0)
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(new["apps"]["queue"]), {"namespace", "api", "worker"})
        self.assertNotIn("automated", new["apps"]["queue"]["worker"]["sync"])
        self.assertIn("namespace    jobs", out)
        self.assertTrue((self.root / "values" / "queue" / "worker.yaml").exists())

    def test_empty_apps_map(self):
        (self.root / "values.yaml").write_text(ROOT_VALUES.split("apps:")[0] + "apps: {}\n", encoding="utf-8")
        self.run_add("--app", "orders", *self.chart_args())
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(new["apps"]), ["orders"])

    def test_existing_entry_and_file_refused(self):
        with self.assertRaises(SystemExit):
            self.run_add("--app", "cache", *self.chart_args("cache", "1.2.0"))
        (self.root / "values").mkdir()
        (self.root / "values" / "orders.yaml").write_text("x: 1\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_add("--app", "orders", *self.chart_args())
        self.assertEqual((self.root / "values.yaml").read_text(encoding="utf-8"), ROOT_VALUES)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_add("--app", "orders", *self.chart_args(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run: nothing written", out)
        self.assertEqual((self.root / "values.yaml").read_text(encoding="utf-8"), ROOT_VALUES)
        self.assertFalse((self.root / "values").exists())

    def test_names_without_cluster_and_with_group_equal_to_chart(self):
        (self.root / "values.yaml").write_text(ROOT_VALUES.replace("cluster: lab\n", ""), encoding="utf-8")
        _code, out = self.run_add("--app", "api", "--group", "platform", *self.chart_args(), "--dry-run")
        self.assertIn("application  platform-api-dev", out)

    def test_numeric_looking_version_is_quoted(self):
        self.run_add("--app", "orders", *self.chart_args(version="1.0"))
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(new["apps"]["orders"]["chart"]["version"], "1.0")

    def test_chart_path_app_has_no_values_file(self):
        code, out = self.run_add("--app", "local", "--chart-path", "charts/local", "--values-file", "dev.yaml")
        self.assertEqual(code, 0)
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(new["apps"]["local"]["chart"], {"path": "charts/local", "valuesFiles": ["dev.yaml"]})
        self.assertIn("releaseName  local", out)
        self.assertFalse((self.root / "values").exists())

    def test_chart_default_credentials_become_questions(self):
        (self.svc / "values.yaml").write_text(
            SERVICE_VALUES + "smtp:\n  host: mail\n  auth:\n    username: bot\n    password: changeme\n", encoding="utf-8")
        self.run_add("--app", "orders", *self.chart_args())
        body = yaml.safe_load((self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["smtp"]["auth"]["username"], "bot")
        self.assertTrue(body["smtp"]["auth"]["password"].startswith("<ask:"))

    def test_bad_names_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_add("--app", "Orders_API", *self.chart_args())

    def test_sync_presets_come_from_the_library(self):
        env_map = json.loads((fx.FIXTURES / "environment-map.json").read_text())
        env_map["apps"]["syncPresets"]["manual"] = {"options": ["ServerSideApply=true"], "retry": {}}
        fx.package(self.root, fx.GITOPS, "1.9.0", fx.environment_files(env_map))
        self.run_add("--app", "orders", *self.chart_args(), "--sync", "manual")
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(new["apps"]["orders"]["sync"], {"options": ["ServerSideApply=true"], "retry": {}})

    def test_a_library_release_without_the_map_stops(self):
        fx.package(self.root, fx.GITOPS, "1.9.0", {})
        with self.assertRaises(SystemExit) as stop:
            self.run_add("--app", "orders", *self.chart_args())
        self.assertIn("ships no docs/environment-map.json", str(stop.exception))
        self.assertIn("to 9.9.9", str(stop.exception))
        self.assertEqual((self.root / "values.yaml").read_text(encoding="utf-8"), ROOT_VALUES)

    def test_root_and_extras_must_pin_one_release(self):
        (self.root / "extras").mkdir()
        fx.depend(self.root / "extras", "platform", fx.GITOPS, "1.8.0", fx.environment_files())
        with self.assertRaises(SystemExit) as stop:
            self.run_add("--app", "orders", *self.chart_args())
        self.assertIn("both charts must pin the same release", str(stop.exception))

    def test_a_service_library_without_the_chart_contract_stops(self):
        fx.package(self.svc, fx.SERVICE, "1.5.0", {"values.schema.json": b"{}"})
        with self.assertRaises(SystemExit) as stop:
            self.run_add("--app", "orders", *self.chart_args())
        self.assertIn("ships no docs/chart-contract.json", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
