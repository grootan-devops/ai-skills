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
>
> - **Invoked bare or ambiguously?** The skill halts and prompts you to select one of the 3 commands above (`terraform-module add`, `terraform-module update`, or `terraform-module audit`).
> - **Missing parameters?** The skill prompts for the specific missing inputs (e.g. provider/resource name or module path) rather than aborting.

---

## 2. Non-Negotiable Core Engineering Protocols

### 2.0. Resolve the Reference Repository First, and Read It

`terraform-modules` is the source of truth for every standard these modules are built to.
Before designing an API, naming a resource, choosing a security control, writing a README,
or classifying a release, resolve the repository per
[reference-repo-link.md](./references/reference-repo-link.md) and read the relevant section
of its `README.md` at the ref this run resolved. Upgrade behaviour is in its `MIGRATION.md`.

This skill deliberately does not carry a second copy of those standards. When a rule below
is the library's, the pointer *is* the rule — follow the link rather than working from
memory, and if the library and this skill disagree, the library wins and the disagreement is
worth reporting.

That repository's `README.md` also carries "current state" callouts recording where the
shipped modules diverge from the standard they document. Generate to the standard; treat a
divergence in existing code as a finding, not a precedent.

### 2.1. Trusted Providers, Latest Versions

- **Trusted tiers**: consume providers from the **`official`**, **`partner`**, or
  **`partner-premier`** tiers per the
  [Terraform Registry](https://registry.terraform.io/browse/providers?tier=official%2Cpartner%2Cpartner-premier).
- **Community providers**: an established, actively maintained one (`kreuzwerker/docker`,
  `cyrilgdn/postgresql`, `paultyng/git`) is a `P2` advisory — visible, not blocking. An
  obscure or unmaintained one is a `P1`, with a search for an official or partner
  alternative.
- **Latest version**: `add` and `update` both resolve the latest stable release of the
  target provider from the registry and target it. Never carry a version forward from
  memory or from a sibling module.
- **Registry module calls**: any `module "..."` sourced from the public registry declares an
  explicit `version` and tracks the latest release.

How that version is then written into `versions.tf` — minimum bound only, no artificial
upper bound, and how `required_version` is derived — is the library's contract: `README.md`
§4.4 *Version constraints*.

### 2.2. Project & Brand Neutrality

Reusable modules are 100% project-neutral: no project, company, or customer label in
resource names, locals, defaults, tags, documentation, or diagrams. Everything flows through
`var.application`, `var.environment`, and `var.name`.

Lifting a brand name out of the *prompt* that asked for the module is the part the library
cannot do for itself — see
[naming-standards.md §1](./references/naming-standards.md).

### 2.3. Provider Schema & Registry Docs-First Pipeline

Never generate a module from prose documentation alone. Execute:

```bash
terraform -chdir=<module_dir> init -backend=false
terraform -chdir=<module_dir> providers schema -json
```

Compare the schema JSON against the Registry documentation for the resolved provider
version, then inventory the capabilities:

- **Tier 1 (Mandatory security & CMEK)**: customer-managed keys, audit logging, TLS 1.2+,
  deletion protection.
- **Tier 2 (Production best practice)**: high-availability defaults, strongly-typed objects.
- **Tier 3 (Provider inherited)**: omit — a redundant block dates the module against
  provider defaults.
- **Tier 4 (Advanced/edge)**: conditional blocks or commented references.

See: [provider-schema-guide.md](./references/provider-schema-guide.md).

### 2.4. Capability-Aware Security

There is no blanket "KMS everywhere" rule. Each control on each resource resolves to one of
six statuses, and the library defines them: `README.md` §6 *Security Baselines*, with the
resolved AWS matrix in `docs/AWS.md`.

For a resource the matrix does not cover — including any Azure or GCP resource, since the
library is AWS-only — derive the status from the schema:
[security-capability-matrix.md](./references/security-capability-matrix.md).

### 2.5. Public API Design

The variable and output contract — mandatory attributes, strongly-typed `optional()`
objects, the `lookup()` ban, zero default credentials, curated outputs, and the rule against
exporting a whole resource — is the library's: `README.md` §4 *Module Contract*. Design
against it directly.

### 2.6. State Migration & Zero-Destroy Refactoring

Any refactor that changes a resource address, converts `count` to `for_each`, or renames a
resource ships a `moved` block. The guarantee, the block shapes, the permanent-retention
rule, and the plan assertion that proves it are in the library's `MIGRATION.md`; detecting
the change before it ships is
[state-migration-guide.md](./references/state-migration-guide.md).

Any change that needs an entry in `MIGRATION.md` gets one in the same change, not later.

---


## 3. Detailed Workflow Execution Procedures

### 3.1. Workflow 1: `terraform-module add`

Executed when provisioning a new module from scratch. It automatically orchestrates all required creation tasks:

1. **Resolve Latest Provider & Introspect Schema**:
   - Query latest provider version and extract `terraform providers schema -json`.
   - Read Registry docs for `<provider>/<resource_type>`.
2. **Design Public API & Security**:
   - Classify controls against the library's `README.md` §6 and `docs/AWS.md`.
   - Build strongly-typed `variables.tf` (zero secrets in defaults, zero `lookup()` abuse).
   - Design stable consumer `outputs.tf`.
3. **Scaffold Decomposed File Architecture**:
   - `<resource>.tf`: Primary resource declarations and child blocks.
   - `security.tf`: Encryption, access control, and TLS policies.
   - `variables.tf`, `outputs.tf`, `locals.tf`.
   - `versions.tf`: Pins latest provider version (`version = ">= <latest>"`) and derived core version.
   - `data.tf`: Platform context queries **only** if consumed by expressions.
4. **Generate Documentation (`add/update readme`)**:
   - Execute `python3 scripts/generate-module-docs.py <module_dir>` to render the module `README.md` to the standard in the library's `README.md` §7. The script generates the Requirements, Inputs, and Outputs tables; the Architecture, Guardrails, and Usage sections are written by hand.
5. **Scaffold Tests (`add/update test`)**:
   - Scaffold `tests/unit.tftest.hcl` with `mock_provider` for plan assertions and negative validation tests.
6. **Execute Verification & Security Gate (`validate module` & `audit security`)**:
   - Run `python3 scripts/check-module-rules.py <module_dir> --strict`.
   - Run the library's own gate from the repository root: `make verify`. It is the
     authority on whether a module is valid, and it checks contract rules this skill does
     not (required governance variables, README source pinning, no relative sources in any
     Markdown).
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
   - Executes `check-module-rules.py` and `make verify`, and classifies change impact
     against the library's `README.md` §8 (PATCH, MINOR, or MAJOR).
   - Writes the `MIGRATION.md` entry when the classification requires one.

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
