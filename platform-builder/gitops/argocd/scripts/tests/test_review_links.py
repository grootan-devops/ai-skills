import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_links  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = review_links.main(list(argv))
    return code, out.getvalue()


class ReviewLinksTests(unittest.TestCase):
    def test_a_gitlab_https_url_in_a_nested_group(self):
        code, out = run("https://gitlab.example.com/devops/environment/team/gitops.git",
                        "--branch", "oneuptime/dev", "--file", "values/chat.yaml")
        self.assertEqual(code, 0, out)
        self.assertIn("https://gitlab.example.com/devops/environment/team/gitops/-/tree/oneuptime/dev", out)
        self.assertIn("https://gitlab.example.com/devops/environment/team/gitops/-/blob/oneuptime/dev/values/chat.yaml",
                      out)

    def test_ssh_urls_become_web_addresses(self):
        for url in ("git@gitlab.example.com:devops/gitops.git", "ssh://git@gitlab.example.com:2222/devops/gitops.git"):
            with self.subTest(url=url):
                self.assertEqual(review_links.web_base(url), "https://gitlab.example.com/devops/gitops")

    def test_a_github_url_uses_github_paths_and_the_mr_comes_first(self):
        code, out = run("git@github.com:example/gitops.git", "--branch", "review-oneuptime-dev-chat",
                        "--file", "extras/manifests/chat/secret.yaml", "--mr", "https://github.com/example/gitops/pull/7")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "review   https://github.com/example/gitops/pull/7")
        self.assertIn("https://github.com/example/gitops/tree/review-oneuptime-dev-chat", lines[1])
        self.assertIn("https://github.com/example/gitops/blob/review-oneuptime-dev-chat/extras/manifests/chat/secret.yaml",
                      lines[2])

    def test_a_host_that_names_neither_platform_must_be_told(self):
        code, out = run("https://scm.example.com/devops/gitops.git", "--branch", "oneuptime/dev")
        self.assertEqual(code, 2)
        self.assertIn("--platform", out)
        code, out = run("https://scm.example.com/devops/gitops.git", "--branch", "oneuptime/dev", "--platform", "gitlab")
        self.assertIn("https://scm.example.com/devops/gitops/-/tree/oneuptime/dev", out)

    def test_changed_lists_the_files_the_branch_adds_or_changes(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "HOME": td,
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"}

            def git(*args):
                subprocess.run(["git", *args], cwd=td, check=True, capture_output=True, env=env)

            git("init", "--quiet", "-b", "oneuptime/dev")
            for name in ("values.yaml", "values/old.yaml"):
                (Path(td) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(td) / name).write_text("a: 1\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "--quiet", "-m", "base")
            git("checkout", "--quiet", "-b", "review-oneuptime-dev-chat")
            (Path(td) / "values.yaml").write_text("a: 2\n", encoding="utf-8")
            (Path(td) / "values/chat.yaml").write_text("b: 1\n", encoding="utf-8")
            (Path(td) / "values/old.yaml").unlink()
            git("add", "-A")
            git("commit", "--quiet", "-m", "chat")
            code, out = run("https://gitlab.example.com/devops/gitops.git", "--branch", "review-oneuptime-dev-chat",
                            "--changed", "oneuptime/dev", "--repo", td, "--json")
        self.assertEqual(code, 0, out)
        self.assertIn("/-/blob/review-oneuptime-dev-chat/values/chat.yaml", out)
        self.assertIn("/-/blob/review-oneuptime-dev-chat/values.yaml", out)
        self.assertNotIn("old.yaml", out)


if __name__ == "__main__":
    unittest.main()
