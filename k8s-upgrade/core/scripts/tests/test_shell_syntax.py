"""Every shell script of the skill parses with bash -n."""
import shutil
import subprocess
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is not installed")
class ShellSyntaxTests(unittest.TestCase):
    def test_every_shell_script_parses(self):
        scripts = sorted(SKILL.rglob("*.sh"))
        self.assertGreaterEqual(len(scripts), 8)
        for script in scripts:
            with self.subTest(script=str(script.relative_to(SKILL))):
                r = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_script_is_executable_with_a_bash_shebang(self):
        for script in sorted(SKILL.rglob("*.sh")):
            with self.subTest(script=str(script.relative_to(SKILL))):
                self.assertEqual(script.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env bash")
                self.assertTrue(script.stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
