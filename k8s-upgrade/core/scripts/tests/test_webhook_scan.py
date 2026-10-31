"""webhook_scan.py: which failurePolicy Fail webhooks can lock the API server out of its Service."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webhook_scan  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "webhooks.json"


class WebhookScanTests(unittest.TestCase):
    def scan(self, ready=lambda svc: True, *extra):
        out = io.StringIO()
        with patch.object(sys, "argv", ["webhook_scan.py", *extra]), \
                patch.object(sys, "stdin", io.StringIO(FIXTURE.read_text())), \
                patch.object(webhook_scan, "service_ready", side_effect=ready), redirect_stdout(out):
            rc = webhook_scan.main()
        status = {l.split()[1].split("/", 1)[1]: l.split()[0] for l in out.getvalue().splitlines() if "/" in l.split()[1]}
        return rc, status, out.getvalue()

    def test_fail_webhooks_that_intercept_the_kubernetes_service_are_risk(self):
        rc, status, _ = self.scan()
        self.assertEqual(rc, 1)
        self.assertEqual(status["catch-all-policy"], "RISK")

    def test_a_webhook_without_failure_policy_counts_as_fail(self):
        _, status, out = self.scan()
        self.assertEqual(status["default-policy"], "RISK")
        self.assertIn("endpointslices in default", out)

    def test_ignore_policy_exemptions_selectors_and_delete_rules_are_ok_while_backends_are_up(self):
        _, status, _ = self.scan()
        for name in ("ignore-policy", "exempts-apiserver", "skips-system-namespaces", "deletes-only"):
            self.assertEqual(status[name], "OK", name)

    def test_a_fail_webhook_whose_service_has_no_ready_endpoint_is_down(self):
        _, status, out = self.scan(lambda svc: svc["name"] != "exempt-webhook")
        self.assertEqual(status["exempts-apiserver"], "DOWN")
        self.assertIn("service has no ready endpoint", out)
        # Ignore webhooks are never probed, so their backend state does not matter.
        _, status, _ = self.scan(lambda svc: False)
        self.assertEqual(status["ignore-policy"], "OK")

    def test_out_writes_every_webhook_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "webhooks.json"
            self.scan(lambda svc: True, "--out", str(target))
            results = json.loads(target.read_text())
        self.assertEqual(len(results), 6)
        risk = next(r for r in results if r["configuration"] == "catch-all-policy")
        self.assertEqual(risk["intercepts"], ["endpoints in default", "endpointslices in default",
                                              "services in default", "leases in kube-system"])

    def test_namespace_selectors_are_evaluated_against_the_namespace_name_label(self):
        labels = {"kubernetes.io/metadata.name": "default"}
        self.assertTrue(webhook_scan.selector_matches(None, labels))
        self.assertTrue(webhook_scan.selector_matches({"matchExpressions": [
            {"key": "kubernetes.io/metadata.name", "operator": "In", "values": ["default"]}]}, labels))
        self.assertFalse(webhook_scan.selector_matches({"matchLabels": {"team": "apps"}}, labels))
        self.assertFalse(webhook_scan.selector_matches({"matchExpressions": [
            {"key": "kubernetes.io/metadata.name", "operator": "Exists"}, {"key": "team", "operator": "Exists"}]}, labels))


if __name__ == "__main__":
    unittest.main()
