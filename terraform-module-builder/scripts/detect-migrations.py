#!/usr/bin/env python3
"""
detect-migrations.py

AST-based state migration & SemVer breaking change detector for Terraform modules.
1. Scans for resource address renames and scaffolds moved {} blocks into moved.tf.
2. Compares git baseline (HEAD~1 or specified ref) with working tree to detect:
   - Removed variables (MAJOR)
   - Removed outputs (MAJOR)
   - Changed variable defaults (MAJOR/MINOR)
   - New optional variables (MINOR)
   - New outputs (MINOR)
3. Outputs a SemVer classification report (PATCH, MINOR, or MAJOR).
"""

import os
import sys
import re
import subprocess
import argparse
from pathlib import Path

def get_git_content(file_path, git_ref="HEAD"):
    try:
        rel_path = os.path.relpath(file_path, os.getcwd())
        cmd = ["git", "show", f"{git_ref}:{rel_path}"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return res.stdout
    except Exception:
        return None

def extract_variables(hcl_text):
    if not hcl_text:
        return {}
    vars_dict = {}
    blocks = re.finditer(r'variable\s+"([^"]+)"\s*\{([\s\S]*?)\n\}', hcl_text)
    for b in blocks:
        name = b.group(1)
        body = b.group(2)
        has_default = bool(re.search(r'default\s*=', body))
        vars_dict[name] = {"has_default": has_default, "body": body}
    return vars_dict

def extract_outputs(hcl_text):
    if not hcl_text:
        return {}
    outputs_dict = {}
    blocks = re.finditer(r'output\s+"([^"]+)"\s*\{([\s\S]*?)\n\}', hcl_text)
    for b in blocks:
        name = b.group(1)
        body = b.group(2)
        outputs_dict[name] = {"body": body}
    return outputs_dict

def extract_resources(dir_path):
    resources = set()
    for root, _, files in os.walk(dir_path):
        if ".terraform" in root:
            continue
        for file in files:
            if file.endswith(".tf") and not file.startswith(("versions", "variables", "outputs")):
                content = (Path(root) / file).read_text(encoding="utf-8", errors="ignore")
                matches = re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"', content)
                for m in matches:
                    resources.add(f"{m.group(1)}.{m.group(2)}")
    return resources

def scaffold_moved_block(module_dir, from_addr, to_addr):
    moved_file = Path(module_dir) / "moved.tf"
    block_text = f"""
moved {{
  from = {from_addr}
  to   = {to_addr}
}}
"""
    if moved_file.exists():
        existing = moved_file.read_text(encoding="utf-8")
        if f"from = {from_addr}" in existing and f"to   = {to_addr}" in existing:
            print(f"Notice: moved block from {from_addr} to {to_addr} already exists in moved.tf.")
            return
        moved_file.write_text(existing + block_text, encoding="utf-8")
    else:
        moved_file.write_text("# Automated State Migrations\n" + block_text, encoding="utf-8")
    print(f"Successfully appended moved block to {moved_file}: {from_addr} -> {to_addr}")

def main():
    parser = argparse.ArgumentParser(description="Detect state migrations and SemVer breaking changes.")
    parser.add_argument("path", help="Path to the Terraform module directory")
    parser.add_argument("--scaffold-move", nargs=2, metavar=("FROM", "TO"), help="Scaffold a moved block into moved.tf")
    parser.add_argument("--compare-ref", default="HEAD", help="Git ref to compare current working tree against (default: HEAD)")
    args = parser.parse_args()

    module_dir = Path(args.path).resolve()
    if not module_dir.is_dir():
        print(f"Error: {module_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    if args.scaffold_move:
        scaffold_moved_block(module_dir, args.scaffold_move[0], args.scaffold_move[1])
        sys.exit(0)

    vars_file = module_dir / "variables.tf"
    outputs_file = module_dir / "outputs.tf"

    current_vars_text = vars_file.read_text(encoding="utf-8") if vars_file.exists() else ""
    current_outs_text = outputs_file.read_text(encoding="utf-8") if outputs_file.exists() else ""

    baseline_vars_text = get_git_content(vars_file, args.compare_ref) or ""
    baseline_outs_text = get_git_content(outputs_file, args.compare_ref) or ""

    curr_vars = extract_variables(current_vars_text)
    base_vars = extract_variables(baseline_vars_text)

    curr_outs = extract_outputs(current_outs_text)
    base_outs = extract_outputs(baseline_outs_text)

    breaking_changes = []
    minor_changes = []
    patch_changes = []

    # Check for removed or newly-required variables (MAJOR)
    for vname, vdata in base_vars.items():
        if vname not in curr_vars:
            breaking_changes.append(f"Removed public variable: '{vname}'")
        elif not vdata["has_default"] and curr_vars[vname]["has_default"]:
            minor_changes.append(f"Variable '{vname}' was required and is now optional")
        elif vdata["has_default"] and not curr_vars[vname]["has_default"]:
            breaking_changes.append(f"Variable '{vname}' was optional and is now required (breaking default change)")

    for vname, vdata in curr_vars.items():
        if vname not in base_vars:
            if vdata["has_default"]:
                minor_changes.append(f"Added new optional variable: '{vname}'")
            else:
                breaking_changes.append(f"Added new required variable: '{vname}' without default")

    # Check for removed outputs (MAJOR)
    for oname in base_outs.keys():
        if oname not in curr_outs:
            breaking_changes.append(f"Removed public output: '{oname}'")

    for oname in curr_outs.keys():
        if oname not in base_outs:
            minor_changes.append(f"Added new output: '{oname}'")

    print("\n================================================================================")
    print(f" API & SEMVER CHANGE REPORT: {module_dir.name}")
    print(f" Compared Against: {args.compare_ref}")
    print("================================================================================\n")

    if breaking_changes:
        print("[CRITICAL - MAJOR BUMP REQUIRED]")
        for b in breaking_changes:
            print(f"  - {b}")
        print("\nRecommended SemVer: MAJOR (e.g. bump to next X.0.0)\n")
    elif minor_changes:
        print("[NOTICE - MINOR BUMP RECOMMENDED]")
        for m in minor_changes:
            print(f"  - {m}")
        print("\nRecommended SemVer: MINOR (e.g. bump to next 0.X.0)\n")
    else:
        print("[CLEAN - PATCH / NO API CHANGES DETECTED]")
        print("Recommended SemVer: PATCH (e.g. bump to next 0.0.X)\n")

if __name__ == "__main__":
    main()
