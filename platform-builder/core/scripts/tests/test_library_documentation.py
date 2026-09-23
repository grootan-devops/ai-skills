"""Source/ref and reading-boundary scenarios; no network, checkouts or commits."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import libraries


class LibraryDocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "README.md").write_text("# Library\n[Chart](docs/chart.md)\n")
        (self.root / "docs").mkdir()
        (self.root / "docs/chart.md").write_text("# Chart\nUncommitted local guidance.\n")
        (self.root / "docs/unrelated.md").write_text("Not relevant to this task.\n")

    def test_configured_defaults_use_main(self):
        with patch.dict(os.environ, {}, clear=True):
            for name in ("gitlab-ci-library", "github-ci-library", "helm-tpl-library"):
                spec, origin = libraries.resolve_spec(name, None, {})
                self.assertEqual(libraries.parse_source(spec).ref, "main")
                self.assertIn("default", origin)

    def test_explicit_ref_overrides_environment_and_default(self):
        name = "github-ci-library"
        selected = "https://github.com/grootan-devops/github-ci-library/tree/docs/1.2.0"
        with patch.dict(os.environ, {"PLATFORM_BUILDER_LIB_GITHUB_CI_LIBRARY": "https://example.com/library.git@dev"}):
            spec, origin = libraries.resolve_spec(name, self.root, {name: selected})
        self.assertEqual(origin, "command line")
        self.assertEqual(libraries.parse_source(spec).ref, "docs/1.2.0")

    def test_local_docs_read_in_place_without_preloading_topics(self):
        with patch.object(libraries, "_describe_local") as describe:
            resolved = libraries.resolve_one("github-ci-library", str(self.root), "command line")
        describe.assert_called_once()
        self.assertTrue(resolved.ok)
        self.assertEqual(Path(resolved.path), self.root)
        self.assertEqual(set(resolved.docs), {"README.md"})
        selected = Path(resolved.docs["README.md"]).parent / "docs/chart.md"
        self.assertIn("Uncommitted", selected.read_text())
        selected.write_text("# Chart\nAn additional local edit.\n")
        self.assertIn("additional local edit", selected.read_text())

    def test_remote_pages_stay_in_the_selected_ref_checkout(self):
        source = "https://github.com/grootan-devops/github-ci-library.git@1.2.0"
        with patch.object(libraries, "_cache_dir", return_value=self.root), patch.object(libraries, "_clone") as clone, patch.object(libraries, "_git", return_value=subprocess.CompletedProcess([], 0, "a" * 40)):
            resolved = libraries.resolve_one("github-ci-library", source, "command line")
        self.assertEqual(clone.call_args.args[0].ref, "1.2.0")
        self.assertEqual(resolved.ref, "1.2.0")
        self.assertEqual(Path(resolved.path) / "docs/chart.md", self.root / "docs/chart.md")

    def test_legacy_readme_without_docs_links_remains_usable(self):
        (self.root / "README.md").write_text("# Library\n## Chart\nLegacy inlined contract.\n")
        with patch.object(libraries, "_describe_local"):
            resolved = libraries.resolve_one("helm-tpl-library", str(self.root), "command line")
        self.assertTrue(resolved.ok)
        self.assertIn("Legacy", Path(resolved.docs["README.md"]).read_text())
        self.assertIn("index", libraries.render([resolved]))

    def test_branch_tag_and_commit_ignore_readme_example_pin(self):
        (self.root / "README.md").write_text("# Library\nExample: workflow.yml@1.0.0\n[Chart](docs/chart.md)\n")
        for ref in ("docs/1.2.0", "1.2.0", "a" * 40):
            with self.subTest(ref=ref), patch.object(libraries, "_cache_dir", return_value=self.root), patch.object(libraries, "_clone") as clone, patch.object(libraries, "_git", return_value=subprocess.CompletedProcess([], 0, "b" * 40)):
                resolved = libraries.resolve_one("github-ci-library", "https://example.com/library.git@" + ref, "command line")
                self.assertTrue(resolved.ok)
                self.assertEqual(resolved.ref, ref)
                self.assertEqual(clone.call_args.args[0].ref, ref)
                self.assertEqual(resolved.commit, "b" * 40)
                self.assertEqual(Path(resolved.docs["README.md"]).parent / "docs/chart.md", self.root / "docs/chart.md")

    def test_unqualified_url_reports_remote_default_branch_including_slashes(self):
        def git(args):
            output = "origin/release/main\n" if "symbolic-ref" in args else "c" * 40
            return subprocess.CompletedProcess(args, 0, output)

        with patch.object(libraries, "_cache_dir", return_value=self.root), patch.object(libraries, "_clone") as clone, patch.object(libraries, "_git", side_effect=git):
            resolved = libraries.resolve_one("github-ci-library", "https://example.com/library.git", "command line")
        self.assertTrue(resolved.ok)
        self.assertIsNone(clone.call_args.args[0].ref)
        self.assertEqual(resolved.ref, "release/main")
        self.assertEqual(resolved.ref_kind, "default-branch")

    def test_cached_unqualified_source_checks_out_remote_default_not_main(self):
        commands = []

        def git(args):
            commands.append(args)
            output = "origin/trunk\n" if "symbolic-ref" in args else ""
            return subprocess.CompletedProcess(args, 0, output)

        with patch.object(libraries, "_git", side_effect=git):
            libraries._clone(libraries.parse_source("https://example.com/library.git"), self.root, False)
        self.assertIn(["-C", str(self.root), "reset", "--hard", "origin/trunk"], commands)

    def test_local_ref_is_rejected_without_switching_branches(self):
        with patch.object(libraries, "_describe_local") as describe:
            resolved = libraries.resolve_one("helm-tpl-library", str(self.root) + "@dev", "command line")
        self.assertFalse(resolved.ok)
        describe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
