#!/usr/bin/env python3
"""Validate README/docs links, anchors, examples and optional CI-library topic coverage."""
import argparse
import json
from pathlib import Path

from documentation import check_documentation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library_dir", type=Path)
    parser.add_argument("--no-contract", action="store_true", help="Check links/examples without CI-specific required topics")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.library_dir.is_dir():
        parser.error(f"Not a directory: {args.library_dir}")
    findings = check_documentation(args.library_dir, contract=not args.no_contract)
    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for finding in findings:
            print(f"{finding['severity']} {finding['where']}: {finding['message']}")
        print(f"{len(findings)} documentation finding(s).")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
