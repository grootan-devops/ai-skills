---
name: terraform-module-builder
description: >-
  Industrial-grade, multi-cloud Terraform module engineering platform. Use this skill when
  the user asks to create, update, refactor, or audit production Terraform modules across
  AWS, Azure, GCP, or Kubernetes. Always uses the latest provider versions.
---

# Terraform Module Builder Skill

An industrial-grade, multi-cloud engineering skill that automates the complete lifecycle of production Terraform modules across **AWS**, **Azure**, **GCP**, and **Kubernetes**.

---

## 1. Streamlined Command Suite & Interactive Guardrails

To eliminate confusion and keep the user experience clean, all module lifecycle tasks are unified into **three comprehensive, high-leverage workflows**:

| # | Command | Mandatory Arguments | Unified Execution Workflow |
|:---:|---|---|---|
| **1** | `terraform-module add` | `<provider>` `<resource_type>` `<module_name>` | **End-to-End Creation**: Resolves latest provider version & schema → audits capabilities → generates decomposed HCL → generates canonical 8-section `README.md` → scaffolds native & Terratest tests → executes security audit & validation → checks release readiness. |
| **2** | `terraform-module update` (or `refactor`) | `<module_path>` | **End-to-End Upgrade & Modernization**: Bumps provider to latest version → analyzes AST & scaffolds `moved.tf` blocks for zero-destroy state safety → modernizes types (bans `lookup()` abuse) → refreshes `README.md` → updates tests → validates syntax & emits SemVer report. |
| **3** | `terraform-module audit` (or repository) | `<module_path_or_repo>` | **Read-Only Verification**: Audits version compatibility, dead/unwired variables, unused context data sources, tag clobber risks, doc drift, and security baselines without modifying code. |

> [!IMPORTANT]
> **Interactive Guardrail**:
> - **Invoked bare or ambiguously?** The skill halts and prompts you to select one of the 3 commands above (`terraform-module add`, `terraform-module update`, or `terraform-module audit`).
> - **Missing parameters?** The skill prompts for the specific missing inputs (e.g. provider/resource name or module path) rather than aborting.

---

## 2. Non-Negotiable Core Engineering Protocols

### 2.1. Always Use Trusted Official/Partner Providers and Latest Versions
- **Trusted Provider Tiers**: Production modules should strictly consume providers belonging to the **`official`**, **`partner`**, or **`partner-premier`** tiers per the [Terraform Registry](https://registry.terraform.io/browse/providers?tier=official%2Cpartner%2Cpartner-premier).
- **Community Provider Governance**:
  - **Reputable & Well-Maintained Community Providers**: Established community providers with high adoption and active maintenance (e.g. `kreuzwerker/docker`, `cyrilgdn/postgresql`, `paultyng/git`) are flagged as advisory notices (`P2`) for visibility, but allowed without blocking changes.
  - **Untrusted / Obscure Community Providers**: Flagged as **`P1` warnings**, with automatic search and recommendation for an official, partner, or verified community alternative.
- **Latest Version Enforcement**: When creating (`terraform-module add`) or modernizing (`terraform-module update`), the skill **always resolves and targets the latest stable release** of the target cloud provider.
- In `versions.tf`, declare the latest provider version as the minimum constraint:
  ```hcl
  terraform {
    required_version = ">= 1.5.0"

    required_providers {
      aws = {
        source  = "hashicorp/aws"
        version = ">= 6.64.0" # Always pin to the latest verified release
      }
    }
  }
  ```
- Child modules declare minimum bounds only (`version = ">= <latest_version>"`), omitting artificial upper constraints (`< 7.0.0`) so consumer stacks maintain upgrade flexibility.
- **Module Call Governance**: Any child module calls (`module "..."`) using the public registry must declare an explicit `version` constraint and stay up to date with the latest release.

### 2.2. Absolute Project & Brand Neutrality
- Reusable modules must remain 100% project-neutral.
- **Zero Project Names**: Never include internal project names, company names, or brand labels (e.g. `Plainr`, `takween`, `xyz`) in resource names, locals, defaults, tags, documentation, or diagrams.
- Parameterize naming generically: `${var.application}-${var.environment}-${var.name}`.
- See: [naming-standards.md](./references/naming-standards.md).

### 2.3. Provider Schema & Registry Docs-First Pipeline
Never generate modules from prose documentation alone. Execute:
```bash
terraform -chdir=<module_dir> init -backend=false
terraform -chdir=<module_dir> providers schema -json
```
- Compare schema JSON with official Registry documentation for the latest provider version.
- Inventory capabilities into:
  - **Tier 1 (Mandatory Security & CMEK)**: Customer-managed keys, audit logging, TLS 1.2+, deletion protection.
  - **Tier 2 (Production Best Practices)**: High-availability defaults, strongly-typed objects.
  - **Tier 3 (Provider Inherited)**: Omit redundant provider-level defaults.
  - **Tier 4 (Advanced/Edge Features)**: Conditional blocks or commented references.
- See: [provider-schema-guide.md](./references/provider-schema-guide.md).

### 2.4. Capability-Aware Security Model
Do **not** enforce blanket "KMS everywhere" rules. Match each resource to its real cloud capabilities:
- `required`: Mandatory encryption, logging, or deletion protection (S3, RDS, Secrets Manager).
- `recommended`: Production defaults with caller override.
- `optional`: Niche or advanced opt-in features.
- `provider_managed`: Rely on cloud provider default without redundant blocks.
- `not_supported` / `not_applicable`: Never invent synthetic parameters for non-existent controls (IAM, Route Tables).
- See: [security-capability-matrix.md](./references/security-capability-matrix.md).

### 2.5. Variable API Design Contract
- **Mandatory**: `description`, `type` (explicit primitives, collections, or strongly-typed structural objects).
- **Conditional**: `default` (optional variables only), `sensitive` (secrets only), `nullable` (when null is invalid), `ephemeral` (temporary inputs only), `validation` (genuine domain restrictions).
- **Zero Default Credentials**: Never provide default passwords, tokens, or mock secrets.
- **Banned**: `lookup()` used to emulate weak object typing. Permitted only on open-ended dynamic maps.
- See: [module-api-contract.md](./references/module-api-contract.md).

### 2.6. Deliberate Consumer Outputs
- Export IDs, ARNs, endpoints, and structured maps.
- Do **not** output full raw provider resource objects (`value = aws_resource.this`) by default.
- Set `sensitive = true` **only** when the output contains sensitive data.

### 2.7. State Migration & Zero-Destroy Refactoring
- Any refactor altering resource addresses, converting `count` to `for_each`, or renaming resources **must** append `moved` blocks to `moved.tf`.
- Historical `moved` blocks are preserved permanently.
- Pre-refactor plans must assert zero unexpected `delete` actions.
- See: [state-migration-guide.md](./references/state-migration-guide.md).

---

## 3. Detailed Workflow Execution Procedures

### 3.1. Workflow 1: `terraform-module add`
Executed when provisioning a new module from scratch. It automatically orchestrates all required creation tasks:
1. **Resolve Latest Provider & Introspect Schema**:
   - Query latest provider version and extract `terraform providers schema -json`.
   - Read Registry docs for `<provider>/<resource_type>`.
2. **Design Public API & Security**:
   - Classify controls via `security-capability-matrix.md` (CMEK, TLS, deletion protection).
   - Build strongly-typed `variables.tf` (zero secrets in defaults, zero `lookup()` abuse).
   - Design stable consumer `outputs.tf`.
3. **Scaffold Decomposed File Architecture**:
   - `<resource>.tf`: Primary resource declarations and child blocks.
   - `security.tf`: Encryption, access control, and TLS policies.
   - `variables.tf`, `outputs.tf`, `locals.tf`.
   - `versions.tf`: Pins latest provider version (`version = ">= <latest>"`) and derived core version.
   - `data.tf`: Platform context queries **only** if consumed by expressions.
4. **Generate Documentation (`add/update readme`)**:
   - Execute `python3 scripts/generate-module-docs.py <module_dir>` to render the canonical 8-section `README.md`.
5. **Scaffold Tests (`add/update test`)**:
   - Scaffold `tests/unit.tftest.hcl` with `mock_provider` for plan assertions and negative validation tests.
6. **Execute Verification & Security Gate (`validate module` & `audit security`)**:
   - Run `terraform fmt` and `terraform validate`.
   - Run `python3 scripts/check-module-rules.py <module_dir> --strict`.
   - Emit Release Readiness Summary.

---

### 3.2. Workflow 2: `terraform-module update` (or `refactor`)
Executed when modernizing, upgrading, or refactoring an existing module. It automatically bundles all maintenance tasks:
1. **Upgrade Provider & Core Versions (`bump versions` & `upgrade module`)**:
   - Bumps provider constraints to latest stable release in `versions.tf`.
   - Audits changelogs for breaking changes or deprecated attributes.
2. **State Migration Analysis (`migrate module`)**:
   - Executes `python3 scripts/detect-migrations.py <module_path>`.
   - Detects resource renames or key re-indexing and automatically appends `moved {}` blocks to `moved.tf`.
   - Verifies pre-refactor plan to ensure **zero unexpected resource destroys**.
3. **Modernize Variable Typing & Code Architecture**:
   - Replaces weak `list(any)` and `lookup()` calls with strongly-typed `optional()` objects.
   - Decomposes monolithic files into logical subsystem files.
   - Inverts tag merge precedence to protect reserved governance tags (`merge(var.tags, local.governance_tags)`).
   - Prunes dead variables and unconsumed context data sources.
4. **Synchronize Documentation (`add/update readme`)**:
   - Runs `python3 scripts/generate-module-docs.py <module_dir>` to update Requirements, Inputs, and Outputs tables.
5. **Update Tests (`add/update test`)**:
   - Updates `tests/*.tftest.hcl` and Terratest fixtures to reflect updated inputs.
6. **Validate & Emit SemVer Change Report**:
   - Executes `check-module-rules.py` and classifies change impact (PATCH, MINOR, or MAJOR).

---

### 3.3. Workflow 3: `terraform-module audit` (or repository)
Executed for non-destructive inspection, health-checking, and CI compliance verification:
1. Runs `python3 scripts/check-module-rules.py <module_path>`.
2. Verifies:
   - Trusted provider tiers (enforcing `official`, `partner`, or `partner-premier` per [Terraform Registry](https://registry.terraform.io/browse/providers?tier=official%2Cpartner%2Cpartner-premier); flagging `community` or untrusted providers as `P1` warnings).
   - Up-to-date provider versions (flagging constraints locking out or lagging behind latest releases as `P1`/`P2` warnings).
   - Registry module governance (enforcing explicit `version` constraints on child module calls).
   - Version gate compatibility (flags write-only args on TF < 1.11).
   - Dead/unwired variables and unused context queries.
   - Tag governance clobber risks.
   - Cleartext credentials and fake default passwords.
   - Non-neutral project branding.
3. Runs `python3 scripts/generate-module-docs.py <module_path> --check` to flag documentation drift.
4. Outputs prioritized findings report (P0, P1, P2) with actionable remediation steps.
