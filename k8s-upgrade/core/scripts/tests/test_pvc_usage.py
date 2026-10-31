"""pvc_usage.py: kubelet stats first, df inside a mounting pod next, unmounted otherwise."""
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pvc_usage  # noqa: E402


def pvc(ns, name):
    return {"metadata": {"namespace": ns, "name": name}}


def mounting_pod(ns, name, node, claim, phase="Running"):
    return {"metadata": {"namespace": ns, "name": name},
            "spec": {"nodeName": node, "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim}}],
                     "containers": [{"name": "app", "volumeMounts": [{"name": "data", "mountPath": "/data"}]}]},
            "status": {"phase": phase}}


PVCS = [pvc("data", "pg"), pvc("data", "search"), pvc("data", "broken"), pvc("data", "idle"), pvc("data", "stopped")]
PODS = [mounting_pod("data", "pg-0", "worker-1", "pg"),
        mounting_pod("data", "search-0", "worker-2", "search"),
        mounting_pod("data", "broken-0", "worker-2", "broken"),
        mounting_pod("data", "stopped-0", "worker-2", "stopped", phase="Succeeded")]
SUMMARY = {"pods": [{"volume": [{"pvcRef": {"namespace": "data", "name": "pg"}, "usedBytes": 4096,
                                 "capacityBytes": 10240}]}]}
DF = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sdb 1000 250 750 25% /data\n"


def fake_kubectl(*args, timeout=60):
    if args[:2] == ("get", "pvc"):
        return json.dumps({"items": PVCS})
    if args[:2] == ("get", "pods"):
        return json.dumps({"items": PODS})
    if args[:2] == ("get", "nodes"):
        return json.dumps({"items": [{"metadata": {"name": "worker-1"}}, {"metadata": {"name": "worker-2"}}]})
    if args[:2] == ("get", "--raw"):
        if "worker-2" in args[2]:
            raise subprocess.CalledProcessError(1, "kubectl")
        return json.dumps(SUMMARY)
    if args[0] == "exec":
        if "broken-0" in args:
            raise subprocess.CalledProcessError(126, "kubectl")
        return DF
    raise AssertionError(args)


class PvcUsageTests(unittest.TestCase):
    def test_each_pvc_is_measured_from_the_best_source_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with patch.object(sys, "argv", ["pvc_usage.py", tmp]), patch.object(pvc_usage, "kubectl", side_effect=fake_kubectl), \
                    redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(pvc_usage.main(), 0)
            with open(Path(tmp) / "pvc_usage.tsv", encoding="utf-8") as f:
                rows = {r["pvc"]: r for r in csv.DictReader(f, delimiter="\t")}
        self.assertEqual((rows["pg"]["used_bytes"], rows["pg"]["capacity_bytes"], rows["pg"]["source"]),
                         ("4096", "10240", "kubelet-stats"))
        # df reports 1024-byte blocks.
        self.assertEqual((rows["search"]["used_bytes"], rows["search"]["capacity_bytes"], rows["search"]["source"]),
                         (str(250 * 1024), str(1000 * 1024), "df"))
        self.assertEqual((rows["broken"]["used_bytes"], rows["broken"]["source"]), ("", "unmeasured"))
        self.assertEqual((rows["idle"]["pod"], rows["idle"]["source"]), ("", "unmounted"))
        self.assertEqual(rows["stopped"]["source"], "unmounted")
        self.assertIn("WARNING stats summary unavailable for worker-2", err.getvalue())


if __name__ == "__main__":
    unittest.main()
