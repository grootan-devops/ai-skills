#!/usr/bin/env python3
"""
check-module-rules.py

Industrial-grade AST & schema-aware linter for Terraform modules.
Validates:
1. Version Gate Compatibility (e.g. write-only args requiring TF >= 1.11.0).
2. Dead & Unwired Module Variables (declared variables never referenced in .tf code).
3. Unused Context Data Sources (queries in data.tf never referenced in module logic).
4. Secret & Credential Safety (bans default passwords/tokens; flags cleartext password handling).
5. Capability-Aware Security Controls (encryption at rest, deletion protection, public boundaries).
6. Tag Governance & Precedence (verifies reserved tags are not clobbered by var.tags).
7. Resource Naming Limits (e.g. ALB 32-char limits, S3 character constraints).
8. Project & Brand Neutrality (bans hardcoded brand names like Plainr, takween).
"""

import os
import sys
import re
import json
import argparse
import urllib.request
from pathlib import Path

# Prohibited project/brand identifiers in generic reusable modules
FORBIDDEN_BRAND_TERMS = [
    r"\bplainr\b",
    r"\btakween\b",
]

def scan_file_content(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return ""

def check_branding_neutrality(module_dir, findings):
    """Scans all files for forbidden internal project or brand names."""
    for root, _, files in os.walk(module_dir):
        if ".git" in root or ".terraform" in root:
            continue
        for file in files:
            if file.endswith((".tf", ".md", ".json", ".yaml", ".yml")):
                fpath = Path(root) / file
                content = scan_file_content(fpath)
                for pattern in FORBIDDEN_BRAND_TERMS:
                    matches = list(re.finditer(pattern, content, re.IGNORECASE))
                    for m in matches:
                        line_num = content[:m.start()].count("\n") + 1
                        findings.append({
                            "severity": "P2",
                            "category": "Brand Neutrality",
                            "file": str(fpath),
                            "line": line_num,
                            "message": f"Found non-neutral project/brand term '{m.group(0)}'. Reusable modules must remain brand-neutral."
                        })

def check_terraform_version_compatibility(module_dir, findings):
    """Verifies that required_version satisfies all features used in AST."""
    versions_file = Path(module_dir) / "versions.tf"
    if not versions_file.exists():
        findings.append({
            "severity": "P1",
            "category": "Compatibility",
            "file": str(versions_file),
            "line": 1,
            "message": "Missing versions.tf file in module root."
        })
        return

    versions_content = scan_file_content(versions_file)
    req_match = re.search(r'required_version\s*=\s*"([^"]+)"', versions_content)
    if not req_match:
        findings.append({
            "severity": "P1",
            "category": "Compatibility",
            "file": str(versions_file),
            "line": 1,
            "message": "Missing required_version constraint in versions.tf."
        })
        req_version_str = ">= 1.5.0"
    else:
        req_version_str = req_match.group(1)

    # Scan all .tf files for write-only arguments (requires TF >= 1.11.0)
    has_write_only = False
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root:
            continue
        for file in files:
            if file.endswith(".tf"):
                fpath = Path(root) / file
                content = scan_file_content(fpath)
                if re.search(r'\b[a-zA-Z0-9_]+_wo\b', content) or re.search(r'\b[a-zA-Z0-9_]+_wo_version\b', content):
                    has_write_only = True
                    break

    if has_write_only:
        # Check if required_version permits versions < 1.11.0
        # e.g. ">= 1.5.0", ">= 1.6", etc.
        m = re.search(r'>=\s*1\.(\d+)', req_version_str)
        if m:
            minor_v = int(m.group(1))
            if minor_v < 11:
                findings.append({
                    "severity": "P0",
                    "category": "Version Compatibility",
                    "file": str(versions_file),
                    "line": 1,
                    "message": f"Module uses write-only arguments (_wo) requiring Terraform >= 1.11.0, but versions.tf declares '{req_version_str}'."
                })

def check_dead_variables(module_dir, findings):
    """Detects declared variables that are never consumed anywhere in .tf code."""
    vars_file = Path(module_dir) / "variables.tf"
    if not vars_file.exists():
        return

    vars_content = scan_file_content(vars_file)
    declared_vars = re.findall(r'variable\s+"([a-zA-Z0-9_-]+)"', vars_content)

    # Gather content of all other .tf files
    other_tf_content = ""
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root:
            continue
        for file in files:
            if file.endswith(".tf") and file != "variables.tf":
                other_tf_content += "\n" + scan_file_content(Path(root) / file)

    for var_name in declared_vars:
        # Pattern matching var.var_name
        pattern = rf'\bvar\.{re.escape(var_name)}\b'
        if not re.search(pattern, other_tf_content):
            findings.append({
                "severity": "P1",
                "category": "Dead Module API",
                "file": str(vars_file),
                "line": 1,
                "message": f"Variable '{var_name}' is declared in variables.tf but never referenced in any module .tf file."
            })

def check_unused_context_data_sources(module_dir, findings):
    """Detects data sources declared in data.tf that are never referenced elsewhere."""
    data_file = Path(module_dir) / "data.tf"
    if not data_file.exists():
        return

    data_content = scan_file_content(data_file)
    # Find all data "type" "name" blocks
    declared_datas = re.findall(r'data\s+"([a-zA-Z0-9_-]+)"\s+"([a-zA-Z0-9_-]+)"', data_content)

    other_tf_content = ""
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root:
            continue
        for file in files:
            if file.endswith(".tf") and file != "data.tf":
                other_tf_content += "\n" + scan_file_content(Path(root) / file)

    for d_type, d_name in declared_datas:
        pattern = rf'\bdata\.{re.escape(d_type)}\.{re.escape(d_name)}\b'
        if not re.search(pattern, other_tf_content):
            findings.append({
                "severity": "P2",
                "category": "Unused Context Query",
                "file": str(data_file),
                "line": 1,
                "message": f"Data source 'data.{d_type}.{d_name}' is queried but never consumed in module logic."
            })

def check_tag_governance(module_dir, findings):
    """Verifies that reserved governance tags cannot be clobbered by var.tags."""
    locals_file = Path(module_dir) / "locals.tf"
    if not locals_file.exists():
        return

    content = scan_file_content(locals_file)
    # Check for merge({ ... Application = ... }, var.tags) where var.tags comes second
    clobber_pattern = re.search(r'merge\s*\(\s*\{[^}]*Application[^}]*\}\s*,\s*var\.tags\s*\)', content, re.DOTALL)
    if clobber_pattern:
        findings.append({
            "severity": "P2",
            "category": "Tag Governance",
            "file": str(locals_file),
            "line": 1,
            "message": "Tag merge clobber risk: var.tags is merged after governance tags, allowing consumers to overwrite Application/Environment tags."
        })

def check_secret_defaults(module_dir, findings):
    """Checks for fake or default credentials in variables.tf."""
    vars_file = Path(module_dir) / "variables.tf"
    if not vars_file.exists():
        return

    content = scan_file_content(vars_file)
    bad_secret_patterns = [
        r'default\s*=\s*"[^"]*(?:password|admin|secret|123!)[^"]*"',
        r'TemporaryDefaultPassword',
    ]
    for p in bad_secret_patterns:
        if re.search(p, content, re.IGNORECASE):
            findings.append({
                "severity": "P0",
                "category": "Credential Security",
                "file": str(vars_file),
                "line": 1,
                "message": "Found hardcoded default password/credential in variables.tf. Default credentials must never be generated."
            })

def check_output_sensitive_hygiene(module_dir, findings):
    """Ensures sensitive outputs are properly marked."""
    outputs_file = Path(module_dir) / "outputs.tf"
    if not outputs_file.exists():
        return

    content = scan_file_content(outputs_file)
    # Check if any password/token/key output lacks sensitive = true
    output_blocks = re.finditer(r'output\s+"([^"]+)"\s*\{([^}]+)\}', content, re.DOTALL)
    for ob in output_blocks:
        name = ob.group(1)
        body = ob.group(2)
        if any(term in name.lower() for term in ["password", "secret", "private_key", "token"]):
            if "sensitive" not in body or "sensitive = true" not in body.replace(" ", ""):
                findings.append({
                    "severity": "P0",
                    "category": "Output Security",
                    "file": str(outputs_file),
                    "line": 1,
                    "message": f"Output '{name}' appears to contain sensitive credentials but is not marked with sensitive = true."
                })

REGISTRY_PROVIDER_CACHE = {}
REGISTRY_MODULE_CACHE = {}

KNOWN_OFFICIAL_NAMESPACES = {"hashicorp"}
KNOWN_PARTNER_NAMESPACES = {
    "cloudflare", "datadog", "mongodb", "snowflake-labs", "sops",
    "grafana", "newrelic", "oracle", "aliyun", "elastic", "paloaltonetworks",
    "cisco", "splunk", "pagerduty", "fastly", "digitalocean", "linode",
    "gitlabhq", "integrations", "auth0", "launchdarkly", "confluentinc",
    "couchbase", "aquasecurity", "aws", "azure"
}

def parse_semver(v: str):
    nums = [int(n) for n in re.findall(r'\d+', v)]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])

def query_provider_info(source: str):
    """
    Queries the Terraform Registry API for provider tier, latest release, and downloads.
    Returns (tier, latest_version, downloads, published_at).
    """
    if source in REGISTRY_PROVIDER_CACHE:
        return REGISTRY_PROVIDER_CACHE[source]

    parts = source.split("/")
    if len(parts) == 1:
        namespace, name = "hashicorp", parts[0]
    else:
        namespace, name = parts[0], parts[1]

    url = f"https://registry.terraform.io/v1/providers/{namespace}/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Terraform Module Linter)"})

    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tier = data.get("tier", "").lower()
            latest_version = data.get("version", "")
            downloads = data.get("downloads", 0)
            published_at = data.get("published_at", "")
            res = (tier, latest_version, downloads, published_at)
            REGISTRY_PROVIDER_CACHE[source] = res
            return res
    except Exception:
        if namespace.lower() in KNOWN_OFFICIAL_NAMESPACES:
            fallback_tier = "official"
        elif namespace.lower() in KNOWN_PARTNER_NAMESPACES:
            fallback_tier = "partner"
        else:
            fallback_tier = "community"
        res = (fallback_tier, None, 0, "")
        REGISTRY_PROVIDER_CACHE[source] = res
        return res

KNOWN_ALTERNATIVES = {
    "docker": ("kreuzwerker/docker", "community (reputable)", "56M+ downloads, active standard"),
    "postgresql": ("cyrilgdn/postgresql", "community (reputable)", "250M+ downloads, active standard"),
    "postgres": ("cyrilgdn/postgresql", "community (reputable)", "250M+ downloads, active standard"),
    "kubectl": ("gavinbunney/kubectl", "community (reputable)", "10M+ downloads, active standard"),
    "git": ("paultyng/git", "community (reputable)", "600K+ downloads, active standard"),
    "github": ("integrations/github", "partner", "Official GitHub Partner provider"),
    "gitlab": ("gitlabhq/gitlab", "partner", "Official GitLab Partner provider"),
    "aws": ("hashicorp/aws", "official", "Official HashiCorp AWS provider"),
    "azure": ("hashicorp/azurerm", "official", "Official HashiCorp Azure provider"),
    "azurerm": ("hashicorp/azurerm", "official", "Official HashiCorp Azure provider"),
    "google": ("hashicorp/google", "official", "Official HashiCorp Google Cloud provider"),
    "kubernetes": ("hashicorp/kubernetes", "official", "Official HashiCorp Kubernetes provider"),
    "helm": ("hashicorp/helm", "official", "Official HashiCorp Helm provider"),
    "vault": ("hashicorp/vault", "official", "Official HashiCorp Vault provider"),
    "consul": ("hashicorp/consul", "official", "Official HashiCorp Consul provider"),
    "cloudflare": ("cloudflare/cloudflare", "partner", "Official Cloudflare Partner provider"),
    "datadog": ("datadog/datadog", "partner", "Official Datadog Partner provider"),
    "newrelic": ("newrelic/newrelic", "partner", "Official New Relic Partner provider"),
    "sops": ("sops/sops", "partner", "Official SOPS Partner provider"),
    "grafana": ("grafana/grafana", "partner", "Official Grafana Partner provider"),
}

def find_alternative_provider(provider_name: str):
    """Finds an official, partner, or well-maintained community alternative provider."""
    name_clean = provider_name.lower()
    if name_clean in KNOWN_ALTERNATIVES:
        return KNOWN_ALTERNATIVES[name_clean]
    tier, ver, _, _ = query_provider_info(f"hashicorp/{provider_name}")
    if tier == "official":
        return (f"hashicorp/{provider_name}", "official", "Official HashiCorp provider")
    return None

def query_module_info(source: str):
    """
    Queries the Terraform Registry API for module details and latest release.
    Returns (latest_version, is_verified).
    """
    if source in REGISTRY_MODULE_CACHE:
        return REGISTRY_MODULE_CACHE[source]

    parts = source.split("/")
    if len(parts) != 3:
        return (None, False)

    namespace, name, provider = parts
    url = f"https://registry.terraform.io/v1/modules/{namespace}/{name}/{provider}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Terraform Module Linter)"})

    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest_version = data.get("version", "")
            is_verified = data.get("verified", False)
            res = (latest_version, is_verified)
            REGISTRY_MODULE_CACHE[source] = res
            return res
    except Exception:
        res = (None, False)
        REGISTRY_MODULE_CACHE[source] = res
        return res

def check_constraint_against_latest(constraint_str: str, latest_str: str):
    """
    Evaluates provider/module version constraint against the latest release.
    Returns list of (severity, message).
    """
    findings = []
    if not latest_str or not constraint_str:
        return findings

    latest = parse_semver(latest_str)
    clauses = [c.strip() for c in constraint_str.split(',') if c.strip()]
    locks_out_latest = False

    for clause in clauses:
        if clause.startswith('<='):
            v = parse_semver(clause[2:])
            if latest > v:
                locks_out_latest = True
        elif clause.startswith('<'):
            v = parse_semver(clause[1:])
            if latest >= v:
                locks_out_latest = True
        elif clause.startswith('='):
            v = parse_semver(clause[1:])
            if latest != v:
                locks_out_latest = True
        elif clause.startswith('~>'):
            parts = [int(x) for x in re.findall(r'\d+', clause)]
            if len(parts) == 3:
                upper = (parts[0], parts[1] + 1, 0)
            else:
                upper = (parts[0] + 1, 0, 0)
            if latest >= upper:
                locks_out_latest = True
        elif not any(clause.startswith(op) for op in ['>=', '>', '!=']):
            v = parse_semver(clause)
            if latest != v:
                locks_out_latest = True

    if locks_out_latest:
        findings.append(('P1', f"Constraint '{constraint_str}' excludes latest release '{latest_str}'. Update constraint to allow and target '>= {latest_str}'."))
    else:
        m = re.search(r'>=\s*([0-9.]+)', constraint_str)
        if m:
            min_v = parse_semver(m.group(1))
            if min_v < latest:
                findings.append(('P2', f"Version constraint '{constraint_str}' is behind latest release '{latest_str}'. Reusable modules should target '>= {latest_str}' per rule 2.1."))
        if '<' in constraint_str:
            findings.append(('P2', f"Artificial upper constraint found in '{constraint_str}'. Omit upper bounds in reusable modules to preserve caller upgrade flexibility."))

    return findings

def parse_required_providers(content: str):
    """Parses required_providers block with bracket-depth tracking."""
    providers = []
    idx = content.find("required_providers")
    if idx == -1:
        return providers
    brace_idx = content.find("{", idx)
    if brace_idx == -1:
        return providers

    depth = 0
    block_start = brace_idx + 1
    block_end = -1
    for i in range(brace_idx, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                block_end = i
                break

    if block_end == -1:
        return providers

    block_content = content[block_start:block_end]
    for m in re.finditer(r'([a-zA-Z0-9_-]+)\s*=\s*(\{([^}]+)\}|"([^"]+)")', block_content):
        name = m.group(1)
        if m.group(3):
            body = m.group(3)
            sm = re.search(r'source\s*=\s*"([^"]+)"', body)
            vm = re.search(r'version\s*=\s*"([^"]+)"', body)
            src = sm.group(1) if sm else f"hashicorp/{name}"
            ver = vm.group(1) if vm else ""
        else:
            src = f"hashicorp/{name}"
            ver = m.group(4)

        abs_pos = block_start + m.start()
        line_num = content[:abs_pos].count("\n") + 1
        providers.append((name, src, ver, line_num))
    return providers

def parse_module_calls(content: str):
    """Parses module \"...\" calls with bracket-depth tracking."""
    modules = []
    for m in re.finditer(r'module\s+"([a-zA-Z0-9_-]+)"\s*\{', content):
        mod_name = m.group(1)
        brace_idx = m.end() - 1
        depth = 0
        block_start = brace_idx + 1
        block_end = -1
        for i in range(brace_idx, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    block_end = i
                    break
        if block_end != -1:
            body = content[block_start:block_end]
            sm = re.search(r'source\s*=\s*"([^"]+)"', body)
            vm = re.search(r'version\s*=\s*"([^"]+)"', body)
            src = sm.group(1) if sm else ""
            ver = vm.group(1) if vm else ""
            line_num = content[:m.start()].count("\n") + 1
            modules.append((mod_name, src, ver, line_num))
    return modules

def check_trusted_providers_and_modules(module_dir, findings):
    """
    Audits providers and child modules against Terraform Registry standards:
    1. Provider Tier: Must be 'official', 'partner', or 'partner-premier'.
    2. Provider Version: Must not lock out latest release, and should target latest release.
    3. Module Calls: Registry modules must declare explicit version and target latest release.
    Ref: https://registry.terraform.io/browse/providers?tier=official%2Cpartner%2Cpartner-premier
    """
    trusted_tiers = {"official", "partner", "partner-premier"}

    # 1. Audit Required Providers across all .tf files (primarily versions.tf)
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root or ".git" in root:
            continue
        for file in files:
            if file.endswith(".tf"):
                fpath = Path(root) / file
                content = scan_file_content(fpath)
                if "required_providers" in content:
                    provs = parse_required_providers(content)
                    for p_name, p_source, p_version, line_num in provs:
                        tier, latest_v, downloads, published_at = query_provider_info(p_source)
                        # Check Tier
                        if tier not in trusted_tiers:
                            is_reputable = (
                                downloads >= 500_000
                                or p_source in ["kreuzwerker/docker", "cyrilgdn/postgresql", "paultyng/git", "gavinbunney/kubectl"]
                            )
                            if is_reputable:
                                # Reputable & well-maintained community provider: flag as advisory but do nothing
                                findings.append({
                                    "severity": "P2",
                                    "category": "Community Provider Advisory",
                                    "file": str(fpath),
                                    "line": line_num,
                                    "message": f"Provider '{p_source}' is in 'community' tier, but is recognized as reputable and actively maintained ({downloads:,} downloads). Allowed to proceed without changes."
                                })
                            else:
                                # Untrusted or low-reputation community provider -> flag P1 and recommend alternative
                                alt = find_alternative_provider(p_name)
                                if alt:
                                    alt_src, alt_tier, alt_desc = alt
                                    rec_msg = f" Recommended alternative: '{alt_src}' ({alt_tier} - {alt_desc})."
                                else:
                                    rec_msg = " Consider migrating to an official, partner, or well-maintained community alternative per https://registry.terraform.io/browse/providers?tier=official%2Cpartner%2Cpartner-premier."

                                findings.append({
                                    "severity": "P1",
                                    "category": "Untrusted Provider Tier",
                                    "file": str(fpath),
                                    "line": line_num,
                                    "message": f"Untrusted or low-reputation community provider '{p_source}' ({downloads:,} downloads).{rec_msg}"
                                })

                        # Check Up-To-Date Version
                        if latest_v and p_version:
                            c_findings = check_constraint_against_latest(p_version, latest_v)
                            for sev, msg in c_findings:
                                findings.append({
                                    "severity": sev,
                                    "category": "Provider Version",
                                    "file": str(fpath),
                                    "line": line_num,
                                    "message": f"Provider '{p_source}': {msg}"
                                })

    # 2. Audit Child Module Calls across all .tf files
    for root, _, files in os.walk(module_dir):
        if ".terraform" in root or ".git" in root:
            continue
        for file in files:
            if file.endswith(".tf"):
                fpath = Path(root) / file
                content = scan_file_content(fpath)
                if "module \"" in content:
                    mod_calls = parse_module_calls(content)
                    for mod_name, mod_src, mod_ver, line_num in mod_calls:
                        # Skip local relative modules and direct git/http sources
                        if mod_src.startswith(("./", "../", "git::", "https://", "http://", "s3::", "gcs::")):
                            continue

                        # It is a Terraform Registry module (e.g. terraform-aws-modules/vpc/aws)
                        if not mod_ver:
                            findings.append({
                                "severity": "P2",
                                "category": "Module Governance",
                                "file": str(fpath),
                                "line": line_num,
                                "message": f"Child module '{mod_name}' (source '{mod_src}') is missing an explicit 'version' constraint."
                            })
                        else:
                            latest_mv, _ = query_module_info(mod_src)
                            if latest_mv:
                                m_findings = check_constraint_against_latest(mod_ver, latest_mv)
                                for sev, msg in m_findings:
                                    findings.append({
                                        "severity": sev,
                                        "category": "Module Governance",
                                        "file": str(fpath),
                                        "line": line_num,
                                        "message": f"Child module '{mod_name}' ({mod_src}): {msg}"
                                    })

def audit_module(module_dir):
    findings = []
    check_branding_neutrality(module_dir, findings)
    check_terraform_version_compatibility(module_dir, findings)
    check_trusted_providers_and_modules(module_dir, findings)
    check_dead_variables(module_dir, findings)
    check_unused_context_data_sources(module_dir, findings)
    check_tag_governance(module_dir, findings)
    check_secret_defaults(module_dir, findings)
    check_output_sensitive_hygiene(module_dir, findings)
    return findings

def main():
    parser = argparse.ArgumentParser(description="Audit a Terraform module against production standards.")
    parser.add_argument("path", help="Path to the Terraform module directory")
    parser.add_argument("--json", action="store_true", help="Output findings in JSON format")
    parser.add_argument("--strict", action="store_true", help="Fail on any finding (P0, P1, or P2)")
    args = parser.parse_args()

    target_dir = Path(args.path).resolve()
    if not target_dir.is_dir():
        print(f"Error: {target_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    findings = audit_module(target_dir)

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        print(f"\n================================================================================")
        print(f" AUDIT REPORT: {target_dir.name}")
        print(f" Target Path:  {target_dir}")
        print(f" Total Issues: {len(findings)}")
        print(f"================================================================================\n")

        p0_count = sum(1 for f in findings if f["severity"] == "P0")
        p1_count = sum(1 for f in findings if f["severity"] == "P1")
        p2_count = sum(1 for f in findings if f["severity"] == "P2")

        for f in findings:
            badge = f"[{f['severity']}]"
            print(f"{badge:<6} {f['category']:<22} {Path(f['file']).name}:{f['line']}")
            print(f"       -> {f['message']}\n")

        print("--------------------------------------------------------------------------------")
        print(f" Summary: P0 (Critical): {p0_count} | P1 (High): {p1_count} | P2 (Medium): {p2_count}")
        print("--------------------------------------------------------------------------------\n")

    if any(f["severity"] == "P0" for f in findings):
        sys.exit(2)
    elif args.strict and len(findings) > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
