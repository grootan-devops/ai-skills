import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.libraries = load_module(
            "platform_libraries",
            ROOT / "platform-builder/core/scripts/libraries.py",
        )
        cls.gitops = load_module(
            "gitops_helper",
            ROOT / "gitops-app-manager/scripts/gitops-helper.py",
        )

    def test_release_metadata_is_consistent(self):
        self.assertEqual("1.0.0", (ROOT / "VERSION").read_text().strip())
        self.assertIn("[1.0.0]", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("No migration is required", (ROOT / "MIGRATION.md").read_text())

    def test_default_first_party_libraries_are_pinned(self):
        config = json.loads(
            (ROOT / "platform-builder/core/libraries.json").read_text()
        )
        for name, entry in config["libraries"].items():
            with self.subTest(name=name):
                self.assertEqual("1.0.0", entry["ref"])

    def test_source_parser_preserves_git_identity_and_ref(self):
        parsed = self.libraries.parse_source(
            "https://github.com/grootan-devops/github-ci-library.git@1.0.0"
        )
        self.assertEqual("git", parsed.kind)
        self.assertEqual("1.0.0", parsed.ref)
        self.assertEqual(
            "https://github.com/grootan-devops/github-ci-library.git",
            parsed.location,
        )

    def test_environment_aliases_and_branch_matching(self):
        self.assertEqual("dev", self.gitops.normalize_env("Development"))
        match, suggestions = self.gitops.match_branch_similarity(
            "payments/development", ["payments/dev", "payments/prod"]
        )
        self.assertEqual("payments/dev", match)
        self.assertEqual([], suggestions)


if __name__ == "__main__":
    unittest.main()
