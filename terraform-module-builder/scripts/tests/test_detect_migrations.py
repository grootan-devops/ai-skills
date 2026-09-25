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

    def detect(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.module), *args],
                              cwd=self.root.parent, capture_output=True, text=True)

    def test_unchanged_module_is_patch_from_another_directory(self):
        result = self.detect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PATCH / NO API CHANGES", result.stdout)

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


if __name__ == "__main__":
    unittest.main()
