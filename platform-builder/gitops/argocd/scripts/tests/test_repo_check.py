import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "core" / "scripts"))

import repo_check  # noqa: E402


def chart(name="oneuptime", pin="1.9.0", library="argocd-gitops-tpl-library"):
    return (f"apiVersion: v2\nname: {name}\nversion: 1.0.0\ndependencies:\n"
            f"  - name: {library}\n    version: {pin}\n    repository: oci://registry.example.com/charts\n")


def environment(env="dev", branch="oneuptime/dev", pin="1.9.0", extras_pin=None, apps="{}"):
    return {
        "Chart.yaml": chart(pin=pin),
        "values.yaml": f"enabled: true\nproject: developer\nenvironment: {env}\nbranch: {branch}\napps: {apps}\n",
        "templates/apps.yaml": "{{- include \"tpl.argocd.applications\" $ }}\n",
        "extras/Chart.yaml": chart(pin=extras_pin or pin),
        "extras/values.yaml": "extras: {}\n",
        "extras/templates/apps.yaml": "{{- include \"tpl.argocd.application.extras\" $ }}\n",
    }


class RepoCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "gitops"
        self.git("init", "--quiet", "--bare", str(self.repo), cwd=self.tmp.name)
        self.work = Path(self.tmp.name) / "work"
        self.git("clone", "--quiet", str(self.repo), str(self.work), cwd=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd or self.work, check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "HOME": self.tmp.name,
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"})

    def branch(self, name, files):
        self.git("checkout", "--quiet", "--orphan", name)
        self.git("rm", "-rf", "--quiet", "--ignore-unmatch", ".")
        for rel, text in files.items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", name)
        self.git("push", "--quiet", "origin", name)

    def run_check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = repo_check.main([str(self.repo)])
        return code, out.getvalue()

    def test_an_empty_repository(self):
        code, out = self.run_check()
        self.assertEqual(code, 3)
        self.assertIn("empty", out)

    def test_a_repository_of_another_kind(self):
        self.branch("main", {"README.md": "# tools\n", "Chart.yaml": chart(library="nginx")})
        code, out = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn("not the GitOps repository", out)
        self.assertEqual(repo_check.check(repo_check.Repo(self.repo))["status"], "not-gitops")

    def test_a_gitops_repository_that_follows_its_pattern(self):
        self.branch("main", {"README.md": "# edge-east (cluster name)\n"})
        self.branch("oneuptime/dev", environment())
        self.branch("oneuptime/prod", environment("prod", "oneuptime/prod"))
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        result = repo_check.check(repo_check.Repo(self.repo))
        self.assertEqual((result["defaultBranch"], result["clusterLabel"], result["pins"]), ("main", "edge-east", ["1.9.0"]))
        self.assertEqual([b["environment"] for b in result["branches"]], [False, True, True])

    def test_broken_environment_branches_are_problems(self):
        self.branch("main", {"README.md": "# gitops\n"})
        self.branch("oneuptime/dev", environment(extras_pin="1.8.0"))
        broken = environment("qa", "oneuptime/dev", apps="[a, b]")
        del broken["extras/Chart.yaml"]
        self.branch("oneuptime/qa", broken)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("the root pins 1.9.0 but extras/ pins 1.8.0", out)
        self.assertIn("extras/Chart.yaml is missing", out)
        self.assertIn("`apps` is not a map", out)
        self.assertIn("records branch 'oneuptime/dev', not 'oneuptime/qa'", out)

    def test_drift_from_the_siblings_is_reported_but_not_a_problem(self):
        self.branch("main", {"README.md": "# gitops\n"})
        for env in ("dev", "qa", "uat"):
            self.branch(f"oneuptime/{env}", environment(env, f"oneuptime/{env}"))
        odd = environment("prod", "oneuptime/prod", pin="1.7.0")
        odd["values.yaml"] = odd["values.yaml"].replace("project: developer\n", "")
        del odd["templates/apps.yaml"]
        self.branch("oneuptime/prod", odd)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("pins 1.7.0; most branches pin 1.9.0", out)
        self.assertIn("lacks keys most branches set: project", out)
        self.assertIn("lacks files most branches carry: templates/apps.yaml", out)

    def test_a_link_that_cannot_be_read(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(repo_check.main([str(Path(self.tmp.name) / "missing")]), 2)
        self.assertIn("cannot be read", out.getvalue())


if __name__ == "__main__":
    unittest.main()
