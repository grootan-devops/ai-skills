"""Which library release a run reads: the project's pin, else the latest release tag.

Runs against a real git repository tagged like a library, served as a git source, so the
ls-remote, shallow-clone and data-file paths are the real ones. No network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import libraries  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, **_ENV})


def _git_source(spec: str) -> libraries.Source:
    """A local bare repository read the way a remote one is: cloned, at a ref."""
    location, ref = libraries._split_ref(spec)
    return libraries.Source(spec, location, ref, "git")


class LibraryResolutionTests(unittest.TestCase):
    """Tags 1.0.0 (no map), 1.1.0, v1.1.5, 1.2.0-rc.1 (a candidate) and 1.2.0."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        work = root / "work"
        work.mkdir()
        _git(work, "init", "-q", "-b", "main")
        (work / "README.md").write_text("# Library\n")
        sections = []
        for tag, schema in (("1.0.0", None), ("1.1.0", 1), ("v1.1.5", 1), ("1.2.0-rc.1", 1), ("1.2.0", 1)):
            if schema is not None:
                (work / "docs").mkdir(exist_ok=True)
                (work / "docs" / "workflow-map.json").write_text(json.dumps({"schema": schema}) + "\n")
            sections.insert(0, f"## {tag.lstrip('v')}\n\nNotes for {tag}.\n")
            (work / "MIGRATION.md").write_text("# Migration Guide\n\n" + "\n".join(sections))
            _git(work, "add", "-A")
            _git(work, "commit", "-q", "-m", tag)
            _git(work, "tag", tag)
        cls.remote = root / "library.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(cls.remote)], check=True)
        cls.location = f"file://{cls.remote}"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        env = patch.dict(os.environ, {"PLATFORM_BUILDER_CACHE": self.cache.name})
        env.start()
        self.addCleanup(env.stop)
        source = patch.object(libraries, "parse_source", _git_source)
        source.start()
        self.addCleanup(source.stop)
        libraries._REMOTE_TAGS.clear()

    def resolve(self, ref: str):
        return libraries.resolve_one("github-ci-library", f"{self.location}@{ref}", "test")

    def test_latest_is_the_highest_release_not_a_candidate(self):
        res = self.resolve(libraries.LATEST)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.ref, "1.2.0")
        self.assertEqual(res.requested_ref, libraries.LATEST)
        self.assertEqual(res.release_tags, ["1.0.0", "1.1.0", "v1.1.5", "1.2.0"])
        self.assertIn("docs/workflow-map.json", res.data)

    def test_migration_chain_is_complete_from_a_shallow_clone(self):
        # The clone of 1.2.0 is shallow and holds only its own tag; the chain still lists
        # every release after the pin, because the tags come from the remote.
        res = self.resolve(libraries.LATEST)
        chain = [v for v, _ in libraries.migration_chain(res, "1.0.0")]
        self.assertEqual(chain, ["1.1.0", "v1.1.5", "1.2.0"])
        self.assertEqual(dict(libraries.migration_chain(res, "1.1.0"))["1.2.0"], "Notes for 1.2.0.")

    def test_a_pin_behind_the_latest_release_is_read_and_noted(self):
        res = self.resolve("1.1.0")
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.ref, "1.1.0")
        self.assertIn("behind: 1.1.0 -> 1.2.0 (platform update moves the project)", res.notes)

    def test_a_release_without_the_data_file_stops_and_names_the_upgrade(self):
        res = self.resolve("1.0.0")
        self.assertFalse(res.ok)
        self.assertIn("ships no docs/workflow-map.json", res.error)
        self.assertIn("Upgrade the project to 1.2.0", res.error)

    def test_an_unknown_schema_asks_for_a_newer_skill(self):
        with patch.object(libraries, "SUPPORTED_SCHEMAS", (2,)), \
                patch.object(libraries, "skills_source", return_value=self.location):
            res = self.resolve("1.2.0")
        self.assertFalse(res.ok)
        self.assertIn("has schema 1, which this skill does not know", res.error)
        self.assertIn("Upgrade the skills to the latest release (1.2.0)", res.error)

    def test_offline_latest_uses_a_cached_release_or_stops(self):
        with patch.object(libraries, "remote_release_tags", return_value=None):
            res = self.resolve(libraries.LATEST)
        self.assertFalse(res.ok)
        self.assertIn("cannot list the releases", res.error)

        self.assertTrue(self.resolve("1.1.0").ok)          # leaves a cached clone of 1.1.0
        libraries._REMOTE_TAGS.clear()
        with patch.object(libraries, "remote_release_tags", return_value=None):
            res = self.resolve(libraries.LATEST)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.ref, "1.1.0")
        self.assertTrue(any(n.startswith("offline: using the cached release 1.1.0") for n in res.notes))


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_the_project_pin_beats_the_default_and_latest_skips_it(self):
        spec, origin = libraries.resolve_spec("gitlab-ci-library", self.repo, {}, pinned="1.10.0")
        self.assertEqual((libraries.parse_source(spec).ref, origin), ("1.10.0", "project pin"))
        spec, origin = libraries.resolve_spec("gitlab-ci-library", self.repo, {}, pinned="1.10.0", latest=True)
        self.assertEqual(libraries.parse_source(spec).ref, libraries.LATEST)
        self.assertIn("default", origin)

    def test_explicit_sources_beat_the_pin(self):
        cli = {"gitlab-ci-library": "https://example.com/mirror.git@dev"}
        spec, origin = libraries.resolve_spec("gitlab-ci-library", self.repo, cli, pinned="1.10.0")
        self.assertEqual((spec, origin), (cli["gitlab-ci-library"], "command line"))

        (self.repo / ".platform-builder.json").write_text(json.dumps(
            {"libraries": {"gitlab-ci-library": "https://example.com/mirror.git@1.9.0"}}))
        spec, origin = libraries.resolve_spec("gitlab-ci-library", self.repo, {}, pinned="1.10.0")
        self.assertEqual(libraries.parse_source(spec).ref, "1.9.0")
        # An upgrade keeps the configured mirror but moves it to the latest release.
        spec, origin = libraries.resolve_spec("gitlab-ci-library", self.repo, {}, pinned="1.10.0", latest=True)
        self.assertEqual(spec, f"https://example.com/mirror.git@{libraries.LATEST}")

    def test_a_chart_library_is_read_only_for_a_repository_with_a_chart(self):
        with patch.object(libraries, "resolve_one",
                          side_effect=lambda name, spec, origin, **_: libraries.Resolved(name, spec, origin, "git")):
            names = [r.name for r in libraries.resolve_all(self.repo, "gitlab", {})]
            self.assertEqual(names, ["gitlab-ci-library"])
            (self.repo / "chart").mkdir()
            (self.repo / "chart" / "Chart.yaml").write_text(
                "name: api\ndependencies:\n  - name: tpl-library\n    version: 1.5.0\n")
            resolved = libraries.resolve_all(self.repo, "gitlab", {})
        helm = next(r for r in resolved if r.name == "helm-tpl-library")
        self.assertEqual((libraries.parse_source(helm.spec).ref, helm.origin), ("1.5.0", "project pin"))


if __name__ == "__main__":
    unittest.main()
