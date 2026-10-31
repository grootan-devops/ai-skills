"""nodes.py: the credential tables remote.sh reads, and what --check refuses."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nodes  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRETS = ("ssh-secret-1", "pa|ss", "sudo-secret-2", "ssh-secret-3")


class NodesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Path(self.tmp.name)
        for name in ("control-plane.md", "workers.md"):
            shutil.copy(FIXTURES / name, self.c / name)
            os.chmod(self.c / name, 0o600)
        self.cp, self.wk = self.c / "control-plane.md", self.c / "workers.md"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, rows, header="| name | host | user | auth | password | ssh_key_path |"):
        path = self.c / name
        path.write_text("\n".join([header, "| --- | --- | --- | --- | --- | --- |", *rows]) + "\n")
        os.chmod(path, 0o600)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["nodes.py", *argv]), redirect_stdout(out), redirect_stderr(err):
            rc = nodes.main()
        return rc, out.getvalue(), err.getvalue()

    def test_only_the_first_table_is_read_and_escaped_pipes_are_kept(self):
        rows = nodes.parse_table(self.cp)
        self.assertEqual([r["name"] for r in rows], ["cp-1", "cp-2"])
        self.assertEqual(rows[1]["password"], "pa|ss")

    def test_defaults_port_22_lower_case_auth_and_sudo_falls_back_to_password(self):
        loaded = {n["name"]: n for n in nodes.load(self.cp, self.wk)}
        self.assertEqual((loaded["cp-1"]["port"], loaded["cp-1"]["auth"]), ("22", "password"))
        self.assertEqual(loaded["cp-1"]["sudo_password"], "ssh-secret-1")
        self.assertEqual(loaded["cp-2"]["sudo_password"], "sudo-secret-2")
        self.assertEqual((loaded["worker-1"]["role"], loaded["worker-1"]["port"]), ("worker", "2222"))

    def test_check_passes_valid_files_without_printing_a_secret(self):
        rc, out, err = self.main("--check", str(self.cp), str(self.wk))
        self.assertEqual((rc, out.strip(), err), (0, "OK 2 control plane(s), 1 worker(s)", ""))

    def test_list_prints_no_secret(self):
        rc, out, _ = self.main("--list", str(self.cp), str(self.wk))
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "control-plane\tcp-1\t192.0.2.11\t22\tadmin\tpassword")
        for secret in SECRETS:
            self.assertNotIn(secret, out)

    def test_get_prints_one_field_and_expands_the_key_path(self):
        self.assertEqual(self.main("--get", "cp-2", "password", str(self.cp), str(self.wk))[:2], (0, "pa|ss"))
        self.write("workers.md", ["| worker-9 | 192.0.2.29 | admin | key | | ~/.ssh/id_test |"])
        rc, out, _ = self.main("--get", "worker-9", "ssh_key_path", str(self.cp), str(self.wk))
        self.assertEqual((rc, out), (0, os.path.expanduser("~/.ssh/id_test")))
        self.assertEqual(self.main("--get", "nope", "host", str(self.cp), str(self.wk))[0], 1)
        self.assertEqual(self.main("--get", "cp-1", "token", str(self.cp), str(self.wk))[0], 2)

    def test_credential_files_readable_by_others_are_refused(self):
        os.chmod(self.wk, 0o644)
        errors = nodes.check(self.cp, self.wk)
        self.assertEqual(errors, [f"{self.wk}: mode 644, must be 600 (chmod 600 {self.wk})"])

    def test_check_reports_every_bad_row(self):
        key = self.c / "id_test"
        key.write_text("key\n")
        self.write("workers.md", [
            f"| worker-1 | 192.0.2.21 | admin | key | | {key} |",
            "| cp-1 | 192.0.2.22 | admin | password | | |",
            "| worker-3 | | admin | key | | /nonexistent/id |",
            "| worker-4 | 192.0.2.24 | admin | token | | |",
        ])
        errors = nodes.check(self.cp, self.wk)
        self.assertEqual(errors, [
            "worker cp-1: name repeats",
            "worker cp-1: auth is password but password is empty",
            "worker worker-3: host is empty",
            "worker worker-3: auth is key but ssh_key_path does not exist",
            "worker worker-4: auth must be password or key",
        ])

    def test_a_cluster_needs_at_least_one_control_plane(self):
        self.write("control-plane.md", [])
        self.assertIn(f"{self.cp}: no table rows", nodes.check(self.cp, self.wk))
        self.assertEqual(nodes.check(self.c / "missing.md", self.wk), [f"{self.c / 'missing.md'}: missing"])

    def test_remote_sh_reads_fields_through_the_cli(self):
        r = subprocess.run([sys.executable, str(Path(nodes.__file__)), "--get", "worker-1", "port", str(self.cp), str(self.wk)],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "2222"))


if __name__ == "__main__":
    unittest.main()
