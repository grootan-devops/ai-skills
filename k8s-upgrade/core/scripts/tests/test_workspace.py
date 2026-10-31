"""Workspace init, lint and the stage gate that keeps the six stages in order."""
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workspace  # noqa: E402

CONTEXTS = "lab-admin\nother\n"


def fake_run(git_ignored=True, contexts=CONTEXTS, kubeconfig="apiVersion: v1\nkind: Config\n"):
    """Stand-in for subprocess.run: answers the git and kubectl calls workspace.py makes."""
    def run(cmd, **kwargs):
        if cmd[:2] == ["git", "-C"] and "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "true\n", "")
        if cmd[:2] == ["git", "-C"] and "check-ignore" in cmd:
            return subprocess.CompletedProcess(cmd, 0 if git_ignored else 1, "", "")
        if cmd[:3] == ["kubectl", "config", "get-contexts"]:
            return subprocess.CompletedProcess(cmd, 0, contexts, "")
        if cmd[:3] == ["kubectl", "config", "view"]:
            return subprocess.CompletedProcess(cmd, 0 if kubeconfig else 1, kubeconfig, "" if kubeconfig else "no context")
        raise AssertionError(f"unexpected command {cmd}")
    return run


STUB_PROVIDER = SimpleNamespace(SECRET_FILES=["control-plane.md"], lint=lambda c: (["provider inputs"], []))


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = self.root / "lab"

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, fn, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fn(SimpleNamespace(**kwargs))
        return rc, out.getvalue()

    def init(self):
        return self.call(workspace.cmd_init, root=str(self.root), cluster="lab", provider="kubeadm", context="lab-admin")

    def record(self, stage, result="pass", run=None):
        workspace._record(self.c, stage, result, run)

    def require(self, stage):
        return self.call(workspace.cmd_require, cluster_dir=str(self.c), stage=stage)

    def test_init_creates_the_workspace_with_credential_files_mode_600(self):
        rc, _ = self.init()
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / ".gitignore").is_file())
        cluster = (self.c / "cluster.md").read_text()
        self.assertIn("provider: kubeadm", cluster)
        self.assertIn("context: lab-admin", cluster)
        for name in ("control-plane.md", "workers.md"):
            self.assertEqual(stat.S_IMODE((self.c / name).stat().st_mode), 0o600, name)
        self.assertTrue((self.c / "critical-extra.txt").is_file())
        state = json.loads((self.c / "state.json").read_text())
        self.assertEqual(state["stages"]["init"]["result"], "pass")
        self.assertIsNone(state["run"])

    def test_init_overwrites_nothing_the_user_filled_in(self):
        self.c.mkdir()
        (self.c / "workers.md").write_text("mine\n")
        (self.c / "critical-extra.txt").write_text("apps/deployment/api\n")
        _, out = self.init()
        self.assertEqual((self.c / "workers.md").read_text(), "mine\n")
        self.assertEqual((self.c / "critical-extra.txt").read_text(), "apps/deployment/api\n")
        self.assertNotIn("lab/workers.md", out.splitlines()[0])
        self.assertEqual(stat.S_IMODE((self.c / "workers.md").stat().st_mode), 0o600)

    def test_a_planned_provider_is_refused_with_the_supported_list(self):
        with self.assertRaises(SystemExit) as e:
            self.call(workspace.cmd_init, root=str(self.root), cluster="lab", provider="eks", context="x")
        self.assertIn("not supported", str(e.exception))
        self.assertIn("kubeadm", str(e.exception))
        self.assertFalse(self.c.exists())

    def test_init_refuses_a_cluster_that_names_another_provider(self):
        self.c.mkdir()
        (self.c / "cluster.md").write_text("provider: gke\ncontext: x\n")
        with self.assertRaises(SystemExit) as e:
            self.init()
        self.assertIn("'gke'", str(e.exception))

    def test_providers_lists_the_provider_folders_with_hooks(self):
        self.assertIn("kubeadm", workspace.available_providers())

    def test_a_stage_is_blocked_until_the_stage_before_it_ran(self):
        self.init()
        rc, out = self.require("check")
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED check: run lint first", out)

    def test_a_stage_is_blocked_when_an_earlier_stage_failed(self):
        self.init()
        self.record("lint", "fail")
        rc, out = self.require("check")
        self.assertEqual(rc, 1)
        self.assertIn("lint did not pass", out)

    def test_stages_after_check_need_the_run_folder_check_recorded(self):
        self.init()
        self.record("lint")
        self.record("check")
        rc, out = self.require("prepare")
        self.assertEqual((rc, out.strip()), (1, "BLOCKED prepare: no run folder recorded; run check again"))
        self.record("check", run="v1.30.4-to-v1.31.2-20261031-0900")
        rc, out = self.require("prepare")
        self.assertEqual(rc, 0)
        self.assertIn("run=v1.30.4-to-v1.31.2", out)

    def test_upgrade_needs_every_earlier_stage_in_order(self):
        self.init()
        self.record("lint")
        self.record("check", run="r1")
        self.assertEqual(self.require("upgrade")[0], 1)
        self.record("prepare")
        self.assertEqual(self.require("upgrade")[0], 0)

    def test_re_running_a_stage_clears_every_later_stage(self):
        self.init()
        self.record("lint")
        self.record("check", run="r1")
        self.record("prepare")
        self.record("check", run="r2")
        state = workspace.load_state(self.c)
        self.assertEqual(list(state["stages"]), ["init", "lint", "check"])
        self.assertEqual(state["run"], "r2")
        rc, out = self.require("upgrade")
        self.assertEqual(rc, 1)
        self.assertIn("run prepare first", out)

    def test_re_running_lint_drops_the_run_folder_of_the_old_check(self):
        self.init()
        self.record("lint")
        self.record("check", run="r1")
        self.record("lint")
        self.assertIsNone(workspace.load_state(self.c)["run"])

    def test_status_names_the_next_stage(self):
        self.init()
        self.record("lint")
        _, out = self.call(workspace.cmd_status, cluster_dir=str(self.c))
        self.assertIn("provider: kubeadm", out)
        self.assertTrue(out.rstrip().endswith("next: check"))

    def lint(self, **run):
        with patch.object(workspace, "load_provider", return_value=STUB_PROVIDER), \
                patch.object(workspace.subprocess, "run", side_effect=fake_run(**run)), \
                patch.object(workspace.shutil, "which", return_value="/usr/bin/tool"):
            return self.call(workspace.cmd_lint, cluster_dir=str(self.c))

    def test_lint_passes_a_complete_workspace_and_records_it(self):
        self.init()
        rc, out = self.lint()
        self.assertEqual(rc, 0, out)
        self.assertIn("OK   kubectl context lab-admin", out)
        self.assertEqual(workspace.load_state(self.c)["stages"]["lint"]["result"], "pass")

    def test_lint_fails_credentials_git_does_not_ignore(self):
        self.init()
        rc, out = self.lint(git_ignored=False)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL control-plane.md is not ignored by git", out)
        self.assertIn("FAIL kubeconfig is not ignored by git", out)
        self.assertEqual(workspace.load_state(self.c)["stages"]["lint"]["result"], "fail")

    def test_lint_fails_an_unknown_context_and_a_bad_critical_extra_line(self):
        self.init()
        (self.c / "critical-extra.txt").write_text("# comment\napps/Deployment/api\napps/cronjob/nightly\nbroken\n")
        rc, out = self.lint(contexts="other\n")
        self.assertEqual(rc, 1)
        self.assertIn("kubectl context lab-admin not found", out)
        self.assertIn("critical-extra.txt:3: expected namespace/kind/name, got apps/cronjob/nightly", out)
        self.assertIn("critical-extra.txt:4:", out)
        self.assertNotIn("critical-extra.txt:2:", out)

    def test_kubeconfig_holds_only_the_named_context_and_is_mode_600(self):
        self.init()
        with patch.object(workspace.subprocess, "run", side_effect=fake_run()) as run:
            rc, out = self.call(workspace.cmd_kubeconfig, cluster_dir=str(self.c))
        self.assertEqual(rc, 0)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--context") + 1], "lab-admin")
        self.assertIn("--minify", cmd)
        target = self.c / "kubeconfig"
        self.assertEqual(out.strip(), str(target))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertIn("kind: Config", target.read_text())

    def test_kubeconfig_fails_when_the_context_cannot_be_exported(self):
        self.init()
        with patch.object(workspace.subprocess, "run", side_effect=fake_run(kubeconfig="")), \
                redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            rc = workspace.cmd_kubeconfig(SimpleNamespace(cluster_dir=str(self.c)))
        self.assertEqual(rc, 1)
        self.assertFalse((self.c / "kubeconfig").exists())


if __name__ == "__main__":
    unittest.main()
