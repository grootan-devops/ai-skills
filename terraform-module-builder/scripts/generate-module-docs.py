#!/usr/bin/env python3
"""
generate-module-docs.py

Automated documentation generator and drift checker for Terraform modules.
Extracts:
1. Requirements & Providers from versions.tf
2. Managed Resources from all *.tf files
3. Inputs Specification table from variables.tf
4. Outputs Specification table from outputs.tf
5. Preserves existing architectural narratives and usage examples.
"""

import os
import sys
import re
import argparse
from pathlib import Path

def parse_versions(module_dir):
    versions_file = Path(module_dir) / "versions.tf"
    req_tf = ">= 1.5.0"
    providers = []
    if versions_file.exists():
        content = versions_file.read_text(encoding="utf-8")
        tf_match = re.search(r'required_version\s*=\s*"([^"]+)"', content)
        if tf_match:
            req_tf = tf_match.group(1)

        prov_matches = re.finditer(r'([a-zA-Z0-9_-]+)\s*=\s*\{[^}]*version\s*=\s*"([^"]+)"', content)
        for pm in prov_matches:
            providers.append((pm.group(1), pm.group(2)))

    if not providers:
        providers.append(("aws", ">= 6.0.0"))
    return req_tf, providers

def parse_resources(module_dir):
    resources = []
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root:
            continue
        for file in sorted(files):
            if file.endswith(".tf") and not file.startswith(("versions", "variables", "outputs")):
                content = (Path(root) / file).read_text(encoding="utf-8")
                res_matches = re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"', content)
                for rm in res_matches:
                    res_type = rm.group(1)
                    res_name = rm.group(2)
                    resources.append(f"`{res_type}.{res_name}`")
    return sorted(list(set(resources)))

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
            if "object(" in var_type:
                var_type = "`object({...})`"
            elif "list(object(" in var_type:
                var_type = "`list(object({...}))`"
            elif "map(object(" in var_type:
                var_type = "`map(object({...}))`"
            else:
                var_type = f"`{var_type}`"
        else:
            var_type = "`any`"

        # Check default / required
        default_match = re.search(r'default\s*=\s*([^\n#]+)', body)
        if default_match:
            default_val = default_match.group(1).strip()
            if default_val == "null":
                default_str = "`null`"
            elif default_val in ["{}", "[]"]:
                default_str = f"`{default_val}`"
            elif default_val in ["true", "false"]:
                default_str = f"`{default_val}`"
            else:
                # Truncate long defaults
                if len(default_val) > 25:
                    default_str = "`{...}`"
                else:
                    default_str = f"`{default_val}`"
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
    req_lines = ["| Requirement | Version |", "|---|---|", f"| `terraform` | `{req_tf}` |"]
    for p_name, p_ver in providers:
        req_lines.append(f"| `{p_name}` | `{p_ver}` |")
    requirements_table = "\n".join(req_lines)

    # Inputs Table
    in_lines = ["| Name | Description | Type | Default | Required |", "|---|---|---|---|:---:|"]
    for v in variables:
        in_lines.append(f"| `{v['name']}` | {v['description']} | {v['type']} | {v['default']} | {v['required']} |")
    inputs_table = "\n".join(in_lines)

    # Outputs Table
    out_lines = ["| Name | Description | Sensitive |", "|---|---|:---:|"]
    for o in outputs:
        out_lines.append(f"| `{o['name']}` | {o['description']} | {o['sensitive']} |")
    outputs_table = "\n".join(out_lines)

    return requirements_table, inputs_table, outputs_table

def build_full_readme(module_dir, req_tf, providers, resources, variables, outputs):
    mod_name = Path(module_dir).name
    prov_name = providers[0][0].upper() if providers else "Cloud"
    req_tbl, in_tbl, out_tbl = generate_markdown_tables(req_tf, providers, variables, outputs)

    res_bullets = "\n".join([f"- {r}: Managed resource." for r in resources[:10]])
    if not res_bullets:
        res_bullets = "- Primary cloud infrastructure resources."

    return f"""# {prov_name} {mod_name.replace('-', ' ').title()} Module

The `{mod_name}` module provisions production-grade, compliant cloud infrastructure adhering to enterprise security baselines, deterministic naming, and least-privilege principles.

### Architecture & Managed Resources
{res_bullets}

### Security & Compliance Guardrails
- **Customer-Managed Key Encryption**: Enforces Customer Managed Keys (CMEK) for sensitive data-at-rest.
- **In-Transit TLS Protection**: Enforces TLS 1.2+ minimum protocol versions.
- **Audit & Access Logging**: Configures automated logging and monitoring.
- **Resource Isolation**: Deploys into isolated, private network tiers by default.

---

## Requirements & Providers

{req_tbl}

---

## Usage Examples

### Minimal Working Example
```hcl
module "{mod_name}" {{
  source = "./"

  application = "core"
  environment = "prod"
  name        = "primary"
}}
```

### Complete Production Example
```hcl
module "{mod_name}" {{
  source = "./"

  application = "enterprise"
  environment = "prod"
  name        = "production-stack"

  tags = {{
    CostCenter = "PlatformEngineering"
  }}
}}
```

---

## Inputs Specification

{in_tbl}

---

## Outputs Specification

{out_tbl}
"""

def update_readme(module_dir, check_mode=False):
    readme_path = Path(module_dir) / "README.md"
    req_tf, providers = parse_versions(module_dir)
    resources = parse_resources(module_dir)
    variables = parse_variables(module_dir)
    outputs = parse_outputs(module_dir)

    req_tbl, in_tbl, out_tbl = generate_markdown_tables(req_tf, providers, variables, outputs)

    if not readme_path.exists():
        new_content = build_full_readme(module_dir, req_tf, providers, resources, variables, outputs)
    else:
        existing_content = readme_path.read_text(encoding="utf-8")
        new_content = existing_content

        # Patch Requirements Table
        req_pattern = r'(## Requirements & Providers\s*\n\n)([\s\S]*?)(\n\n---)'
        if re.search(req_pattern, new_content):
            new_content = re.sub(req_pattern, rf'\g<1>{req_tbl}\g<3>', new_content)

        # Patch Inputs Table
        in_pattern = r'(## Inputs Specification\s*\n\n)([\s\S]*?)(\n\n---|\Z)'
        if re.search(in_pattern, new_content):
            new_content = re.sub(in_pattern, rf'\g<1>{in_tbl}\g<3>', new_content)

        # Patch Outputs Table
        out_pattern = r'(## Outputs Specification\s*\n\n)([\s\S]*?)(\n\n---|\Z)'
        if re.search(out_pattern, new_content):
            new_content = re.sub(out_pattern, rf'\g<1>{out_tbl}\g<3>', new_content)

    if check_mode:
        if not readme_path.exists() or existing_content.strip() != new_content.strip():
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
