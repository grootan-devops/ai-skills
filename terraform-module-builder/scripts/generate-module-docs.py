#!/usr/bin/env python3
"""Refresh and check generated tables in an existing Terraform module README.

Architecture, security claims, and public Git examples require manual review.
"""

import sys
import re
import argparse
from pathlib import Path

def parse_versions(module_dir):
    versions_file = Path(module_dir) / "versions.tf"
    if not versions_file.exists():
        raise ValueError(f"{versions_file} is missing")
    req_tf = None
    providers = []
    content = versions_file.read_text(encoding="utf-8")
    tf_match = re.search(r'(?m)^[ \t]*required_version[ \t]*=[ \t]*"([^"]+)"', content)
    if tf_match:
        req_tf = tf_match.group(1)

    prov_matches = re.finditer(r'([a-zA-Z0-9_-]+)\s*=\s*\{[^}]*version\s*=\s*"([^"]+)"', content)
    for pm in prov_matches:
        providers.append((pm.group(1), pm.group(2)))
    if not req_tf or not providers:
        raise ValueError(f"{versions_file} needs required_version and versioned providers")
    return req_tf, providers


def default_expression(body):
    """Read one default expression without treating a multiline collection as `[` or `{`."""
    match = re.search(r'(?m)^[ \t]*default[ \t]*=[ \t]*', body)
    if not match:
        return None
    remainder = body[match.end():]
    first = remainder[:1]
    if first not in ("{", "["):
        return remainder.splitlines()[0].strip()

    opening, closing = ("{", "}") if first == "{" else ("[", "]")
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(remainder):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return remainder[:index + 1].strip()
    return remainder.splitlines()[0].strip()


def display_default(expression):
    if expression in ("null", "{}", "[]", "true", "false"):
        return f"`{expression}`"
    if expression.startswith(("{", "[")):
        compact = " ".join(line.strip() for line in expression.splitlines())
        compact = re.sub(r'[ \t]*=[ \t]*', ' = ', compact)
        if len(compact) > 60:
            compact = "{...}" if expression.startswith("{") else "[...]"
        return f"`{compact}`"
    return f"`{expression}`"


def parse_variables(module_dir):
    vars_file = Path(module_dir) / "variables.tf"
    variables = []
    if not vars_file.exists():
        return variables

    content = vars_file.read_text(encoding="utf-8")
    var_blocks = re.finditer(r'variable\s+"([^"]+)"\s*\{([\s\S]*?)\n\}', content)

    for vb in var_blocks:
        name = vb.group(1)
        body = vb.group(2)

        desc_match = re.search(r'description\s*=\s*"([^"]*)"', body)
        description = desc_match.group(1) if desc_match else ""

        # Determine type
        type_match = re.search(r'type\s*=\s*([^\n#]+)', body)
        if type_match:
            var_type = type_match.group(1).strip()
            if "list(object(" in var_type:
                var_type = "`list(object({...}))`"
            elif "map(object(" in var_type:
                var_type = "`map(object({...}))`"
            elif "object(" in var_type:
                var_type = "`object({...})`"
            else:
                var_type = f"`{var_type}`"
        else:
            var_type = "`any`"

        # Check default / required
        default_val = default_expression(body)
        if default_val is not None:
            default_str = display_default(default_val)
            required = "No"
        else:
            default_str = "**Required**"
            required = "Yes"

        variables.append({
            "name": name,
            "description": description,
            "type": var_type,
            "default": default_str,
            "required": required
        })
    return variables

def parse_outputs(module_dir):
    outputs_file = Path(module_dir) / "outputs.tf"
    outputs = []
    if not outputs_file.exists():
        return outputs

    content = outputs_file.read_text(encoding="utf-8")
    output_blocks = re.finditer(r'output\s+"([^"]+)"\s*\{([\s\S]*?)\n\}', content)

    for ob in output_blocks:
        name = ob.group(1)
        body = ob.group(2)

        desc_match = re.search(r'description\s*=\s*"([^"]*)"', body)
        description = desc_match.group(1) if desc_match else ""

        sensitive_match = re.search(r'sensitive\s*=\s*true', body)
        sensitive = "Yes" if sensitive_match else "No"

        outputs.append({
            "name": name,
            "description": description,
            "sensitive": sensitive
        })
    return outputs

def generate_markdown_tables(req_tf, providers, variables, outputs):
    # Requirements Table
    req_lines = ["| Requirement | Version |", "| --- | --- |", f"| `terraform` | `{req_tf}` |"]
    for p_name, p_ver in providers:
        req_lines.append(f"| `{p_name}` | `{p_ver}` |")
    requirements_table = "\n".join(req_lines)

    # Inputs Table
    in_lines = ["| Name | Description | Type | Default | Required |", "| --- | --- | --- | --- | :---: |"]
    for v in variables:
        description = f" {v['description']}" if v['description'] else ""
        in_lines.append(f"| `{v['name']}` |{description} | {v['type']} | {v['default']} | {v['required']} |")
    inputs_table = "\n".join(in_lines)

    # Outputs Table
    out_lines = ["| Name | Description | Sensitive |", "| --- | --- | :---: |"]
    for o in outputs:
        out_lines.append(f"| `{o['name']}` | {o['description']} | {o['sensitive']} |")
    outputs_table = "\n".join(out_lines)

    return requirements_table, inputs_table, outputs_table

def update_readme(module_dir, check_mode=False):
    readme_path = Path(module_dir) / "README.md"
    if not readme_path.exists():
        print(f"[MISSING README] {readme_path}: write reviewed architecture, security, "
              "and public Git examples before generating tables.", file=sys.stderr)
        return False
    try:
        req_tf, providers = parse_versions(module_dir)
    except ValueError as exc:
        print(f"[INVALID VERSIONS] {exc}", file=sys.stderr)
        return False
    variables = parse_variables(module_dir)
    outputs = parse_outputs(module_dir)

    req_tbl, in_tbl, out_tbl = generate_markdown_tables(req_tf, providers, variables, outputs)
    existing_content = readme_path.read_text(encoding="utf-8")
    new_content = existing_content

    # Patch generated tables without changing hand-written narratives or examples.
    req_pattern = r'(## Requirements & Providers\s*\n\n)([\s\S]*?)(\n\n---)'
    if not re.search(req_pattern, new_content):
        print(f"[MISSING SECTION] {readme_path}: Requirements & Providers", file=sys.stderr)
        return False
    new_content = re.sub(req_pattern, rf'\g<1>{req_tbl}\g<3>', new_content)

    in_pattern = r'(## Inputs Specification\s*\n\n)([\s\S]*?)(\n\n---|\Z)'
    if not re.search(in_pattern, new_content):
        print(f"[MISSING SECTION] {readme_path}: Inputs Specification", file=sys.stderr)
        return False
    new_content = re.sub(in_pattern, rf'\g<1>{in_tbl}\g<3>', new_content)

    out_pattern = r'(## Outputs Specification\s*\n\n)([\s\S]*?)(\n\n---|\Z)'
    if not re.search(out_pattern, new_content):
        print(f"[MISSING SECTION] {readme_path}: Outputs Specification", file=sys.stderr)
        return False
    new_content = re.sub(out_pattern, rf'\g<1>{out_tbl}\g<3>', new_content)

    if check_mode:
        if existing_content.strip() != new_content.strip():
            print(f"[DRIFT DETECTED] {readme_path} is out of date with variables.tf / outputs.tf.", file=sys.stderr)
            return False
        else:
            print(f"[OK] {readme_path} is strictly up to date.")
            return True
    else:
        readme_path.write_text(new_content, encoding="utf-8")
        print(f"Successfully generated/updated {readme_path}")
        return True

def main():
    parser = argparse.ArgumentParser(description="Generate or update module README documentation.")
    parser.add_argument("path", help="Path to the Terraform module directory")
    parser.add_argument("--check", action="store_true", help="Check for documentation drift without modifying files")
    args = parser.parse_args()

    module_dir = Path(args.path).resolve()
    if not module_dir.is_dir():
        print(f"Error: {module_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    success = update_readme(module_dir, check_mode=args.check)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
