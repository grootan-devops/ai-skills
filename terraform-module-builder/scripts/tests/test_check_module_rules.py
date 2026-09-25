"""Focused regressions for Terraform module rule findings."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "check-module-rules.py"
SPEC = importlib.util.spec_from_file_location("check_module_rules", SCRIPT)
RULES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RULES)


class ModuleRulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.module = Path(self.temp.name)

    def findings_for_output(self, source):
        (self.module / "outputs.tf").write_text(source)
        findings = []
        RULES.check_output_sensitive_hygiene(self.module, findings)
        return findings

    def test_sensitive_output_accepts_true_after_nested_value(self):
        findings = self.findings_for_output('''output "api_token" {
  value = {
    token = "{not a block}"
  }
  sensitive = true
}
''')
        self.assertEqual(findings, [])
        self.assertEqual(self.findings_for_output(
            'output "api_token" {\n  value = "secret"\n  sensitive = true // reviewed\n}\n'), [])

    def test_sensitive_output_rejects_false_or_missing_marker(self):
        for marker in ("sensitive = false", "# sensitive = true", ""):
            with self.subTest(marker=marker):
                findings = self.findings_for_output(
                    f'output "api_token" {{\n  value = "secret"\n  {marker}\n}}\n')
                self.assertEqual([finding["category"] for finding in findings],
                                 ["Output Security"])

    def test_conditional_governance_tags_cannot_be_overridden(self):
        (self.module / "locals.tf").write_text('''locals {
  tags = merge(
    var.application != "" ? { Application = var.application } : {},
    var.environment != "" ? { Environment = var.environment } : {},
    { Name = local.rendered_name },
    var.tags
  )
}
''')
        findings = []
        RULES.check_tag_governance(self.module, findings)
        self.assertEqual([finding["category"] for finding in findings],
                         ["Tag Governance"])

        (self.module / "locals.tf").write_text('''locals {
  tags = merge(var.tags, local.governance_tags)
}
''')
        findings = []
        RULES.check_tag_governance(self.module, findings)
        self.assertEqual(findings, [])

    def test_minimum_provider_constraint_is_not_stale(self):
        self.assertEqual(RULES.check_constraint_against_latest(">= 6.0.0", "6.66.0"), [])
        capped = RULES.check_constraint_against_latest(">= 6.0.0, < 7.0.0", "6.66.0")
        self.assertEqual([severity for severity, _ in capped], ["P2"])
        self.assertIn("upper constraint", capped[0][1])
        self.assertEqual(RULES.check_constraint_against_latest(">= 7.0.0", "6.66.0")[0][0],
                         "P1")
        self.assertEqual(RULES.check_constraint_against_latest("~> 6.0", "6.66.0")[0][0],
                         "P2")

    def test_unused_data_sources_are_checked_in_any_tf_file(self):
        (self.module / "iam.tf").write_text('data "aws_caller_identity" "current" {}\n')
        findings = []
        RULES.check_unused_context_data_sources(self.module, findings)
        self.assertEqual([finding["category"] for finding in findings],
                         ["Unused Context Query"])
        self.assertEqual(findings[0]["file"], str(self.module / "iam.tf"))

        (self.module / "locals.tf").write_text(
            'locals { account = data.aws_caller_identity.current.account_id }\n'
        )
        findings = []
        RULES.check_unused_context_data_sources(self.module, findings)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
