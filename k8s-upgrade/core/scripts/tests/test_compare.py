"""compare.py verdicts: the post baseline against the one prepare took."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compare  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "baseline"
TARGET = "v1.31.2"


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pre, self.post = Path(self.tmp.name) / "pre", Path(self.tmp.name) / "post"
        shutil.copytree(BASELINE, self.pre)
        shutil.copytree(BASELINE, self.post)
        self.edit("nodes.json", lambda d: [n["status"]["nodeInfo"].update(kubeletVersion=TARGET) for n in d["items"]])

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, name, change):
        path = self.post / name
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def edit_tsv(self, name, old, new):
        path = self.post / name
        path.write_text(path.read_text().replace(old, new))

    def compare(self, *extra):
        out = io.StringIO()
        argv = ["compare.py", str(self.pre), str(self.post), "--target", TARGET, *extra]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            rc = compare.main()
        text = out.getvalue()
        return rc, [l for l in text.splitlines() if "REGRESSION" in l], [l for l in text.splitlines() if "WARNING" in l], text

    def pods(self, d):
        return {p["metadata"]["name"]: p for p in d["items"]}

    def test_an_unchanged_cluster_on_the_target_passes(self):
        rc, reg, warn, text = self.compare()
        self.assertEqual((rc, reg), (0, []))
        self.assertIn("RESULT PASS", text)
        # Pre-existing problems stay warnings: the crash-looping pod and the half-ready ingress.
        self.assertTrue(any("CrashLoopBackOff (pre-existing)" in w for w in warn), warn)
        self.assertTrue(any("ingress-nginx-controller 1/2 ready (pre-existing" in w for w in warn), warn)

    def test_a_node_left_behind_or_cordoned_is_a_regression(self):
        def change(d):
            d["items"][1]["status"]["nodeInfo"]["kubeletVersion"] = "v1.30.4"
            d["items"][0]["spec"]["unschedulable"] = True
        self.edit("nodes.json", change)
        rc, reg, _, _ = self.compare()
        self.assertEqual(rc, 1)
        self.assertIn("- REGRESSION node worker-1 kubelet v1.30.4, expected v1.31.2", reg)
        self.assertIn("- REGRESSION node cp-1 still cordoned", reg)

    def test_a_missing_or_not_ready_node_is_a_regression(self):
        def change(d):
            d["items"][0]["status"]["conditions"][0]["status"] = "False"
            del d["items"][1]
        self.edit("nodes.json", change)
        _, reg, _, _ = self.compare()
        self.assertIn("- REGRESSION node cp-1 not Ready", reg)
        self.assertIn("- REGRESSION node worker-1 missing", reg)

    def test_pods_renamed_by_a_drain_are_matched_by_their_workload(self):
        def change(d):
            for p in d["items"]:
                if p["metadata"]["name"].startswith("web-"):
                    p["metadata"]["name"] = p["metadata"]["name"].replace("6d4cf56db6", "8b5f7c9d4a")
                    p["metadata"]["labels"]["pod-template-hash"] = "8b5f7c9d4a"
                    p["metadata"]["ownerReferences"][0]["name"] = "web-8b5f7c9d4a"
        self.edit("pods.json", change)
        rc, reg, _, _ = self.compare()
        self.assertEqual((rc, reg), (0, []))

    def test_a_new_pending_pod_in_a_workload_is_a_regression(self):
        def change(d):
            pending = json.loads(json.dumps(self.pods(d)["web-6d4cf56db6-abcde"]))
            pending["metadata"]["name"] = "web-6d4cf56db6-zzzzz"
            pending["status"] = {"phase": "Pending"}
            d["items"].append(pending)
        self.edit("pods.json", change)
        rc, reg, _, _ = self.compare()
        self.assertEqual(rc, 1)
        self.assertIn("- REGRESSION default/Deployment/web 1 pod(s) Pending (none before)", reg)

    def test_fewer_ready_pods_and_a_new_crash_loop_are_regressions(self):
        def change(d):
            pg = self.pods(d)["pg-0"]
            pg["status"]["conditions"][0]["status"] = "False"
            pg["status"]["containerStatuses"][0]["state"] = {"waiting": {"reason": "CrashLoopBackOff"}}
        self.edit("pods.json", change)
        _, reg, _, _ = self.compare()
        self.assertTrue(any(r.startswith("- REGRESSION data/StatefulSet/pg ready pods 1 -> 0") for r in reg), reg)
        self.assertIn("- REGRESSION data/StatefulSet/pg CrashLoopBackOff (new)", reg)

    def test_a_workload_with_no_pods_now_is_a_regression_and_completed_jobs_are_ignored(self):
        self.edit("pods.json", lambda d: d.update(items=[p for p in d["items"] if p["metadata"]["namespace"] != "data"
                                                         and p["metadata"]["namespace"] != "batch"]))
        _, reg, _, _ = self.compare()
        self.assertIn("- REGRESSION data/StatefulSet/pg has no pods now (had 1 ready)", reg)
        self.assertFalse(any("batch/" in r for r in reg), reg)

    def test_a_critical_workload_ready_before_and_not_now_is_a_regression(self):
        self.edit("critical.json", lambda d: d[0].update(ready=0))
        rc, reg, _, _ = self.compare()
        self.assertEqual(rc, 1)
        self.assertIn("- REGRESSION critical database data/StatefulSet/pg 0/1 ready", reg)

    def test_a_pvc_no_longer_bound_and_a_lost_pv_are_regressions(self):
        self.edit("pvcs.json", lambda d: d["items"][0]["status"].update(phase="Lost"))
        self.edit("pvs.json", lambda d: d.update(items=[]))
        _, reg, _, _ = self.compare()
        self.assertIn("- REGRESSION pvc data/data-pg-0 Lost", reg)
        self.assertIn("- REGRESSION PVs 1 -> 0", reg)

    def test_database_rows_are_matched_across_a_new_deployment_pod_name(self):
        self.edit_tsv("db_stats.tsv", "cache-5b7d9c6f8d-k2j4m", "cache-7c8d9e6f5a-p9q8r")
        rc, reg, _, _ = self.compare()
        self.assertEqual((rc, reg), (0, []))

    def test_a_database_no_longer_measurable_or_missing_is_a_regression(self):
        self.edit_tsv("db_stats.tsv", "pg-0\tpostgres\tpostgres\tapp\t104857600\tbytes\tok",
                      "pg-0\tpostgres\tpostgres\t-\t\t\tauth-failed")
        self.edit_tsv("db_stats.tsv", "\tdb0\t5000\tkeys\tok\n", "\tdb1\t5000\tkeys\tok\n")
        _, reg, _, _ = self.compare()
        self.assertIn("- REGRESSION db data/pg-0 postgres app: not measurable now (auth-failed)", reg)
        self.assertIn("- REGRESSION db default/cache-5b7d9c6f8d-k2j4m redis db0: missing", reg)

    def test_a_database_smaller_by_more_than_ten_percent_is_a_warning(self):
        self.edit_tsv("db_stats.tsv", "\t104857600\t", "\t90000000\t")
        rc, reg, warn, _ = self.compare()
        self.assertEqual((rc, reg), (0, []))
        self.assertIn("- WARNING db data/pg-0 postgres app: 104857600 -> 90000000 bytes (-15%)", warn)

    def test_pvc_usage_warns_only_on_a_drop_over_ten_percent_and_100_mib(self):
        self.edit_tsv("pvc_usage.tsv", "\t2147483648\t", "\t2100000000\t")
        self.assertFalse(any("pvc usage" in w for w in self.compare()[2]))
        self.edit_tsv("pvc_usage.tsv", "\t2100000000\t", "\t1000000000\t")
        rc, _, warn, _ = self.compare()
        self.assertEqual(rc, 0)
        self.assertIn("- WARNING pvc usage data/data-pg-0: 2147483648 -> 1000000000 bytes", warn)

    def test_out_writes_the_same_report(self):
        report = Path(self.tmp.name) / "compare.md"
        _, _, _, text = self.compare("--out", str(report))
        self.assertEqual(report.read_text(), text)


class PodStateTests(unittest.TestCase):
    def test_state_reports_the_waiting_reason_before_the_phase(self):
        pod = {"metadata": {}, "status": {"phase": "Pending", "initContainerStatuses": [
            {"state": {"waiting": {"reason": "PodInitializing"}}}]}}
        self.assertEqual(compare.pod_state(pod), "Init:PodInitializing")
        self.assertTrue(compare.BAD_STATES.match(compare.pod_state(pod)))
        pod = {"metadata": {"deletionTimestamp": "x"}, "status": {"phase": "Running"}}
        self.assertEqual(compare.pod_state(pod), "Terminating")

    def test_static_pods_and_bare_pods_keep_their_own_names(self):
        static = {"metadata": {"namespace": "kube-system", "name": "etcd-cp-1",
                               "ownerReferences": [{"kind": "Node", "name": "cp-1"}]}}
        bare = {"metadata": {"namespace": "tools", "name": "debug"}}
        self.assertEqual(compare.workload(static), "kube-system/StaticPod/etcd-cp-1")
        self.assertEqual(compare.workload(bare), "tools/Pod/debug")


if __name__ == "__main__":
    unittest.main()
