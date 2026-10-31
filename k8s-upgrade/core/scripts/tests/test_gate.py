"""gate.py checks against canned kubectl answers: what passes and what stops the next node."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gate  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "baseline"


def node(name, kubelet="v1.31.2", runtime="containerd://1.7.22", ready=True, cordoned=False):
    return {"metadata": {"name": name}, "spec": {"unschedulable": True} if cordoned else {},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                       "nodeInfo": {"kubeletVersion": kubelet, "containerRuntimeVersion": runtime}}}


def workload(ns, kind, name, desired, ready):
    if kind == "DaemonSet":
        return {"kind": kind, "metadata": {"namespace": ns, "name": name},
                "status": {"desiredNumberScheduled": desired, "numberReady": ready}}
    return {"kind": kind, "metadata": {"namespace": ns, "name": name}, "spec": {"replicas": desired},
            "status": {"readyReplicas": ready}}


class GateTests(unittest.TestCase):
    def kubectl_node(self, n):
        return patch.object(gate, "kubectl", return_value=json.dumps(n))

    def test_a_node_on_the_expected_kubelet_passes_and_any_other_version_fails(self):
        with self.kubectl_node(node("worker-1")):
            self.assertTrue(gate.check_node("worker-1", "v1.31.2")[0])
            ok, detail = gate.check_node("worker-1", "v1.31.3")
        self.assertFalse(ok)
        self.assertEqual(detail, "Ready=True kubelet=v1.31.2 expected=v1.31.3")

    def test_a_not_ready_node_fails_even_on_the_right_version(self):
        with self.kubectl_node(node("worker-1", ready=False)):
            self.assertFalse(gate.check_node("worker-1", None)[0])

    def test_cri_o_must_match_the_kubelet_minor(self):
        with self.kubectl_node(node("worker-1", kubelet="v1.31.2", runtime="cri-o://1.31.1")):
            self.assertTrue(gate.check_runtime("worker-1")[0])
        with self.kubectl_node(node("worker-1", kubelet="v1.31.2", runtime="cri-o://1.30.6")):
            ok, detail = gate.check_runtime("worker-1")
        self.assertFalse(ok)
        self.assertIn("CRI-O minor must match the kubelet minor", detail)

    def test_only_containerd_and_cri_o_are_supported_runtimes(self):
        with self.kubectl_node(node("worker-1", runtime="containerd://2.0.0")):
            self.assertTrue(gate.check_runtime("worker-1")[0])
        with self.kubectl_node(node("worker-1", runtime="docker://27.3.1")):
            ok, detail = gate.check_runtime("worker-1")
        self.assertFalse(ok)
        self.assertIn("only containerd and CRI-O are supported", detail)

    def test_endpoints_must_list_every_control_plane(self):
        cps = [{"status": {"addresses": [{"type": "InternalIP", "address": ip}]}} for ip in ("192.0.2.11", "192.0.2.12")]
        slices = [{"endpoints": [{"addresses": ["192.0.2.11"]}]}]
        with patch.object(gate, "items", side_effect=[cps, slices]):
            ok, detail = gate.check_endpoints()
        self.assertFalse(ok)
        self.assertIn("missing ['192.0.2.12']", detail)
        # A managed control plane has no control-plane nodes: endpoints are enough.
        with patch.object(gate, "items", side_effect=[[], slices]):
            self.assertTrue(gate.check_endpoints()[0])

    def test_readyz_with_an_empty_body_falls_back_to_livez(self):
        answers = {"/readyz": "", "/readyz?verbose": "", "/livez": "ok"}
        with patch.object(gate, "kubectl", side_effect=lambda *a, **k: answers[a[-1]]):
            ok, detail = gate.check_api()
        self.assertTrue(ok)
        self.assertIn("readyz body empty", detail)

    def test_another_cordoned_node_fails_but_the_node_being_upgraded_may_be(self):
        nodes = [node("worker-1", cordoned=True), node("worker-2")]
        with patch.object(gate, "items", return_value=nodes):
            self.assertTrue(gate.check_cordoned("worker-1")[0])
            self.assertEqual(gate.check_cordoned(None), (False, "cordoned: worker-1"))

    def test_critical_fails_only_what_was_fully_ready_in_the_baseline(self):
        now = [workload("data", "StatefulSet", "pg", 1, 0),
               workload("ingress", "Deployment", "ingress-nginx-controller", 2, 1)]
        with patch.object(gate, "items", return_value=now):
            ok, detail = gate.check_critical(BASELINE)
        self.assertFalse(ok)
        self.assertEqual(detail, "database data/pg 0/1")
        with patch.object(gate, "items", return_value=now[1:]):
            self.assertEqual(gate.check_critical(BASELINE), (False, "data/pg missing"))
        now[0] = workload("data", "StatefulSet", "pg", 1, 1)
        with patch.object(gate, "items", return_value=now):
            self.assertTrue(gate.check_critical(BASELINE)[0])

    def test_a_check_that_cannot_run_fails_and_no_baseline_skips_critical(self):
        args = SimpleNamespace(node=None, expect_version=None, pre=None)
        boom = subprocess.CalledProcessError(1, ["kubectl"], stderr="connection refused")
        with patch.object(gate, "check_api", side_effect=boom), \
                patch.object(gate, "check_endpoints", return_value=(True, "ok")), \
                patch.object(gate, "check_webhooks", return_value=(True, "ok")), \
                patch.object(gate, "check_storage", return_value=(True, "ok")), \
                patch.object(gate, "check_cordoned", return_value=(True, "ok")):
            ok, lines = gate.run(args)
        self.assertFalse(ok)
        self.assertTrue(lines[0].startswith("FAIL api-readyz: CalledProcessError"), lines[0])
        self.assertIn("SKIP critical: no --pre baseline", lines)
        self.assertFalse(any(l.split()[1].startswith("node-") for l in lines))

    def test_webhooks_fail_on_any_down_line_from_the_scan(self):
        scan = subprocess.CompletedProcess([], 1, "DOWN  ValidatingWebhookConfiguration/p v Fail service has no ready endpoint\n", "")
        with patch.object(gate, "kubectl", return_value='{"items": []}'), \
                patch.object(gate.subprocess, "run", return_value=scan):
            ok, detail = gate.check_webhooks()
        self.assertFalse(ok)
        self.assertIn("ValidatingWebhookConfiguration/p", detail)

    def test_storage_fails_on_attach_errors_and_double_attachments(self):
        vas = [{"spec": {"source": {"persistentVolumeName": "pv-a"}, "nodeName": "worker-1"},
                "status": {"attachError": {"message": "timed out"}}},
               {"spec": {"source": {"persistentVolumeName": "pv-b"}, "nodeName": "worker-1"}, "status": {}},
               {"spec": {"source": {"persistentVolumeName": "pv-b"}, "nodeName": "worker-2"}, "status": {}}]
        no_longhorn = subprocess.CalledProcessError(1, ["kubectl"])
        with patch.object(gate, "items", return_value=vas), patch.object(gate, "kubectl", side_effect=no_longhorn):
            ok, detail = gate.check_storage()
        self.assertFalse(ok)
        self.assertIn("pv-a attachError: timed out", detail)
        self.assertIn("pv-b has attachments on ['worker-1', 'worker-2']", detail)


class GateWithBaselineTests(unittest.TestCase):
    def test_a_missing_baseline_file_is_a_failed_check_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(node=None, expect_version=None, pre=Path(tmp))
            with patch.object(gate, "check_api", return_value=(True, "ok")), \
                    patch.object(gate, "check_endpoints", return_value=(True, "ok")), \
                    patch.object(gate, "check_webhooks", return_value=(True, "ok")), \
                    patch.object(gate, "check_storage", return_value=(True, "ok")), \
                    patch.object(gate, "check_cordoned", return_value=(True, "ok")):
                ok, lines = gate.run(args)
        self.assertFalse(ok)
        self.assertTrue(any(l.startswith("FAIL critical: FileNotFoundError") for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
