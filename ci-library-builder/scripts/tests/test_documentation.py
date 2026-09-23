"""Regression cases for split documentation and legacy README contracts."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from documentation import REQUIRED_TOPICS, check_documentation, links, local_target, parse_markdown


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "github-ci-library"
        self.root.mkdir()
        self.write("CHANGELOG.md", "# Changelog\n")
        self.write("MIGRATION.md", "# Migration\nNo migration required.\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def topics(self):
        return "\n".join("## " + topic for topic in REQUIRED_TOPICS) + "\n"

    def test_legacy_monolithic_readme(self):
        self.write("README.md", "# Library\n" + self.topics())
        self.assertEqual(check_documentation(self.root), [])

    def test_split_docs_nested_links_duplicate_anchors_and_reference_links(self):
        self.write("README.md", "# Library\n[Topics][topics]\n\n[topics]: docs/catalog.md\n")
        self.write("docs/catalog.md", "# Catalog\n" + self.topics() + "[Example](examples/job.md#same-1)\n")
        self.write("docs/examples/job.md", "# Example\n## Same\n## Same\n[Catalog](../catalog.md)\n[Root](../../README.md)\n")
        self.assertEqual(check_documentation(self.root), [])

    def test_broken_nested_anchor_and_missing_page_are_findings(self):
        self.write("README.md", "# Library\n" + self.topics() + "[Job](docs/job.md)\n")
        self.write("docs/job.md", "# Job\n[Bad](../README.md#missing)\n[Missing](absent.md)\n")
        messages = [f["message"] for f in check_documentation(self.root)]
        self.assertTrue(any("Broken anchor" in m for m in messages))
        self.assertTrue(any("Broken local link" in m for m in messages))

    def test_unreachable_topics_do_not_satisfy_contract(self):
        self.write("README.md", "# Library\n")
        self.write("docs/orphan.md", "# Orphan\n" + self.topics())
        messages = [f["message"] for f in check_documentation(self.root)]
        self.assertTrue(any("unreachable" in m for m in messages))
        self.assertTrue(any("No reachable" in m for m in messages))

    def test_fences_do_not_create_headings_or_document_links(self):
        text = "# Example\n````markdown\n# False\n[Bad](absent.md)\n```yaml\na: b\n```\n````\n"
        prose, anchors, blocks, errors = parse_markdown(text)
        self.assertEqual(anchors, {"example"})
        self.assertEqual(list(links(prose)), [])
        self.assertEqual(errors, [])
        self.assertEqual(len(blocks), 1)

    def test_yaml_checked_in_moved_pages_and_gitlab_reference_is_safe(self):
        self.write("README.md", "# Library\n[Examples](docs/examples.md)\n")
        self.write("docs/examples.md", "# Examples\n```yaml\nscript: !reference [.build, script]\n```\n```yaml\nbroken: [\n```\n")
        findings = check_documentation(self.root, contract=False)
        self.assertEqual(len(findings), 1)
        self.assertIn("Invalid YAML", findings[0]["message"])

    def test_unclosed_fence_reports_source(self):
        self.write("README.md", "# Library\n```yaml\na: b\n")
        findings = check_documentation(self.root, contract=False)
        self.assertEqual(findings[0]["where"], "README.md:2")

    def test_workflow_references_use_the_library_not_consumer_filenames(self):
        self.write("README.md", "# Library\n[Example](docs/example.md)\n")
        self.write("docs/example.md", "# Example\n```yaml\n# .github/workflows/pr.yml\njobs:\n  lint:\n    uses: grootan-devops/github-ci-library/.github/workflows/missing.yml@1.0.0\n```\n")
        findings = check_documentation(self.root, contract=False)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing.yml", findings[0]["message"])

    def test_relative_link_resolution_does_not_fetch_remote_pages(self):
        page = self.write("docs/modules/job.md", "# Job\n")
        self.assertEqual(local_target(page, "../config.md#key"), (self.root / "docs/config.md", "key"))
        self.assertIsNone(local_target(page, "https://example.com/docs.md#key"))

    def test_workflow_coverage_survives_a_renamed_checkout(self):
        self.write("README.md", "# Library\n" + self.topics())
        self.write(".github/workflows/lint.yml", "name: Lint\n")
        renamed = self.root.with_name("arbitrary-checkout-name")
        self.root.rename(renamed)
        findings = check_documentation(renamed)
        self.assertEqual(len(findings), 1)
        self.assertIn("Workflow 'lint.yml' is undocumented", findings[0]["message"])

    def test_required_gitlab_needs_are_explicit_not_weakened(self):
        spec = importlib.util.spec_from_file_location("gitlab_verifier", SCRIPTS / "verify-gitlab-library.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = self.write("chart/.gitlab-ci.yml", "stages: [init, release]\nCommon:Init:\n  stage: init\n  rules: [{when: on_success}]\nChart:Promote:\n  stage: release\n  rules: [{when: on_success}]\n  needs:\n    - job: Common:Init\n      artifacts: true\n")
        self.assertTrue(any(f.rule == "needs-missing-optional" for f in module.check(self.root)))
        path.write_text(path.read_text() + "      optional: false\n")
        self.assertFalse(any(f.sev == "P0" for f in module.check(self.root)))
        self.assertIs(module.load(path)["Chart:Promote"]["needs"][0]["optional"], False)


if __name__ == "__main__":
    unittest.main()
