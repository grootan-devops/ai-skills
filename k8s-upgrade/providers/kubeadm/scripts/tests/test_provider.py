"""The kubeadm hooks workspace.py calls, and the package script's version arguments."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import provider  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
ASSETS = SCRIPTS.parent / "assets"
FIXTURES = HERE / "fixtures"
BASH = shutil.which("bash")


def which_without(*missing):
    real = shutil.which
    return lambda tool, *a, **k: None if tool in missing else (real(tool, *a, **k) or f"/usr/bin/{tool}")


class ProviderHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, source, files=("control-plane.md", "workers.md")):
        for name in files:
            target = self.c / name
            shutil.copy(source / (name + ".example" if source == ASSETS else name), target)
            os.chmod(target, 0o600)

    def test_init_files_are_the_credential_files_and_their_examples_exist(self):
        self.assertEqual(sorted(provider.INIT_FILES), sorted(provider.SECRET_FILES))
        for example in provider.INIT_FILES.values():
            self.assertTrue((ASSETS / example).is_file(), example)

    def test_filled_in_files_lint_clean(self):
        self.use(FIXTURES)
        with patch.object(provider.shutil, "which", side_effect=which_without()):
            notes, problems = provider.lint(self.c)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["OK 2 control plane(s), 1 worker(s)"])

    def test_example_rows_left_in_place_are_problems(self):
        self.use(ASSETS)
        key = Path(os.path.expanduser("~/.ssh/cluster_ed25519"))
        with patch.object(provider.shutil, "which", side_effect=which_without()):
            notes, problems = provider.lint(self.c)
        if key.is_file():
            self.assertIn("example row left in the credential files: cp-2", problems)
            self.assertIn("example row left in the credential files: worker-1", problems)
        else:  # the example key path does not exist here, so --check stops first
            self.assertIn("control-plane cp-1: auth is key but ssh_key_path does not exist", problems)
        self.assertEqual(notes, [])

    def test_password_auth_without_sshpass_and_missing_tools_are_problems(self):
        self.use(FIXTURES)
        with patch.object(provider.shutil, "which", side_effect=which_without("sshpass", "curl")):
            _, problems = provider.lint(self.c)
        self.assertEqual(problems, ["sshpass is not installed and some nodes use password auth", "curl is not installed"])

    def test_check_errors_become_problems_without_their_prefix(self):
        self.use(FIXTURES)
        os.chmod(self.c / "workers.md", 0o644)
        with patch.object(provider.shutil, "which", side_effect=which_without()):
            notes, problems = provider.lint(self.c)
        self.assertEqual(notes, [])
        self.assertTrue(problems[0].startswith(f"{self.c / 'workers.md'}: mode 644"), problems)


@unittest.skipUnless(BASH, "bash is not installed")
class PackageVersionArgumentTests(unittest.TestCase):
    """k8s_pkg.sh takes a minor for versions and repo, and an exact X.Y.Z without v to install."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake = Path(self.tmp.name) / "apt-get"
        fake.write_text("#!/bin/sh\necho unexpected apt-get call >&2\nexit 99\n")
        fake.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.tmp.name}:{os.environ.get('PATH', '')}"}

    def tearDown(self):
        self.tmp.cleanup()

    def pkg(self, *args):
        return subprocess.run([BASH, str(SCRIPTS / "k8s_pkg.sh"), *args], capture_output=True, text=True, env=self.env)

    def test_versions_and_repo_take_a_minor_not_a_patch(self):
        for cmd in ("versions", "repo"):
            r = self.pkg(cmd, "1.31.2")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("expected MAJOR.MINOR, got 1.31.2", r.stderr)

    def test_install_takes_an_exact_version_without_v(self):
        for version in ("v1.31.2", "1.31"):
            r = self.pkg("install", "kubeadm", version)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(f"expected X.Y.Z without v, got {version}", r.stderr)


if __name__ == "__main__":
    unittest.main()
