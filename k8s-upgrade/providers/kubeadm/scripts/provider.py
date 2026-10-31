"""kubeadm provider hooks for core/scripts/workspace.py.

Every provider ships a scripts/provider.py with the same names (core/references/providers.md):

  INIT_FILES    files init creates in the cluster folder: name -> example in the provider's assets/
  SECRET_FILES  files that hold credentials: created mode 600, must be ignored by git
  lint(c)       offline checks of the provider's inputs: returns (notes, problems)

kubeadm reaches its nodes over SSH, so its inputs are the two node credential files.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

INIT_FILES = {"control-plane.md": "control-plane.md.example", "workers.md": "workers.md.example"}
SECRET_FILES = ["control-plane.md", "workers.md"]


def lint(c: Path) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    problems: list[str] = []
    cp, wk = c / "control-plane.md", c / "workers.md"
    nodes = [sys.executable, str(HERE / "nodes.py")]
    r = subprocess.run([*nodes, "--check", str(cp), str(wk)], capture_output=True, text=True)
    problems += [l.removeprefix("ERROR ") for l in r.stderr.splitlines() if l.startswith("ERROR")]
    if r.returncode == 0:
        notes.append(r.stdout.strip())
        listing = subprocess.run([*nodes, "--list", str(cp), str(wk)], capture_output=True, text=True).stdout
        if "\tpassword" in listing and not shutil.which("sshpass"):
            problems.append("sshpass is not installed and some nodes use password auth")
        for line in listing.splitlines():
            if "change-me" in line or "\t10.0.0." in line:
                problems.append(f"example row left in the credential files: {line.split(chr(9))[1]}")
    for tool in ("ssh", "curl"):
        if not shutil.which(tool):
            problems.append(f"{tool} is not installed")
    return notes, problems
