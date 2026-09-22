#!/usr/bin/env python3
"""Structural gates for a GitLab -> GitHub Actions library port.

Checks the mechanical parts of references/library-conventions.md and
references/docs-contract.md. It does not replace actionlint, yamllint or
shellcheck -- run those too.

Usage:
    python3 verify-port.py <library_dir> [--strict] [--json]

Exit codes:
    0  no P0 findings (or no findings at all with --strict)
    1  P0 findings, or any finding with --strict
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

# A job may write its summary directly, delegate to a shared script, or let an
# action publish it. All three satisfy the summary rule.
SUMMARY_DELEGATES = re.compile(
    r"GITHUB_STEP_SUMMARY|scripts/[\w-]+\.sh|job_summary:\s*true", re.I
)

# Jobs legitimately outside a container: anything driving the Docker daemon, and
# pure-orchestration jobs that run no tooling of their own.
CONTAINERLESS_OK = re.compile(r"verdict|publish|discover", re.I)
NEEDS_DAEMON = re.compile(r"docker\s+(run|build|save|pull|load)|docker/build-push|setup-buildx")

# What a job does -> the token scope it therefore needs. Least privilege cuts both
# ways: an over-privileged declaration is as much a finding as a missing scope.
# Actions the library is expected to need. Anything else warrants a stated reason.
VETTED_ACTIONS = {
    "actions/checkout", "actions/cache", "actions/cache/restore", "actions/cache/save",
    "actions/upload-artifact", "actions/download-artifact",
    "docker/setup-buildx-action", "docker/login-action", "docker/build-push-action",
    "mikepenz/action-junit-report", "softprops/action-gh-release",
}
# Toolchain installers: in a containerised library the build image already has these.
SETUP_ACTIONS = re.compile(r"^(actions/setup-|astral-sh/setup-|azure/setup-|imjasonh/setup-"
                           r"|hashicorp/setup-|terraform-linters/setup-|mikefarah/yq)")
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")

SCOPE_EVIDENCE = [
    ("packages: write", re.compile(r"docker/build-push|docker\s+push|helm\s+push|podman\s+push|crane\s+(push|copy|mutate|tag)")),
    ("checks: write", re.compile(r"action-junit-report")),
    ("actions: read", re.compile(r"gh\s+run\s+(download|list|view)")),
    ("pull-requests: read", re.compile(r"gh\s+pr\s+|commits/.*?/pulls")),
    ("contents: write", re.compile(r"action-gh-release|gh\s+release\s+create|gh\s+api.*?/git/refs")),
]

REQUIRED_README_SECTIONS = [
    "quick start",
    "module catalog",
    "key variables",
    "scan exit codes",
    "ignored cves",
    "integration examples",
]


class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, sev: str, where: str, msg: str, fix: str = "") -> None:
        self.items.append({"severity": sev, "where": where, "message": msg, "fix": fix})

    def by_sev(self, sev: str) -> list[dict]:
        return [i for i in self.items if i["severity"] == sev]


def load_workflow(path: Path):
    try:
        return yaml.safe_load(path.read_text())
    except Exception as exc:  # noqa: BLE001 - surfaced as a finding
        return {"__parse_error__": str(exc)}


def check_workflows(root: Path, f: Findings) -> None:
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        f.add("P0", ".github/workflows", "No workflows directory found.")
        return

    for path in sorted(wf_dir.glob("*.yml")):
        rel = f".github/workflows/{path.name}"
        doc = load_workflow(path)
        if not isinstance(doc, dict):
            f.add("P0", rel, "Workflow does not parse as a YAML mapping.")
            continue
        if "__parse_error__" in doc:
            f.add("P0", rel, f"YAML parse error: {doc['__parse_error__'][:120]}")
            continue

        on = doc.get(True) or doc.get("on") or {}
        is_reusable = isinstance(on, dict) and "workflow_call" in on
        jobs = doc.get("jobs") or {}

        if is_reusable:
            call = on.get("workflow_call") or {}
            for name, spec in (call.get("inputs") or {}).items():
                if not isinstance(spec, dict):
                    continue
                itype = spec.get("type")
                if itype not in ("boolean", "number", "string"):
                    f.add(
                        "P0",
                        rel,
                        f"workflow_call input '{name}' declares type '{itype}'. "
                        "Only boolean, number and string are valid; every caller "
                        "fails at startup with no job and no usable error.",
                    )
                if itype == "boolean" and isinstance(spec.get("default"), str):
                    f.add(
                        "P1",
                        rel,
                        f"workflow_call input '{name}' is boolean but its default "
                        f"is the string {spec['default']!r}.",
                    )

        # --- image coordinate defaults -------------------------------------
        raw = path.read_text()
        for m in re.finditer(
            r"vars\.([A-Z_0-9]*(?:IMAGE|SCANNER)[A-Z_0-9]*)\s*\|\|\s*'([^']+)'", raw
        ):
            var, default = m.group(1), m.group(2)
            if "SUFFIX" in var or "REPOSITORY" in var:
                continue  # repository paths are not image coordinates
            f.add(
                "P0",
                rel,
                f"Container/base image variable {var} carries a hardcoded default "
                f"'{default}'.",
                "Remove the fallback; organisation variables supply image coordinates.",
            )

        # --- explanatory comments inside run blocks ------------------------
        for lineno, line in enumerate(raw.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#") and "shellcheck" not in stripped:
                indent = len(line) - len(line.lstrip())
                if indent >= 10:  # inside a run: block scalar, not a job-level comment
                    f.add(
                        "P2",
                        f"{rel}:{lineno}",
                        "Explanatory comment inside a run: block.",
                        "Move the rationale to the file header or above the job.",
                    )

        # --- action hygiene ---------------------------------------------------
        for lineno, line in enumerate(raw.split("\n"), 1):
            m = re.match(r"\s*-?\s*uses:\s*([^\s@]+)@([^\s#]+)", line)
            if not m:
                continue
            ref, version = m.group(1), m.group(2)
            if ref.startswith("./") or ref.startswith("<"):
                continue  # local reusable workflow, or a README placeholder
            owner_action = "/".join(ref.split("/")[:2])
            is_reusable_call = "/.github/workflows/" in ref

            if is_reusable_call:
                continue

            if SETUP_ACTIONS.match(ref):
                f.add(
                    "P1",
                    f"{rel}:{lineno}",
                    f"Uses toolchain installer '{ref}'.",
                    "The build container already provides the toolchain; drop the action.",
                )
            elif owner_action not in VETTED_ACTIONS and not ref.startswith("actions/"):
                f.add(
                    "P2",
                    f"{rel}:{lineno}",
                    f"'{ref}' is outside the vetted action register.",
                    "Prefer a run: step, a first-party or vendor action; else justify it.",
                )

            if not ref.startswith("actions/"):
                if not SHA_PIN.match(version):
                    f.add(
                        "P1",
                        f"{rel}:{lineno}",
                        f"Third-party action '{ref}' is pinned to '{version}', not a commit SHA.",
                        "Pin to a full 40-character SHA with the version in a trailing comment.",
                    )
                elif "#" not in line:
                    f.add(
                        "P2",
                        f"{rel}:{lineno}",
                        f"'{ref}' is SHA-pinned without a version comment.",
                        "Append '# vX' so the pin is readable.",
                    )

        # --- per-job structure ---------------------------------------------
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            where = f"{rel} · {job_name}"

            if "uses" in job:  # a job that calls another workflow
                if str(job["uses"]).startswith("./") and is_reusable:
                    f.add(
                        "P0",
                        where,
                        "Relative `uses:` inside a reusable workflow resolves to the "
                        "caller's repository, not this one.",
                        "Inline the job and share the logic via scripts/.",
                    )
                continue

            if "timeout-minutes" not in job:
                f.add("P1", where, "No timeout-minutes.", "Add an explicit timeout.")
            perms = job.get("permissions")
            if perms is None:
                f.add(
                    "P1",
                    where,
                    "No explicit permissions block.",
                    "Declare least-privilege permissions; see library-conventions.md §3.",
                )
            steps = job.get("steps") or []
            body = yaml.safe_dump(steps)

            if (
                "container" not in job
                and not CONTAINERLESS_OK.search(job_name)
                and not NEEDS_DAEMON.search(body)
            ):
                f.add(
                    "P1",
                    where,
                    "Runs outside a container and does not drive the Docker daemon.",
                    "Use the organisation build image, or justify the exception in a comment.",
                )

            if not SUMMARY_DELEGATES.search(body):
                f.add(
                    "P1",
                    where,
                    "Writes no step summary, and delegates to nothing that does.",
                    "Append a result block to $GITHUB_STEP_SUMMARY.",
                )

            # fail-fast defaults to true and hides sibling failures
            strategy = job.get("strategy") or {}
            if "matrix" in strategy and strategy.get("fail-fast") is not False:
                f.add(
                    "P1",
                    where,
                    "Matrix without fail-fast: false.",
                    "One run should report every problem, not just the first.",
                )

            # permission adequacy: declared scopes vs what the job actually does
            if isinstance(perms, dict):
                declared = {f"{k}: {v}" for k, v in perms.items()}
                for scope, evidence in SCOPE_EVIDENCE:
                    if evidence.search(body) and scope not in declared:
                        f.add(
                            "P1",
                            where,
                            f"Uses a capability requiring `{scope}` but does not declare it.",
                            "Add the scope, and document it in the caller-side permissions block.",
                        )
                # contents: write is for tagging/releasing, not for uploading an artifact
                if perms.get("contents") == "write":
                    writes = SCOPE_EVIDENCE[-1][1]
                    if not writes.search(body):
                        f.add(
                            "P1",
                            where,
                            "Declares `contents: write` but never tags, releases or pushes.",
                            "Artifact upload works under contents: read. Drop the write scope.",
                        )

            # a skipped dependency silently skips its dependents
            needs = job.get("needs")
            cond = str(job.get("if", ""))
            if needs and "result" not in cond and "always()" not in cond:
                deps = [needs] if isinstance(needs, str) else list(needs)
                optional = [d for d in deps if isinstance(jobs.get(d), dict) and "if" in jobs[d]]
                if optional:
                    f.add(
                        "P1",
                        where,
                        f"Depends on conditional job(s) {optional} without tolerating a skip.",
                        "Add: if: ${{ !cancelled() && needs.<dep>.result != 'failure' }}",
                    )

        # --- input budget ---------------------------------------------------
        if is_reusable:
            wc = on.get("workflow_call") or {}
            n_inputs = len(wc.get("inputs") or {})
            if n_inputs > 12:
                f.add(
                    "P2",
                    rel,
                    f"{n_inputs} inputs.",
                    "Move organisation-wide values to vars.*; keep inputs for "
                    "caller-specific values only.",
                )


def check_scripts(root: Path, f: Findings) -> None:
    # rglob, not glob: the library groups scripts into scripts/<domain>/, so a
    # non-recursive glob silently checked nothing and reported the directory missing.
    scripts = sorted((root / "scripts").rglob("*.sh")) if (root / "scripts").is_dir() else []
    if not scripts:
        f.add(
            "P2",
            "scripts/",
            "No shared scripts directory.",
            "Long or duplicated shell belongs in scripts/, not inline YAML.",
        )
        return
    # A file that another script `source`s runs in the caller's shell, so setting
    # shell options in it silently changes the caller. Those are libraries, not
    # scripts, and the option gate does not apply to them.
    corpus = "\n".join(p.read_text() for p in scripts)
    sourced = {p for p in scripts if f"source " in corpus and (
        f"/{p.name}" in corpus.split("source ", 1)[-1] or f"source {p.name}" in corpus
    ) and any(
        line.strip().startswith("source ") and p.name in line
        for line in corpus.splitlines()
    )}

    for path in scripts:
        text = path.read_text()
        rel = path.relative_to(root).as_posix()
        if not text.startswith("#!"):
            f.add("P1", rel, "No shebang.", "Start with #!/usr/bin/env bash.")
        if path in sourced:
            continue
        if "set -euo pipefail" not in text and "set -uo pipefail" not in text:
            f.add("P1", rel, "No `set -euo pipefail`.", "Fail fast on error.")


def check_docs(root: Path, f: Findings) -> None:
    for name in ("README.md", "CHANGELOG.md", "MIGRATION.md"):
        if not (root / name).is_file():
            f.add("P0", name, "Missing.", "Required by the documentation contract.")

    readme = root / "README.md"
    if not readme.is_file():
        return
    text = readme.read_text()
    low = text.lower()

    for section in REQUIRED_README_SECTIONS:
        if section not in low:
            f.add("P1", "README.md", f"No '{section}' section.")

    # GitHub slug: lowercase, strip punctuation, spaces -> hyphens, no collapsing.
    def slug(h: str) -> str:
        h = re.sub(r"[^\w\s-]", "", h.strip().lower())
        return h.replace(" ", "-")

    headings = {slug(m.group(2)) for m in re.finditer(r"^(#{2,6})\s+(.+)$", text, re.M)}
    for anchor in set(re.findall(r"\]\(#([^)]+)\)", text)):
        if anchor not in headings:
            f.add("P1", "README.md", f"Broken TOC anchor '#{anchor}'.")

    for i, block in enumerate(re.findall(r"```yaml\n(.*?)```", text, re.S)):
        try:
            yaml.safe_load(block)
        except Exception as exc:  # noqa: BLE001
            f.add("P1", "README.md", f"YAML example {i} does not parse: {str(exc)[:80]}")

    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        actual = {p.name for p in wf_dir.glob("*.yml")}
        for name in sorted(actual - {"ci.yml", "cd.yml"}):
            if name not in text:
                f.add("P2", "README.md", f"Workflow '{name}' is undocumented.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("library_dir")
    ap.add_argument("--strict", action="store_true", help="fail on P1/P2 as well as P0")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    root = Path(args.library_dir).resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")

    f = Findings()
    check_workflows(root, f)
    check_scripts(root, f)
    check_docs(root, f)

    if args.as_json:
        print(json.dumps({"findings": f.items}, indent=2))
    else:
        if not f.items:
            print(f"✅ {root.name}: no findings.")
        for sev in ("P0", "P1", "P2"):
            items = f.by_sev(sev)
            if not items:
                continue
            icon = {"P0": "❌", "P1": "⚠️ ", "P2": "ℹ️ "}[sev]
            print(f"\n{icon} {sev} ({len(items)})")
            for i in items:
                print(f"  {i['where']}: {i['message']}")
                if i["fix"]:
                    print(f"      → {i['fix']}")
        print(
            f"\nTotals: P0={len(f.by_sev('P0'))} "
            f"P1={len(f.by_sev('P1'))} P2={len(f.by_sev('P2'))}"
        )
        print("\nAlso run: actionlint · yamllint -s .github/workflows/ · shellcheck $(find scripts -name '*.sh')")

    if f.by_sev("P0"):
        return 1
    if args.strict and f.items:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
