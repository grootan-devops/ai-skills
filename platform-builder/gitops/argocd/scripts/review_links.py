#!/usr/bin/env python3
"""Links a developer opens to review a GitOps change before it goes live.

The review gate hands the dev the review branch (or a new environment branch), the MR/PR when
there is one, and each file to read. This prints those links in the GitOps host's web form:

    GitLab   <web>/-/tree/<branch>    <web>/-/blob/<branch>/<path>
    GitHub   <web>/tree/<branch>      <web>/blob/<branch>/<path>

An SSH or `.git` clone URL is turned into its https web address. The host kind is read from the
host name when it says `gitlab` or `github`; a self-hosted name that says neither needs
`--platform`. With `--changed <base>`, the files are the ones the branch changes against
`<base>` in a local clone (its own branches, else `origin/*` as last fetched), so the links
cover exactly what the dev should review.

    python3 review_links.py <gitops-url> --branch <b> [--file <path> ...] [--changed <base> --repo <clone>]
                            [--mr <url>] [--platform gitlab|github] [--json]

Nothing is written or pushed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

GITLAB, GITHUB = "gitlab", "github"
_SCP = re.compile(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+)$")
_URL = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$")


def web_base(url: str) -> Optional[str]:
    """The repository's https web address, or None when the URL is not one."""
    url = url.strip().rstrip("/")
    m = _URL.match(url) or _SCP.match(url)
    if not m:
        return None
    path = re.sub(r"\.git$", "", m.group(2).strip("/"))
    return f"https://{m.group(1).lower()}/{path}" if path else None


def host_kind(base: str, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    host = base.split("/")[2]
    return GITLAB if "gitlab" in host else GITHUB if "github" in host else None


def link(url: str, kind: str, ref: str, path: str = "", line: Optional[int] = None, tree: bool = False) -> str:
    """A branch (`tree`), folder (`tree` with a path) or file link, with an optional line anchor."""
    base = web_base(url)
    if not base:
        raise SystemExit(f"{url}: not a repository URL")
    sep = "/-/" if kind == GITLAB else "/"
    target = f"{base}{sep}{'tree' if tree else 'blob'}/{quote(ref, safe='/')}"
    return target + (f"/{quote(path, safe='/')}" if path else "") + (f"#L{line}" if line else "")


def links(url: str, branch: str, files: List[str], kind: str) -> Dict[str, object]:
    return {"branch": link(url, kind, branch, tree=True), "files": [link(url, kind, branch, f) for f in files]}


def _ref(repo: Path, name: str) -> str:
    """A branch as the clone knows it: its own branch, else the remote's as last fetched."""
    found = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"],
                           capture_output=True, text=True).returncode == 0
    return name if found else f"origin/{name}"


def changed(repo: Path, base: str, branch: str) -> List[str]:
    """Files `branch` adds or changes against `base`; deletions have nothing to open."""
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=d",
                          f"{_ref(repo, base)}...{_ref(repo, branch)}"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"cannot compare {branch} with {base}: {out.stderr.strip()}")
    return out.stdout.split()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url", help="the GitOps repository URL")
    ap.add_argument("--branch", required=True, help="the review branch, or the new environment branch")
    ap.add_argument("--file", action="append", default=[], help="a file on the branch to review")
    ap.add_argument("--changed", metavar="BASE", help="add the files the branch changes against BASE")
    ap.add_argument("--repo", type=Path, default=Path("."), help="local clone for --changed")
    ap.add_argument("--mr", help="the MR/PR link, printed first")
    ap.add_argument("--platform", choices=(GITLAB, GITHUB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    base = web_base(args.url)
    kind = host_kind(base, args.platform) if base else None
    if base and not kind:
        print(f"{base}: the host name says neither gitlab nor github. Ask the user and pass --platform.")
        return 2
    files = list(dict.fromkeys(args.file + (changed(args.repo, args.changed, args.branch) if args.changed else [])))
    result = {"mr": args.mr, **links(args.url, args.branch, files, kind)}
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if args.mr:
        print(f"review   {args.mr}")
    print(f"branch   {result['branch']}")
    for link in result["files"]:
        print(f"file     {link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
