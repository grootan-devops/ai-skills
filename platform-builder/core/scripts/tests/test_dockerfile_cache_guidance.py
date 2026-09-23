import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checks_common  # noqa: E402


class DockerfileCacheGuidanceTests(unittest.TestCase):
    def findings_for(self, platform, dockerfile):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Dockerfile"
            path.write_text(dockerfile, encoding="utf-8")
            checks_common.set_platform(platform)
            return checks_common.check_dockerfile(path, "service")

    def test_github_python_remedy_uses_github_cache_and_handoff(self):
        findings = self.findings_for(
            "github",
            "FROM python:3.12\nRUN uv sync --frozen\nCOPY . /app\nUSER 10001\n",
        )
        packaging = [f for f in findings if f.category == "Dockerfile Packaging Violation"]

        self.assertEqual(len(packaging), 1)
        self.assertIn(".uv-cache", packaging[0].message)
        self.assertIn("github-ci-library docker.yml", packaging[0].message)
        self.assertIn("explicit cache or artifact handoff", packaging[0].message)
        self.assertNotIn("same PROJECT_CACHE_KEY and cache path", packaging[0].message)

    def test_gitlab_python_remedy_uses_shared_gitlab_cache_key(self):
        findings = self.findings_for(
            "gitlab",
            "FROM python:3.12\nRUN uv sync --frozen\nCOPY . /app\nUSER 10001\n",
        )
        packaging = [f for f in findings if f.category == "Dockerfile Packaging Violation"]

        self.assertEqual(len(packaging), 1)
        self.assertIn("source=.uv,target=/tmp/.uv", packaging[0].message)
        self.assertIn("same PROJECT_CACHE_KEY and cache path", packaging[0].message)
        self.assertNotIn("github-ci-library docker.yml", packaging[0].message)


if __name__ == "__main__":
    unittest.main()
