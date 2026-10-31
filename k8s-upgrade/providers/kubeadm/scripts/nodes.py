#!/usr/bin/env python3
"""Read the node credential files of a k8s-upgrade workspace.

    python3 nodes.py --check <control-plane.md> <workers.md>
    python3 nodes.py --list  <control-plane.md> <workers.md>
    python3 nodes.py --get <node> <field> <control-plane.md> <workers.md>

Each file holds one Markdown table with the columns name, host, user, auth and, as needed,
port, password, ssh_key_path and sudo_password (see references/workspace.md). Rows of the
first file are control planes, rows of the second are workers.

--check validates both files: mode 600, required columns, auth is password or key, the key
file exists, and no node name repeats. It prints nothing secret.
--list prints one line per node: role, name, host, port, user, auth (no secrets).
--get prints one field of one node; remote.sh uses it to read a password into a variable.
sudo_password falls back to password when empty.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

REQUIRED = ("name", "host", "user", "auth")
FIELDS = ("name", "host", "port", "user", "auth", "password", "ssh_key_path", "sudo_password")


def parse_table(path: Path) -> list[dict[str, str]]:
    """Return the rows of the first Markdown table in the file."""
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if header is not None and rows:
                break
            continue
        inner = line[1:-1] if line.endswith("|") and not line.endswith("\\|") else line[1:]
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        row = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header)}
        rows.append(row)
    return rows


def load(cp_file: Path, worker_file: Path) -> list[dict[str, str]]:
    nodes = []
    for role, path in (("control-plane", cp_file), ("worker", worker_file)):
        for row in parse_table(path):
            node = {f: row.get(f, "") for f in FIELDS}
            node["role"] = role
            node["port"] = node["port"] or "22"
            node["auth"] = node["auth"].lower()
            if not node["sudo_password"]:
                node["sudo_password"] = node["password"]
            nodes.append(node)
    return nodes


def check(cp_file: Path, worker_file: Path) -> list[str]:
    errors = []
    for path in (cp_file, worker_file):
        if not path.is_file():
            errors.append(f"{path}: missing")
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            errors.append(f"{path}: mode {mode:o}, must be 600 (chmod 600 {path})")
        if not parse_table(path):
            errors.append(f"{path}: no table rows")
    if errors:
        return errors
    seen = set()
    for n in load(cp_file, worker_file):
        label = f"{n['role']} {n['name'] or '?'}"
        for f in REQUIRED:
            if not n[f]:
                errors.append(f"{label}: {f} is empty")
        if n["name"] in seen:
            errors.append(f"{label}: name repeats")
        seen.add(n["name"])
        if n["auth"] == "password":
            if not n["password"]:
                errors.append(f"{label}: auth is password but password is empty")
        elif n["auth"] == "key":
            key = Path(os.path.expanduser(n["ssh_key_path"])) if n["ssh_key_path"] else None
            if key is None or not key.is_file():
                errors.append(f"{label}: auth is key but ssh_key_path does not exist")
        else:
            errors.append(f"{label}: auth must be password or key")
        if not n["port"].isdigit():
            errors.append(f"{label}: port is not a number")
    if not any(n["role"] == "control-plane" for n in load(cp_file, worker_file)):
        errors.append(f"{cp_file}: no control-plane rows")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--get", nargs=2, metavar=("NODE", "FIELD"))
    ap.add_argument("control_plane", type=Path)
    ap.add_argument("workers", type=Path)
    args = ap.parse_args()

    if args.check:
        errors = check(args.control_plane, args.workers)
        for e in errors:
            print(f"ERROR {e}", file=sys.stderr)
        if not errors:
            nodes = load(args.control_plane, args.workers)
            cps = sum(n["role"] == "control-plane" for n in nodes)
            print(f"OK {cps} control plane(s), {len(nodes) - cps} worker(s)")
        return 1 if errors else 0

    nodes = load(args.control_plane, args.workers)
    if args.list:
        for n in nodes:
            print("\t".join((n["role"], n["name"], n["host"], n["port"], n["user"], n["auth"])))
        return 0

    name, field = args.get
    if field not in FIELDS and field != "role":
        print(f"unknown field {field}", file=sys.stderr)
        return 2
    for n in nodes:
        if n["name"] == name:
            value = n[field]
            if field == "ssh_key_path" and value:
                value = os.path.expanduser(value)
            sys.stdout.write(value)
            return 0
    print(f"node {name} not in the credential files", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
