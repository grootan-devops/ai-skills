"""Every skill checks for a newer ai-skills release before it does anything else.

Installed skills are copied one folder at a time, so each ships its own copy of
update_check.py. The release lives only in the repository's root VERSION. These tests keep the
copies identical and the check the first step of every SKILL.md; then they cover the check.
"""
import filecmp
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import update_check  # noqa: E402

_REPO = Path(__file__).resolve().parents[4]
_CANONICAL = _REPO / "platform-builder" / "core" / "scripts" / "update_check.py"
_SKILLS = sorted(p.parent for p in _REPO.glob("*/SKILL.md"))


def refs(*tags, heads=None):
    found = {f"refs/tags/{t}": f"sha-{t}" for t in tags}
    found.update({f"refs/heads/{b}": sha for b, sha in (heads or {}).items()})
    return found


class EverySkillTests(unittest.TestCase):
    def test_skills_were_found(self):
        self.assertGreaterEqual(len(_SKILLS), 5)

    def test_no_skill_ships_its_own_version_file(self):
        for skill in _SKILLS:
            with self.subTest(skill=skill.name):
                self.assertFalse((skill / "VERSION").exists(), "the release lives only in the root VERSION")

    def test_every_skill_ships_one_identical_copy_of_the_check(self):
        for skill in _SKILLS:
            copies = [p for p in skill.rglob("update_check.py") if "tests" not in p.parts]
            with self.subTest(skill=skill.name):
                self.assertEqual(len(copies), 1, copies)
                self.assertTrue(filecmp.cmp(copies[0], _CANONICAL, shallow=False), f"{copies[0]} differs")

    def test_the_check_is_the_first_step_of_every_skill(self):
        for skill in _SKILLS:
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            sections = [line for line in text.splitlines() if line.startswith("## ")]
            copy = next(p for p in skill.rglob("update_check.py") if "tests" not in p.parts)
            with self.subTest(skill=skill.name):
                self.assertEqual(sections[0], "## Check for updates first")
                self.assertIn(f"`python3 {copy.relative_to(skill).as_posix()}`", text)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.skill = Path(self.tmp.name) / "demo-skill"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
        self.version("2.1.0")                     # the clone's root VERSION, beside the skill

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, value):
        (self.skill.parent / "VERSION").write_text(value + "\n", encoding="utf-8")

    def clone(self, **found):
        return {"path": str(self.skill.parent), "commit": "sha-x", "tag": None, "branch": None, **found}

    def copied(self, remote):
        with patch.object(update_check, "checkout", return_value=None):
            return update_check.check(self.skill, remote)

    def test_a_copied_install_does_not_know_its_release_and_gets_the_reinstall_command(self):
        result = self.copied(refs("2.0.0", "2.1.0", "2.2.0-rc.1"))
        self.assertEqual((result["status"], result["installed"], result["latest"]), ("unknown", None, "2.1.0"))
        self.assertIn("npx skills add https://github.com/grootan-devops/ai-skills --skill demo-skill", result["update"])
        with patch.object(update_check, "remote_refs", return_value=refs("2.1.0")), \
                patch.object(update_check, "skill_root", return_value=self.skill), \
                patch.object(update_check, "checkout", return_value=None):
            self.assertEqual(update_check.main([]), 2)

    def test_a_checkout_without_a_tag_reads_the_root_version(self):
        with patch.object(update_check, "checkout", return_value=self.clone()):
            current = update_check.check(self.skill, refs("2.0.0", "2.1.0"))
            self.version("2.0.0")
            behind = update_check.check(self.skill, refs("2.0.0", "2.1.0"))
        self.assertEqual((current["status"], current["installed"]), ("current", "2.1.0"))
        self.assertEqual((behind["status"], behind["installed"]), ("update-available", "2.0.0"))

    def test_offline_is_not_checked_and_carries_on(self):
        self.assertEqual(self.copied(None)["status"], "not-checked")
        with patch.object(update_check, "remote_refs", return_value=None), \
                patch.object(update_check, "skill_root", return_value=self.skill), \
                patch.object(update_check, "checkout", return_value=None):
            self.assertEqual(update_check.main([]), 2)

    def test_a_checkout_on_an_old_tag_is_told_to_check_out_the_latest(self):
        repo = {"path": "/src/ai-skills", "commit": "sha-1.9.0", "tag": "1.9.0", "branch": None}
        with patch.object(update_check, "checkout", return_value=repo):
            result = update_check.check(self.skill, refs("1.9.0", "2.1.0"))
        self.assertEqual(result["status"], "update-available")
        self.assertEqual(result["update"], "git -C /src/ai-skills fetch --tags && git -C /src/ai-skills checkout 2.1.0")

    def test_a_checkout_on_a_branch_behind_its_remote_is_told_to_pull(self):
        repo = {"path": "/src/ai-skills", "commit": "aaa", "tag": None, "branch": "main"}
        with patch.object(update_check, "checkout", return_value=repo):
            behind = update_check.check(self.skill, refs("2.1.0", heads={"main": "bbb"}))
            level = update_check.check(self.skill, refs("2.1.0", heads={"main": "aaa"}))
        self.assertEqual(behind["status"], "update-available")
        self.assertEqual(behind["update"], "git -C /src/ai-skills pull --ff-only")
        self.assertIn("branch main has newer commits on the remote", behind["notes"])
        self.assertEqual(level["status"], "current")

    def test_an_alias_resolves_to_its_parent_skill(self):
        alias = self.skill / "aliases" / "demo-alias"
        alias.mkdir(parents=True)
        (alias / "SKILL.md").write_text("---\nname: demo-alias\n---\n", encoding="utf-8")
        self.assertEqual(update_check.skill_root(alias), self.skill)

    def test_a_copied_skill_reports_itself_by_its_folder_name(self):
        shutil.copy(_CANONICAL, self.skill / "scripts" / "update_check.py")
        self.assertEqual(update_check.skill_root(self.skill / "scripts"), self.skill)
        self.assertEqual(self.copied(refs("2.1.0"))["skill"], "demo-skill")


if __name__ == "__main__":
    unittest.main()
