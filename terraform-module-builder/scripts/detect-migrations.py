#!/usr/bin/env python3
"""Compare a module with a Git ref and report candidate API/state migrations.

The HCL scan is textual. A consumer plan is still required to assess replacement
or destruction, especially for count/for_each key changes and default values.
"""

import sys
import re
import subprocess
import argparse
from pathlib import Path

def git_root(module_dir):
    result = subprocess.run(
        ["git", "-C", str(module_dir), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip()).resolve()


def get_git_content(file_path, git_ref="HEAD", root=None):
    root = root or git_root(Path(file_path).parent)
    rel_path = Path(file_path).resolve().relative_to(root).as_posix()
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{git_ref}:{rel_path}"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def baseline_tf_files(module_dir, git_ref, root):
    rel_dir = module_dir.relative_to(root).as_posix()
    pathspec = "." if rel_dir == "." else rel_dir
    result = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", git_ref, "--", pathspec],
        capture_output=True, text=True, check=True,
    )
    prefix = "" if rel_dir == "." else rel_dir + "/"
    return [root / name for name in result.stdout.splitlines()
            if name.startswith(prefix) and "/" not in name[len(prefix):]
            and name.endswith(".tf")]

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

def extract_resources(texts):
    resources = set()
    for content in texts:
        for match in re.finditer(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{',
                                 content, re.MULTILINE):
            resources.add(f"{match.group(1)}.{match.group(2)}")
    return resources


def moved_pairs(texts):
    pairs = set()
    for content in texts:
        for block in re.finditer(r'\bmoved\s*\{([^}]*)\}', content, re.DOTALL):
            source = re.search(r'^\s*from\s*=\s*([^\s#]+)', block.group(1), re.MULTILINE)
            target = re.search(r'^\s*to\s*=\s*([^\s#]+)', block.group(1), re.MULTILINE)
            if source and target:
                pairs.add((source.group(1), target.group(1)))
    return pairs

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

    try:
        root = git_root(module_dir)
        subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", args.compare_ref],
                       capture_output=True, text=True, check=True)
        old_files = baseline_tf_files(module_dir, args.compare_ref, root)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: cannot read Git baseline {args.compare_ref!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    vars_file = module_dir / "variables.tf"
    outputs_file = module_dir / "outputs.tf"

    current_vars_text = vars_file.read_text(encoding="utf-8") if vars_file.exists() else ""
    current_outs_text = outputs_file.read_text(encoding="utf-8") if outputs_file.exists() else ""

    baseline_vars_text = get_git_content(vars_file, args.compare_ref, root) or ""
    baseline_outs_text = get_git_content(outputs_file, args.compare_ref, root) or ""
    old_texts = [get_git_content(path, args.compare_ref, root) or "" for path in old_files]
    current_texts = [path.read_text(encoding="utf-8") for path in module_dir.glob("*.tf")]

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

    old_resources = extract_resources(old_texts)
    current_resources = extract_resources(current_texts)
    moves = moved_pairs(current_texts)
    for address in sorted(old_resources - current_resources):
        if any(source == address and target in current_resources for source, target in moves):
            patch_changes.append(f"Resource address '{address}' has a moved block; verify it against consumer state")
        else:
            breaking_changes.append(
                f"Removed resource address: '{address}' (possible destruction; inspect state and moved blocks)")
    for address in sorted(current_resources - old_resources):
        if not any(target == address and source in old_resources for source, target in moves):
            minor_changes.append(f"Added resource address: '{address}'")

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
    for change in patch_changes:
        print(f"  - {change}")

if __name__ == "__main__":
    main()
