"""Decide which CI platform a repository targets.

Hostname matching alone is not sufficient and must never be the primary signal:
self-hosted instances are the norm, not the exception. `gitlab.contoso.com` happens to
contain "gitlab", but a GitLab instance at `scm.internal` or GitHub Enterprise at
`git.company.com` carry no hint at all.

Signals are therefore ranked by how directly they express INTENT rather than hosting:

  1. explicit   -- the caller said so. Always wins.
  2. files      -- .gitlab-ci.yml / .github/workflows/*.yml. Strongest inferred signal,
                   because it is a decision someone already made and committed.
  3. ci_env     -- $GITLAB_CI / $GITHUB_ACTIONS when running inside a pipeline.
  4. remote     -- origin URL. A hint, never a verdict.
  5. ambiguous  -- both or neither. This is a QUESTION, not a guess: a mirrored or
                   mid-migration repo has two valid answers and only the user knows which.

Usage:
    python3 platform.py [repo_path]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

GITLAB = "gitlab"
GITHUB = "github"


@dataclass
class Detection:
    platform: Optional[str]          # "gitlab" | "github" | None when unresolved
    confidence: str                  # "explicit" | "certain" | "likely" | "ambiguous" | "unknown"
    signals: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        """True when the agent must ask rather than proceed."""
        return self.confidence in ("ambiguous", "unknown", "likely")

    def summary(self) -> str:
        head = (f"platform={self.platform or 'UNRESOLVED'} "
                f"confidence={self.confidence}")
        body = "\n".join(f"  - {s}" for s in self.signals)
        tail = ""
        if self.needs_confirmation:
            opts = " or ".join(self.candidates) if self.candidates else "gitlab or github"
            tail = f"\n  >> ASK THE USER which platform to target ({opts}). Do not guess."
        return f"{head}\n{body}{tail}"


def _origin_url(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _host(url: str) -> str:
    """Extract the host from an https or scp-style git URL."""
    if not url:
        return ""
    m = re.match(r"(?:https?://)?(?:[^@/]+@)?([^/:]+)", url)
    return m.group(1).lower() if m else ""


def detect(repo_path: str, explicit: Optional[str] = None) -> Detection:
    repo = Path(repo_path).resolve()
    signals: List[str] = []

    if explicit:
        p = explicit.strip().lower()
        if p not in (GITLAB, GITHUB):
            raise ValueError(f"unknown platform '{explicit}' (expected {GITLAB} or {GITHUB})")
        return Detection(p, "explicit", [f"caller specified --platform {p}"])

    # 2. committed configuration -- an intent someone already recorded
    gl_file = (repo / ".gitlab-ci.yml").exists()
    gh_dir = repo / ".github" / "workflows"
    gh_files = sorted(p.name for p in gh_dir.glob("*.y*ml")) if gh_dir.is_dir() else []

    if gl_file:
        signals.append("found .gitlab-ci.yml")
    if gh_files:
        signals.append(f"found .github/workflows/ ({len(gh_files)} file(s))")

    if gl_file and gh_files:
        signals.append("BOTH platforms configured -- mirrored repo, or a migration in flight")
        return Detection(None, "ambiguous", signals, [GITLAB, GITHUB])
    if gl_file:
        return Detection(GITLAB, "certain", signals)
    if gh_files:
        return Detection(GITHUB, "certain", signals)

    # 3. running inside a pipeline
    if os.environ.get("GITLAB_CI") == "true":
        signals.append("environment: GITLAB_CI=true")
        return Detection(GITLAB, "certain", signals)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        signals.append("environment: GITHUB_ACTIONS=true")
        return Detection(GITHUB, "certain", signals)

    # 4. remote host -- a hint only. Deliberately returns "likely", which still routes
    #    to a confirmation prompt: where a repo is HOSTED is not always where its CI runs.
    url = _origin_url(repo)
    host = _host(url)
    if host:
        signals.append(f"origin host: {host}")
        if "gitlab" in host:
            signals.append("host name contains 'gitlab' (heuristic -- self-hosted instances "
                           "often carry no hint, so this is not conclusive)")
            return Detection(GITLAB, "likely", signals, [GITLAB, GITHUB])
        if "github" in host:
            signals.append("host name contains 'github' (heuristic -- GitHub Enterprise "
                           "instances often carry no hint, so this is not conclusive)")
            return Detection(GITHUB, "likely", signals, [GITLAB, GITHUB])
        signals.append("host name matches neither platform; likely self-hosted GitLab or "
                       "GitHub Enterprise. Probe /api/v4/version (GitLab) or /api/v3 (GHE), "
                       "or ask.")
    else:
        signals.append("no git remote configured")

    signals.append("no committed CI configuration to infer intent from")
    return Detection(None, "unknown", signals, [GITLAB, GITHUB])


def available_platforms() -> List[str]:
    base = Path(__file__).resolve().parent.parent.parent / "platforms"
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and (p / "ci_checks.py").exists()) if base.is_dir() else []


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    det = detect(target)
    print(f"Repository: {Path(target).resolve()}")
    print(det.summary())
    print(f"\nAdapters available: {', '.join(available_platforms()) or 'none'}")
