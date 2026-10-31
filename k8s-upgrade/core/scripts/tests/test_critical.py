"""critical.py: which workloads the cluster and its applications depend on."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import critical  # noqa: E402


def workload(ns, kind, name, image, desired=1, ready=1):
    w = {"kind": kind, "metadata": {"namespace": ns, "name": name},
         "spec": {"template": {"spec": {"containers": [{"name": "main", "image": image}]}}}, "status": {}}
    if kind == "DaemonSet":
        w["status"] = {"desiredNumberScheduled": desired, "numberReady": ready}
    else:
        w["spec"]["replicas"] = desired
        w["status"] = {"readyReplicas": ready}
    return w


WORKLOADS = [
    workload("kube-system", "DaemonSet", "calico-node", "quay.io/calico/node:v3.28.0", 3, 3),
    workload("kube-system", "Deployment", "coredns", "registry.k8s.io/coredns/coredns:v1.11.1", 2, 2),
    workload("longhorn-system", "DaemonSet", "longhorn-manager", "longhornio/longhorn-manager:v1.7.1", 3, 2),
    workload("data", "StatefulSet", "orders-db", "docker.io/library/postgres:16", 1, 1),
    workload("apps", "Deployment", "api", "registry.example.com/api:1.4.0", 2, 2),
    workload("apps", "Deployment", "frontend", "registry.example.com/frontend:3.1.0", 2, 2),
    workload("platform", "Deployment", "cluster-operator", "registry.example.com/cluster-operator:0.9.0"),
]


class CriticalTests(unittest.TestCase):
    def test_workloads_are_classified_by_name_and_image_in_category_order(self):
        cats = {w["metadata"]["name"]: (critical.classify(w) or ("-",))[0] for w in WORKLOADS}
        self.assertEqual(cats, {"calico-node": "cni", "coredns": "kube-system", "longhorn-manager": "storage",
                                "orders-db": "database", "api": "-", "frontend": "-",
                                "cluster-operator": "operator"})

    def test_daemonsets_count_scheduled_pods_and_deployments_count_replicas(self):
        self.assertEqual(critical.replicas(WORKLOADS[2]), (3, 2))
        no_replicas = workload("apps", "Deployment", "api", "x")
        del no_replicas["spec"]["replicas"]
        self.assertEqual(critical.replicas(no_replicas), (1, 1))

    def test_main_writes_critical_json_with_the_extra_workloads_and_warns_on_unknown_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = Path(tmp) / "critical-extra.txt"
            extra.write_text("# marked by the user\napps/Deployment/api\napps/deployment/gone\n")
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "argv", ["critical.py", tmp, str(extra)]), \
                    patch.object(critical, "kubectl_json", return_value={"items": WORKLOADS}), \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(critical.main(), 0)
            rows = json.loads((Path(tmp) / "critical.json").read_text())
        names = {r["name"]: r for r in rows}
        self.assertEqual(names["api"]["category"], "extra")
        self.assertNotIn("frontend", names)
        self.assertEqual((names["longhorn-manager"]["desired"], names["longhorn-manager"]["ready"]), (3, 2))
        self.assertIn("longhorn-manager 2/3  NOT READY", out.getvalue())
        self.assertIn("WARNING critical-extra.txt: apps/deployment/gone not found", err.getvalue())
        self.assertEqual([r["category"] for r in rows], sorted(r["category"] for r in rows))


if __name__ == "__main__":
    unittest.main()
