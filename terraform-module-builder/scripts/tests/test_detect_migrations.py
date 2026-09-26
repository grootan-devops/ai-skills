"""Regression checks for Git-baseline and resource-address migration detection."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "detect-migrations.py"


class DetectMigrationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / "modules/sample"
        self.module.mkdir(parents=True)
        (self.module / "variables.tf").write_text('variable "name" {\n  type = string\n}\n')
        (self.module / "outputs.tf").write_text('output "id" {\n  value = test_item.old.id\n}\n')
        (self.module / "main.tf").write_text('resource "test_item" "old" {\n}\n')
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "baseline")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True)

    def detect(self, *args, path=None):
        return subprocess.run([sys.executable, str(SCRIPT), str(path or self.module), *args],
                              cwd=self.root.parent, capture_output=True, text=True)

    def test_unchanged_module_reports_limits_from_another_directory(self):
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO PUBLIC API OR RESOURCE ADDRESS CHANGES", result.stdout)
        self.assertIn("PATCH is only a candidate", result.stdout)

    def test_linked_worktree_uses_its_own_git_baseline(self):
        linked = self.root.parent / f"{self.root.name}-worktree"
        self.git("worktree", "add", "-q", "-b", "test-worktree", str(linked))
        self.addCleanup(lambda: self.git("worktree", "remove", "--force", str(linked)))
        result = self.detect(path=linked / "modules/sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO PUBLIC API OR RESOURCE ADDRESS CHANGES", result.stdout)

    def test_module_inside_submodule_uses_submodule_baseline(self):
        parent_temp = tempfile.TemporaryDirectory()
        self.addCleanup(parent_temp.cleanup)
        parent = Path(parent_temp.name)
        subprocess.run(["git", "init", "-q", str(parent)], check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(parent), "-c", "protocol.file.allow=always",
                        "submodule", "add", "-q", str(self.root), "deps/library"],
                       check=True, capture_output=True, text=True)
        result = self.detect(path=parent / "deps/library/modules/sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO PUBLIC API OR RESOURCE ADDRESS CHANGES", result.stdout)

    def test_removed_resource_address_requires_migration_review(self):
        (self.module / "main.tf").write_text("")
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Removed resource address: 'test_item.old'", result.stdout)
        self.assertIn("possible destruction", result.stdout)
        self.assertIn("MAJOR BUMP REQUIRED", result.stdout)

    def test_moved_block_covers_address_but_still_requires_state_review(self):
        (self.module / "main.tf").write_text('resource "test_item" "new" {\n}\n')
        (self.module / "moved.tf").write_text(
            "moved {\n  from = test_item.old\n  to = test_item.new\n}\n")
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has a moved block; verify it against consumer state", result.stdout)
        self.assertNotIn("MAJOR BUMP REQUIRED", result.stdout)

    def test_invalid_baseline_fails_instead_of_reporting_new_api(self):
        result = self.detect("--compare-ref", "missing-ref")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read Git baseline", result.stderr)

    def test_default_change_needs_plan_review_even_without_api_change(self):
        (self.module / "main.tf").write_text(
            'resource "terraform_data" "old" {\n  triggers_replace = var.name\n}\n')
        (self.module / "variables.tf").write_text(
            'variable "name" {\n  type = string\n  default = "old"\n}\n')
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "optional baseline")
        (self.module / "variables.tf").write_text(
            'variable "name" {\n  type = string\n  default = "new"\n}\n')
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("does not assess changed defaults", result.stdout)
        self.assertNotIn("[CLEAN", result.stdout)

    def test_instance_key_change_needs_plan_review_even_when_label_is_stable(self):
        (self.module / "main.tf").write_text(
            'resource "test_item" "old" {\n  for_each = var.name\n}\n')
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("count/for_each instance keys", result.stdout)
        self.assertNotIn("[CLEAN", result.stdout)


if __name__ == "__main__":
    unittest.main()
