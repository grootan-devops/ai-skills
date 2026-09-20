#!/usr/bin/env python3
"""Cross-repository public-release readiness checks."""

import hashlib
import re
import sys
from pathlib import Path


GITHUB = Path(__file__).resolve().parents[2]
REPOSITORIES = (
    "ai-skills", "argocd-gitops-tpl-library", "github-ci-library",
    "gitlab-ci-library", "helm-tpl-library", "playground",
    "terraform-modules", "toolkit",
)
RELEASABLE = set(REPOSITORIES) - {"playground", "toolkit"}
GOVERNANCE = (
    "LICENSE.md", "SECURITY.md", "CONTRIBUTING.md", "CODEOWNERS",
)
REMOVED_GOVERNANCE = (
    "CODE_OF_CONDUCT.md", "SUPPORT.md", "DEPRECATION.md", "RELEASING.md",
    "CI_REDESIGN_CHECKLIST.md",
)
AGPL_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
REPORTING_EMAIL = "platform-engineering@grootan.com"
PLACEHOLDERS = re.compile(
    r"my-org|github\.com/organization|registry\.example\.com|"
    r"gitlab\.example\.com|Takween-AI-SA"
)
LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def main():
    failures = []
    for name in REPOSITORIES:
        repo = GITHUB / name
        for filename in GOVERNANCE:
            if not (repo / filename).is_file():
                failures.append(f"{name}: missing {filename}")
        for filename in REMOVED_GOVERNANCE:
            if (repo / filename).exists():
                failures.append(f"{name}: obsolete governance file remains: {filename}")
        license_path = repo / "LICENSE.md"
        if license_path.exists():
            digest = hashlib.sha256(license_path.read_bytes()).hexdigest()
            if digest != AGPL_SHA256:
                failures.append(f"{name}: LICENSE.md is not the canonical AGPL v3 text")
        for filename in ("SECURITY.md", "CONTRIBUTING.md"):
            policy = repo / filename
            if policy.exists() and REPORTING_EMAIL not in policy.read_text():
                failures.append(f"{name}: {filename} does not use the reporting address")
        readme = repo / "README.md"
        if readme.exists():
            readme_text = readme.read_text()
            if "AGPL-3.0-only" not in readme_text:
                failures.append(f"{name}: README does not declare AGPL-3.0-only")
            if "Copyright 2026 Grootan Technologies Pvt Ltd." not in readme_text:
                failures.append(f"{name}: README copyright notice is missing")
            if name != "ai-skills" and "grootan-devops/ai-skills/blob/main/COMPATIBILITY.md" not in readme_text:
                failures.append(f"{name}: README does not link the compatibility contract")
        owners = repo / "CODEOWNERS"
        if owners.exists() and "@grootan-devops/platform-engineering" not in owners.read_text():
            failures.append(f"{name}: platform team missing from CODEOWNERS")
        if name in RELEASABLE:
            if (repo / "VERSION").read_text().strip() != "1.0.0":
                failures.append(f"{name}: VERSION is not 1.0.0")
            if "[1.0.0]" not in (repo / "CHANGELOG.md").read_text():
                failures.append(f"{name}: initial changelog entry missing")
            if "No migration is required" not in (repo / "MIGRATION.md").read_text():
                failures.append(f"{name}: initial migration statement missing")
        for document in repo.rglob("*.md"):
            if ".git" in document.parts:
                continue
            text = document.read_text(errors="replace")
            if PLACEHOLDERS.search(text):
                failures.append(f"{document.relative_to(GITHUB)}: legacy placeholder remains")
            for target in LINK.findall(text):
                target = target.split("#", 1)[0].strip()
                if not target or "://" in target or target.startswith(("mailto:", "#", "<")):
                    continue
                candidate = (document.parent / target).resolve()
                if not candidate.exists():
                    failures.append(
                        f"{document.relative_to(GITHUB)}: broken local link {target}"
                    )
    if failures:
        print("\n".join(f"ERROR: {item}" for item in sorted(set(failures))), file=sys.stderr)
        return 1
    print(f"Validated public-release contracts for {len(REPOSITORIES)} repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
