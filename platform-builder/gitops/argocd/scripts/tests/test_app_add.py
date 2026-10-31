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
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "core" / "scripts"))

import app_add  # noqa: E402
import gitops_fixtures as fx  # noqa: E402
import gitops_library as library  # noqa: E402

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

    def test_the_values_file_follows_its_siblings_layout(self):
        (self.root / "values").mkdir(exist_ok=True)
        (self.root / "values" / "cache.yaml").write_text(
            "global:\n  routes:\n    domain: 'dev.example.com'\nreplicas: 1\nenv:\n- name: A\n  value: b\n",
            encoding="utf-8")
        code, out = self.run_add("--app", "orders", *self.chart_args())
        self.assertEqual(code, 0, out)
        self.assertIn("layout       1 values file(s) on this branch", out)
        text = (self.root / "values" / "orders.yaml").read_text(encoding="utf-8")
        self.assertNotIn("\n\nroutes:", text)                 # no blank line between top-level keys
        self.assertIn("domain: '<ask:", text)                  # single quotes, like the sibling

    def test_without_siblings_the_layout_comes_from_another_environment_branch(self):
        import subprocess
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "HOME": self.tmp.name,
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"}
        origin, other = Path(self.tmp.name) / "origin.git", Path(self.tmp.name) / "other"

        def git(*args, cwd=None):
            subprocess.run(["git", *args], cwd=cwd or self.tmp.name, check=True, capture_output=True, env=env)

        git("init", "--quiet", "--bare", str(origin))
        git("clone", "--quiet", str(origin), str(other))
        git("checkout", "--quiet", "-b", "platform/qa", cwd=other)
        (other / "values").mkdir()
        (other / "values" / "cache.yaml").write_text("a:\n  b: 1\nc:\n  d: 2\n", encoding="utf-8")
        git("add", "-A", cwd=other)
        git("commit", "--quiet", "-m", "qa", cwd=other)
        git("push", "--quiet", "origin", "platform/qa", cwd=other)
        git("init", "--quiet", "-b", "platform/dev", cwd=self.root)
        git("remote", "add", "origin", str(origin), cwd=self.root)
        git("fetch", "--quiet", "origin", cwd=self.root)
        code, out = self.run_add("--app", "orders", *self.chart_args())
        self.assertEqual(code, 0, out)
        self.assertIn("layout       1 values file(s) on platform/qa", out)
        self.assertNotIn("\n\nroutes:", (self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))

    def test_with_no_values_files_anywhere_the_default_layout_is_used(self):
        code, out = self.run_add("--app", "orders", *self.chart_args())
        self.assertIn("layout       no values files to follow; the default layout", out)
        self.assertIn("\n\ncontainers:\n", (self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))

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

    def test_a_relative_root_is_accepted(self):
        import os
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            code, out = self.run_add_at(".", "--app", "orders", *self.chart_args(), "--dry-run")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)
        self.assertIn("dry run: nothing written", out)

    def run_add_at(self, root, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = app_add.main([root, *extra])
        return code, buf.getvalue()

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

    def facts(self, status="ready", image_ref="registry.example.com/oneuptime/orders/dev"):
        facts = {"chart": {"name": "orders", "dir": str(self.svc)},
                 "image": {"registry": "registry.example.com"},
                 "published": {"status": status,
                               "chart": {"ref": "oci://registry.example.com/helm/dev/orders",
                                         "version": "1.4.0-rc.7-3"},
                               "image": {"ref": image_ref, "tag": "1.4.0-rc.7-3"}}}
        path = Path(self.tmp.name) / "facts.json"
        path.write_text(json.dumps(facts), encoding="utf-8")
        return str(path)

    def test_app_facts_fill_the_chart_source_and_the_published_image(self):
        code, _out = self.run_add("--app", "orders", "--app-facts", self.facts())
        self.assertEqual(code, 0)
        new = yaml.safe_load((self.root / "values.yaml").read_text(encoding="utf-8"))
        self.assertEqual(new["apps"]["orders"]["chart"], {"repoURL": "oci://registry.example.com/helm/dev",
                                                          "version": "1.4.0-rc.7-3", "name": "orders"})
        body = yaml.safe_load((self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["containers"]["main"]["image"],
                         {"repository": "oneuptime/orders/dev", "tag": "1.4.0-rc.7-3"})
        self.assertNotIn("image", body["global"])

    def test_app_facts_name_another_registry_when_ci_pushed_elsewhere(self):
        self.run_add("--app", "orders", "--app-facts", self.facts(image_ref="ghcr.io/oneuptime/orders"))
        body = yaml.safe_load((self.root / "values" / "orders.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["global"]["image"], {"registry": "ghcr.io"})
        self.assertEqual(body["containers"]["main"]["image"]["repository"], "oneuptime/orders")

    def test_app_facts_of_an_unpublished_chart_stop(self):
        with self.assertRaises(SystemExit) as stop:
            self.run_add("--app", "orders", "--app-facts", self.facts(status="missing"))
        self.assertIn("app-readiness", str(stop.exception))
        self.assertEqual((self.root / "values.yaml").read_text(encoding="utf-8"), ROOT_VALUES)


if __name__ == "__main__":
    unittest.main()
