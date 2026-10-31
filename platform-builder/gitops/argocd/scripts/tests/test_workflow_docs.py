"""The skill's Markdown holds together: links, section and step references, approvals, names.

The workflows lean on each other ("01 step 6", "02 steps 1–4") and on numbered sections of the
references ("discovery.md §7", "kubernetes-rules.md §4"). Renumbering one file silently breaks
the others, so every such reference must land on a heading that exists.
"""
import re
import unittest
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[4]
_BACKEND = _SKILL / "gitops" / "argocd"
_DOCS = sorted(p for p in _SKILL.rglob("*.md") if "tests" not in p.parts)
_WORKFLOWS = {p.name[:2]: p for p in sorted((_BACKEND / "workflows").glob("[0-9][0-9]-*.md"))}
_NUMBERED = {
    "discovery": _BACKEND / "references" / "discovery.md",
    "kubernetes-rules": _SKILL / "core" / "references" / "kubernetes-rules.md",
}

_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
_SECTION_REF = re.compile(r"\[[^\]]*\]\([^)#\s]*?(discovery|kubernetes-rules)\.md\)\s*§(\d+)")
_LOCAL_SECTION_REF = re.compile(r"\(§(\d+)\)")
_STEP_REF = re.compile(r"(?:\[(\d\d)[^\]]*\]\([^)]*\)|\b(\d\d))\s+\*{0,2}steps?\s+(\d+)(?:[–-](\d+))?")
_SECTION_HEADING = re.compile(r"^## (\d+)\. ", re.M)
_STEP_HEADING = re.compile(r"^## Step (\d+):", re.M)
#: Names of real environments and customers; examples use oneuptime, dev and prod instead.
_PRIVATE_NAMES = re.compile(r"(?i)\b(midgard|moheet|ezto|zentre|sparkzen|jaidar\w*|grootan\.com|grootan-(?!devops)\w+)")


def _rel(path):
    return path.relative_to(_SKILL).as_posix()


class SkillDocTests(unittest.TestCase):
    def test_every_workflow_is_routed_from_skill_md(self):
        skill = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(sorted(_WORKFLOWS), ["01", "02", "03", "04", "05"])
        for path in _WORKFLOWS.values():
            with self.subTest(workflow=path.name):
                self.assertIn(f"](./{_rel(path)})", skill)

    def test_every_command_workflow_wires_the_deploy_job(self):
        """env add, app add, clone and promote end with the deploy job, asking only auto or manual."""
        for key in ("01", "02", "03", "04"):
            text = _WORKFLOWS[key].read_text(encoding="utf-8")
            with self.subTest(workflow=key):
                self.assertRegex(text, r"(?m)^## Step \d+: Wire the CI deploy job$")
                self.assertIn(f"]({_WORKFLOWS['05'].name}) steps 1–6", text)
                self.assertIn("**automatic**", text)

    def test_every_command_workflow_holds_new_work_for_review(self):
        """env add, app add, clone and promote hand new work to a dev before any create or sync."""
        for key in ("01", "02", "03", "04"):
            text = _WORKFLOWS[key].read_text(encoding="utf-8")
            with self.subTest(workflow=key):
                gate = text.find("](../references/review-gate.md)")
                self.assertNotEqual(gate, -1)
                live = [i for i in (text.find("argocd app create"), text.find("argocd app sync")) if i != -1]
                if live:
                    self.assertLess(gate, min(live))

    def test_every_command_workflow_reports_what_the_dev_keeps(self):
        """The report opens with app_report.py: URLs, Argo CD apps, file and entry links, sync order."""
        for key in ("01", "02", "03", "04"):
            text = _WORKFLOWS[key].read_text(encoding="utf-8")
            with self.subTest(workflow=key):
                report = text[text.rindex("## Step"):]
                self.assertRegex(report, r"^## Step \d+: Report")
                self.assertIn("gitops/argocd/scripts/app_report.py", report)

    def test_relative_links_resolve(self):
        for path in _DOCS:
            for target in _LINK.findall(path.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+://", target):
                    continue
                with self.subTest(file=_rel(path), link=target):
                    self.assertTrue((path.parent / target).exists(), f"broken link {target}")

    def test_section_references_hit_numbered_sections(self):
        sections = {name: set(_SECTION_HEADING.findall(p.read_text(encoding="utf-8"))) for name, p in _NUMBERED.items()}
        for path in _DOCS:
            text = path.read_text(encoding="utf-8")
            refs = _SECTION_REF.findall(text)
            for name, numbered in _NUMBERED.items():
                if path == numbered:
                    refs += [(name, no) for no in _LOCAL_SECTION_REF.findall(text)]
            for name, no in refs:
                with self.subTest(file=_rel(path), ref=f"{name} §{no}"):
                    self.assertIn(no, sections[name], f"{_rel(path)} points at missing {name}.md §{no}")

    def test_step_references_hit_workflow_steps(self):
        steps = {key: set(map(int, _STEP_HEADING.findall(p.read_text(encoding="utf-8")))) for key, p in _WORKFLOWS.items()}
        for key, found in steps.items():
            with self.subTest(workflow=key):
                self.assertEqual(sorted(found), list(range(1, len(found) + 1)), "steps not numbered 1..n")
        for path in _DOCS:
            for linked, bare, first, last in _STEP_REF.findall(path.read_text(encoding="utf-8")):
                target = linked or bare
                if target not in _WORKFLOWS:
                    continue
                for no in range(int(first), int(last or first) + 1):
                    with self.subTest(file=_rel(path), ref=f"{target} step {no}"):
                        self.assertIn(no, steps[target], f"{_rel(path)} points at missing step {no} of workflow {target}")

    def test_workflows_gate_remote_actions_and_report(self):
        for path in _WORKFLOWS.values():
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                self.assertIn("**Approval rule:**", text)
                self.assertRegex(text, r"(?m)^## Step \d+: Report$")

    def test_upsert_is_only_ever_forbidden(self):
        for path in _DOCS:
            lines = path.read_text(encoding="utf-8").splitlines()
            for no, line in enumerate(lines, 1):
                if "--upsert" in line:
                    # A wrapped sentence may carry its "never" on the line before.
                    with self.subTest(file=_rel(path), line=no):
                        self.assertRegex(" ".join(lines[max(no - 2, 0):no]), r"(?i)\b(no|never|not)\b",
                                         "--upsert mentioned without forbidding it")

    def test_examples_name_no_real_environment_or_customer(self):
        for path in _DOCS:
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=_rel(path), line=no):
                    self.assertIsNone(_PRIVATE_NAMES.search(line), f"{_rel(path)}:{no}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
