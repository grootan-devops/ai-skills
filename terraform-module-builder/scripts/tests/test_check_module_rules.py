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

    def test_identifier_outputs_are_not_credentials(self):
        for name in ("master_user_secret_arn", "secret_arns", "token_id", "secret_name"):
            with self.subTest(name=name):
                self.assertEqual(self.findings_for_output(
                    f'output "{name}" {{\n  value = "x"\n}}\n'), [])

    def dead_variables(self, variables, other=""):
        (self.module / "variables.tf").write_text(variables)
        if other:
            (self.module / "main.tf").write_text(other)
        findings = []
        RULES.check_dead_variables(self.module, findings)
        return sorted(finding["message"].split("'")[1] for finding in findings)

    def test_variable_used_in_another_validation_is_not_dead(self):
        variables = '''variable "allow_public" {
  type    = bool
  default = false
}

variable "statements" {
  type = list(string)

  validation {
    condition     = var.allow_public || length(var.statements) == 0
    error_message = "statements: public needs allow_public."
  }
}
'''
        self.assertEqual(self.dead_variables(variables, 'locals { s = var.statements }\n'), [])

    def test_variable_used_only_in_its_own_validation_is_dead(self):
        variables = '''variable "orphan" {
  type = string

  validation {
    condition     = length(var.orphan) > 0
    error_message = "orphan: required."
  }
}
'''
        self.assertEqual(self.dead_variables(variables), ["orphan"])

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

    def test_alternative_provider_must_exist_in_the_registry(self):
        original = RULES.query_provider_info
        self.addCleanup(setattr, RULES, "query_provider_info", original)
        RULES.query_provider_info = lambda source: ("official", None, 0, "")
        self.assertIsNone(RULES.find_alternative_provider("oneuptime"))
        RULES.query_provider_info = lambda source: ("official", "1.2.0", 10, "")
        self.assertEqual(RULES.find_alternative_provider("example")[0], "hashicorp/example")

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
