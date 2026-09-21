# Terraform Module Builder AI Skill (`terraform-module-builder`)

An industrial-grade, multi-cloud Terraform module engineering platform designed for AI coding assistants (Google Antigravity, Anthropic Claude, Cursor AI, Kiro, Codex, etc.) and human DevOps platform engineers.

This skill equips an AI agent to research official provider schemas, design, scaffold, audit, document, test, refactor, and release production-hardened Terraform modules across **AWS**, **Azure**, **Google Cloud Platform (GCP)**, and **Kubernetes**.

---

## 1. Quick Start & Unified Command Suite

All module lifecycle operations are driven through the unified **`terraform-module`** command suite:

```bash
# 1. End-to-End Module Creation (Schema introspection, capability audit, HCL, tests, docs)
terraform-module add <provider> <resource_type> <module_name>

# 2. End-to-End Upgrade & Modernization (Latest provider, AST moved.tf generation, SemVer)
terraform-module update <module_path>

# 3. Read-Only Compliance & Security Audit (AST linter, tag clobber risks, dead variables)
terraform-module audit <module_path_or_repo> [--strict]
```

> [!IMPORTANT]
> **Interactive Guardrails**:
>
> - **Invoked bare or ambiguously?** The skill halts, displays the 3 workflows above (`terraform-module add`, `terraform-module update`, `terraform-module audit`), and prompts you to select one.
> - **Missing mandatory arguments?** The skill prompts you specifically for the missing input(s) rather than rejecting your request.

---

## 2. Where the Standards Live

The engineering standards these modules are built to are **not** duplicated here. They are
the reference repository's, and the skill reads them at the ref each run resolves:

| Standard | Where |
| --- | --- |
| Module contract — variables, outputs, provider config, version constraints | `terraform-modules/README.md` §4 |
| Naming formula, length limits, truncation, governance tags and merge order | `terraform-modules/README.md` §5 |
| Security capability taxonomy, key governance, credential handling | `terraform-modules/README.md` §6 |
| Module `README.md` section standard | `terraform-modules/README.md` §7 |
| SemVer release levels | `terraform-modules/README.md` §8 |
| Test levels and the `make verify` gate | `terraform-modules/README.md` §9 |
| Upgrade behaviour, `moved` blocks, the zero-destruction guarantee | `terraform-modules/MIGRATION.md` |
| Per-service AWS security matrix and module catalog | `terraform-modules/docs/AWS.md` |

What the skill adds on top is judgement the library cannot record about itself: resolving
the latest trusted provider and reading its schema, classifying a control the matrix does
not yet cover, abstracting a brand name out of a prompt, detecting an address change before
it ships, and emitting the test templates. `SKILL.md` §2 is the runbook for all of it.

Those library sections also carry "current state" callouts recording where the shipped
`1.0.0` modules diverge from the standard they document — tag merge order, provider upper
bounds, name truncation. Generate to the standard; a divergence in existing code is a
finding, not a precedent.

---

## 3. End-to-End Walkthrough: Creating a Production Module

When creating a new module (`terraform-module add`), the skill runs through strict pre-flight validation gates before writing code.

### 3.1. Inputs & Derivation Matrix

| Parameter | Type | Source | Mandatory? | Derivation & Pre-Flight Validation Logic | Confirmation Required? |
| --- | --- | --- | :---: | --- | :---: |
| **`provider`** | String | **User** | **YES** | Cloud provider (e.g. `aws`, `azurerm`, `google`). Verified against official/partner tier. | **Yes** |
| **`resource_type`** | String | **User** | **YES** | Primary resource type (e.g. `aws_sqs_queue`, `aws_rds_cluster`). Probed in schema. | **Yes** |
| **`module_name`** | String | **User** | **YES** | Canonical module directory name (e.g. `sqs`, `rds-cluster`). Validated for neutrality. | **Yes** |
| **`provider_version`** | String | **Skill** | *Auto* | Dynamically resolved latest stable release from Terraform Registry. | Displayed in table |
| **`min_tf_version`** | String | **Skill** | *Auto* | Dynamically calculated from features: `>= 1.6.0` (tests), `>= 1.10.0` (ephemeral), `>= 1.11.0` (write-only). | Auto-resolved |
| **`cmek_requirement`** | Enum | **Skill** | *Auto* | Evaluated via Security Capability Matrix: `required` for S3/RDS, `not_applicable` for IAM. | Displayed in table |
| **`governance_tags`** | Map | **Skill** | *Auto* | Standard audit tags (`Application`, `Environment`, `Name`, `ManagedBy`) via inverted merge. | Displayed in table |
| **`test_suites`** | List | **Skill** | *Auto* | Scaffolds native mock tests (`tests/*.tftest.hcl`) and integration suites (`test/*.go`). | Displayed in table |

### 3.2. Pre-Flight Verification Gates

1. **Trusted Provider & Version Resolution Gate**:
   - Validates that the provider belongs to `official`, `partner`, or verified community tiers.
   - Resolves latest stable release version from the registry.
   - **ABORTS** on obscure, unmaintained, or untrusted community providers.
2. **Schema & Argument Introspection Gate**:
   - Executes `terraform providers schema -json` to inspect official argument schemas.
   - Distinguishes required, optional, computed, sensitive, and write-only (`_wo`) arguments.
   - **ABORTS** if requested resource does not exist in the target provider.
3. **Security Capability Matrix Gate**:
   - Evaluates Customer-Managed Key (CMEK) encryption, TLS 1.2+, IMDSv2, and deletion protection against cloud API capabilities.
   - Enforces explicit `kms_key_arn` for all data-at-rest services.
   - **ABORTS** on synthetic security parameters for resources lacking native support.
4. **Project & Brand Neutrality Gate**:
   - Scans proposed module paths, variable defaults, locals, and documentation for internal brand names.
   - **ABORTS** if proprietary company or project names are detected.
5. **Zero-Destroy State Migration Gate** (for `terraform-module update`):
   - Compares working tree AST with git baseline using `detect-migrations.py`.
   - Generates `moved.tf` blocks for renamed addresses.
   - Speculative plan must confirm **0 unexpected deletions**.

### 3.3. Step-by-Step Module Engineering Workflow

1. **Schema & Capability Introspection**:
   - Resolves latest provider release and dumps `terraform providers schema -json`.
   - Classifies arguments into Tier 1 (Security/CMEK), Tier 2 (Production Best Practices), Tier 3 (Provider Defaults), and Tier 4 (Advanced/Edge).
2. **Architecture & Parameters Confirmation Gate**:
   - Displays proposed inputs, derived versions, CMEK requirements, and module path to user.
   - Prompts for user confirmation before scaffolding.
3. **Decomposed HCL Scaffolding**:
   - `versions.tf`: Pins minimum provider version (`version = ">= <latest>"`) and dynamic `required_version`.
   - `main.tf`: Clean, decomposed resource blocks with deterministic naming and inverted tag merges.
   - `variables.tf`: Strongly-typed structural objects with `optional(type, default)`. Zero `lookup()` abuse.
   - `outputs.tf`: Curated IDs, ARNs, endpoints, and structured maps.
4. **Canonical 8-Section `README.md` Scaffolding**:
   - Generates production documentation via `generate-module-docs.py` including Requirements, Providers, Modules, Resources, Inputs, Outputs, Security Baselines, and Usage.
5. **Testing Suite Scaffolding**:
   - Scaffolds native `tests/*.tftest.hcl` using `mock_provider` for fast offline unit validation.
   - Scaffolds Terratest idempotency test suite (`test/*.go`).
6. **Compliance & Static Rule Audit**:
   - Executes `check-module-rules.py --strict` to verify 100% compliance across compatibility, unwired variables, tag clobber risks, and brand neutrality.

---

## 4. Flow Diagram: End-to-End Module Creation (`terraform-module add`)

```mermaid
flowchart TD
    StartAdd([User Invokes: terraform-module add]) --> CollectAddInputs[Collect: provider, resource_type, module_name]

    %% Gate 1: Provider Check
    CollectAddInputs --> ResolveProvider[Gate 1: Verify Provider Tier & Fetch Latest Release<br/>Check official/partner registry tiers]
    ResolveProvider --> ProviderCheck{Trusted Provider & Version Resolved?}
    ProviderCheck -- Untrusted / Obscure Provider --> AbortProvider[ABORT: Show Warning & Suggest Official Alternative]

    %% Gate 2: Schema Introspection
    ProviderCheck -- Verified --> RunSchema[Gate 2: Introspect Machine-Readable Schema<br/>terraform providers schema -json]
    RunSchema --> SchemaCheck{Valid Resource in Schema?}
    SchemaCheck -- Resource Not Found --> AbortSchema[ABORT: Show Valid Provider Resource Types]

    %% Gate 3: Capability Audit
    SchemaCheck -- Schema Parsed --> AuditCaps[Gate 3: Evaluate Security Capability Matrix<br/>CMEK KMS, TLS 1.2+, IMDSv2, Deletion Protection]
    AuditCaps --> PresentAddPlan[Present Architecture Matrix:<br/>• Provider version constraint<br/>• CMEK requirement: required/optional/N/A<br/>• Dynamic min TF version<br/>• Scaffolding file list]
    
    PresentAddPlan --> ConfirmAddGate{User Confirms Architecture?}
    ConfirmAddGate -- Refine --> CollectAddInputs

    %% Parallel Asset Scaffolding
    ConfirmAddGate -- Approved --> ScaffoldMain[Render main.tf: Atomic Resource<br/>Inverted Tag Merge & Deterministic Naming]
    ConfirmAddGate -- Approved --> ScaffoldVars[Render variables.tf: Strongly-Typed<br/>Zero lookup abuse, Zero hardcoded secrets]
    ConfirmAddGate -- Approved --> ScaffoldOuts[Render outputs.tf: Curated ARNs/IDs<br/>No raw resource object dumps]
    ConfirmAddGate -- Approved --> ScaffoldVers[Render versions.tf: Dynamic required_version<br/>version = '>= latest_version']

    %% Docs, Tests & Verification
    ScaffoldMain & ScaffoldVars & ScaffoldOuts & ScaffoldVers --> ScaffoldTests[Scaffold tests/*.tftest.hcl mock tests<br/>& Terratest test/*.go suite]
    ScaffoldTests --> GenerateDocs[Generate Canonical 8-Section README.md<br/>generate-module-docs.py]
    GenerateDocs --> RunAudit[Gate 4: Execute AST Rule Audit<br/>check-module-rules.py --strict]
    RunAudit --> AuditPass{100% Compliance?}
    AuditPass -- Issues Found --> FixHCL[Correct AST / Variable Drift]
    FixHCL --> RunAudit
    AuditPass -- Passed --> ModReady([Production-Hardened Module Released])
```

---

## 5. Flow Diagram: Module Upgrade & Modernization (`terraform-module update`)

```mermaid
flowchart TD
    StartUpdate([User Invokes: terraform-module update]) --> InputPath[Collect: module_path]

    %% Gate 1: AST Diff & Breaking Change Analysis
    InputPath --> RunDiff[Gate 1: Compare Working Tree against Git Baseline<br/>detect-migrations.py --compare-ref HEAD]
    RunDiff --> ChangeCheck{Breaking API Changes Detected?}
    
    %% AST Refactoring & State Safety
    ChangeCheck -- Renamed Resources / Address Changes --> GenMoved[Scaffold moved.tf Blocks<br/>detect-migrations.py --scaffold-move]
    ChangeCheck -- Variable Defaults Changed / Removed --> FlagBreaking[Emit SemVer Classification: MAJOR<br/>Flag breaking input changes]
    ChangeCheck -- Additions / Non-Breaking --> FlagMinor[Emit SemVer Classification: MINOR / PATCH]

    GenMoved & FlagBreaking & FlagMinor --> BumpProvider[Bump versions.tf Provider to Latest Release<br/>Recalculate dynamic required_version]
    BumpProvider --> ModernizeHCL[Modernize HCL Syntax:<br/>• Replace legacy lookup() with optional()<br/>• Enforce inverted tag merge]
    ModernizeHCL --> RefreshDocs[Refresh README.md Tables<br/>generate-module-docs.py]
    RefreshDocs --> RunFmt[Execute: terraform fmt -recursive & tflint]
    RunFmt --> SpecPlan[Gate 2: Run Speculative Plan<br/>Assert 0 Unexpected Resource Destructions]
    SpecPlan --> PlanCheck{Zero Unexpected Destroys?}
    PlanCheck -- Destructive Plan --> ReviewSafety[ABORT: Demand Missing moved.tf Block]
    ReviewSafety --> GenMoved
    PlanCheck -- Safe Plan --> UpdateComplete([Module Modernization Complete & Verified])
```

---

## 6. Strict Human-in-the-Loop Protocol (The 4 Gatekeepers)

```text
┌─────────────────────────┐
│ Gatekeeper 1            │ ──> Provider Schema & Version Verification
│ Schema Introspection    │     (Resolve latest provider version; inspect schema JSON)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 2            │ ──> Security Capability & Architecture Confirmation
│ Capability Audit        │     (CMEK KMS evaluation, TLS 1.2+, tag merge law -> User Confirms)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 3            │ ──> Staged Scaffolding & Speculative Plan Gate
│ Staged Generation       │     (Render decomposed HCL, canonical README, run test mocks)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 4            │ ──> Compliance Audit & SemVer Sign-Off
│ Compliance & Sign-Off   │     (check-module-rules.py --strict + make verify -> User Sign-Off)
└─────────────────────────┘
```

---

## 7. Automated Tooling Engine CLI Reference

The skill includes a suite of deterministic automation scripts in `scripts/`:

```bash
# 1. Audit a module for P0/P1/P2 issues (compatibility, dead variables, tag clobber risks):
python3 ai-skills/terraform-module-builder/scripts/check-module-rules.py terraform-modules/modules/aws/security/secrets-manager

# 2. Run module audit with strict enforcement (fails on P0, P1, and P2 warnings):
python3 ai-skills/terraform-module-builder/scripts/check-module-rules.py terraform-modules/modules/aws/compute/ecs --strict

# 3. Check for documentation drift in CI:
python3 ai-skills/terraform-module-builder/scripts/generate-module-docs.py terraform-modules/modules/aws/storage/s3 --check

# 4. Generate or refresh module canonical 8-section README:
python3 ai-skills/terraform-module-builder/scripts/generate-module-docs.py terraform-modules/modules/aws/storage/s3

# 5. Detect breaking API changes against git baseline:
python3 ai-skills/terraform-module-builder/scripts/detect-migrations.py terraform-modules/modules/aws/storage/s3

# 6. Scaffold an automated moved block into moved.tf:
python3 ai-skills/terraform-module-builder/scripts/detect-migrations.py terraform-modules/modules/aws/storage/s3 \
  --scaffold-move aws_s3_bucket.main aws_s3_bucket.this
```

These audit what the skill knows. The library's own gate is authoritative and checks rules
these scripts do not — run it from the reference repository root:

```bash
make verify      # contract checks + fmt + init + validate + terraform test
make contracts   # contract checks only; no Terraform binary required
```

---

## 8. Directory Layout

```text
ai-skills/terraform-module-builder/
├── README.md                           # Comprehensive skill documentation (this file)
├── SKILL.md                            # Main orchestrator & 3-workflow command dispatcher
├── assets/
│   ├── architecture-template.svg       # Neutral multi-cloud architecture & verification diagram
│   └── architecture-template.png       # High-resolution rendered asset
├── references/
│   ├── reference-repo-link.md          # Where the library is, and what to read in it
│   ├── provider-schema-guide.md        # Extracting & reading terraform providers schema -json
│   ├── naming-standards.md             # Brand abstraction; naming bounds outside AWS
│   ├── security-capability-matrix.md   # Deriving a control status; Azure/GCP starters
│   ├── state-migration-guide.md        # Detecting an address change before it ships
│   └── testing-strategy.md             # The test templates the skill emits
└── scripts/
    ├── check-module-rules.py           # AST & schema-aware linter (Python 3)
    ├── generate-module-docs.py         # Introspects HCL/schema to generate/update README tables
    └── detect-migrations.py            # AST diff analyzer for moved {} blocks and SemVer classification
```
