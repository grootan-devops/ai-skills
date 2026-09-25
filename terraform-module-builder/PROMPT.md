# Terraform Module Builder: Standard Engineer Prompts

This guide provides battle-tested, copy-pasteable prompts for engineers using the `terraform-module-builder` skill. Use these prompts to create, modernize, refactor, and audit enterprise Terraform modules across AWS, Azure, GCP, and Kubernetes while enforcing strict type contracts, security baselines, zero-destroy state migration, and canonical documentation.

---

## Table of Commands

| Command | Purpose | When to Use |
| :--- | :--- | :--- |
| **`terraform-module add`** | End-to-end module creation from scratch | Creating a new brand-neutral module with latest provider schema & tests. |
| **`terraform-module update`** | End-to-end modernization & refactor | Bumping provider versions, modernizing types, and generating `moved.tf`. |
| **`terraform-module audit`** | Read-only compliance & security check | Checking rule compliance, dead variables, tag clobber, and doc drift. |

---

## Core Engineering Directives (Embedded in All Prompts)

1. **Provider Schema & Registry Docs-First**:
   - Never generate modules from memory or prose alone. Inspect the actual provider schema:
     `terraform providers schema -json`
   - Consume only **`official`**, **`partner`**, or **`partner-premier`** provider tiers.
   - Always target the **latest stable provider release**. Set open-ended minimum constraints (`version = ">= <latest>"`).
2. **Project & Brand Neutrality**:
   - Reusable modules must be 100% brand-neutral: no company names, project names, or hardcoded identifiers in resource names, locals, defaults, tags, or docs.
   - All naming flows through standard inputs: `var.application`, `var.environment`, and `var.name`.
3. **Decomposed HCL Architecture**:
   - Decompose into logical files: `<resource>.tf`, `security.tf`, `variables.tf`, `outputs.tf`, `locals.tf`, `versions.tf`.
   - `data.tf` is created **only** when its queries are actively referenced by expressions.
4. **Strong Typing & Module Contract**:
   - Prohibit `lookup()` abuse and weak `list(any)`. Use strongly-typed objects with `optional()`.
   - Zero default passwords or secrets. Curate `outputs.tf` (never export whole resource blocks).
   - Invert tag merge precedence to prevent clobbering governance tags: `merge(var.tags, local.governance_tags)`.
5. **Zero-Destroy State Migration (`update`)**:
   - Refactoring that renames resources, splits blocks, or transitions from `count` to `for_each` MUST declare `moved {}` blocks in `moved.tf`.
   - Run `python3 scripts/detect-migrations.py <module_path>` to ensure state preservation.
6. **Canonical Documentation & Tests**:
   - Auto-generate Requirements, Inputs, and Outputs tables using `python3 scripts/generate-module-docs.py <module_path>`.
   - Author native tests in `tests/unit.tftest.hcl` with `mock_provider`.
   - Validate with `python3 scripts/check-module-rules.py <module_path> --strict`.

---

## 1. `terraform-module add`

Use when provisioning a new Terraform module from scratch.

### Quick Copy-Paste Prompt (Terse)

```text
Run `terraform-module add <provider> <resource_type> <module_name>`. Resolve the latest stable provider from registry, introspect schema via `terraform providers schema -json`, classify security controls (CMEK, TLS 1.2+, deletion protection), scaffold decomposed HCL (security.tf, variables.tf, outputs.tf, versions.tf with >= latest), generate canonical 8-section README via scripts/generate-module-docs.py, scaffold tests/unit.tftest.hcl with mock_provider, and validate with python3 scripts/check-module-rules.py <module_dir> --strict.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Senior Infrastructure & Terraform Engineer using the `terraform-module-builder` skill.
Execute `terraform-module add` for:
- **Provider**: `<provider>` (e.g. `aws`, `azurerm`, `google`, `kubernetes`)
- **Resource Type**: `<resource_type>` (e.g. `aws_s3_bucket`, `aws_sqs_queue`, `azurerm_storage_account`)
- **Module Name**: `<module_name>` (e.g. `s3`, `sqs`, `storage_account`)

Execute according to the strict 6-phase engineering pipeline:

### Phase 1: Provider Introspection & Capability Inventory
1. Query Terraform Registry to resolve the latest stable release of `<provider>`.
2. Generate provider schema:
   `terraform -chdir=<module_dir> init -backend=false`
   `terraform -chdir=<module_dir> providers schema -json`
3. Cross-reference schema attributes with official Registry documentation:
   - Tier 1: Customer-managed keys (CMEK/KMS), TLS 1.2+, deletion protection, audit logging.
   - Tier 2: Production best practices (HA defaults, strongly-typed configuration).
   - Tier 3: Omit provider-inherited defaults to prevent obsolescence.

### Phase 2: Public API & Security Design
1. **Brand Neutrality**: Ensure zero company or project names exist. Resource naming flows through `var.application`, `var.environment`, `var.name`.
2. **Variable Typing**:
   - Use strongly-typed objects with `optional()`.
   - Ban `lookup()` on untyped maps.
   - Zero hardcoded secrets, tokens, or default passwords.
3. **Outputs Contract**:
   - Curate outputs (`id`, `arn`, endpoints).
   - Do not export entire resource objects (`output "resource" { value = aws_... }` is prohibited).

### Phase 3: Decomposed HCL Scaffolding
Create the module with the decomposed layout:
- `<resource_name>.tf`: Primary resource declarations and child blocks.
- `security.tf`: Encryption, KMS key associations, IAM access policies, TLS requirements.
- `variables.tf`: Strongly-typed inputs with descriptions.
- `outputs.tf`: Curated resource identifiers and connection attributes.
- `locals.tf`: Tag merges (`merge(var.tags, local.governance_tags)`) and naming interpolation.
- `versions.tf`: Pinned latest provider constraint (`version = ">= <latest>"`) and derived core version.
- `data.tf`: Platform context queries **only** if referenced by expressions.

### Phase 4: Canonical Documentation
1. Run documentation generator:
   `python3 scripts/generate-module-docs.py <module_dir>`
2. Ensure the resulting `README.md` complies with the canonical 8-section layout:
   - Title & Status Badge
   - Architecture & Resource Mapping
   - Security Baselines & CMEK Details
   - Usage Examples (Minimal & Production)
   - Requirements
   - Providers
   - Modules & Resources
   - Inputs & Outputs

### Phase 5: Test Fixtures
Scaffold unit tests in `tests/unit.tftest.hcl`:
- Use `mock_provider` for plan assertions without calling real cloud APIs.
- Write assertions for default security settings (encryption enabled, TLS enforced).
- Include negative tests asserting input validations.

### Phase 6: Validation & Verification
Execute strict validation:
`python3 scripts/check-module-rules.py <module_dir> --strict`
Ensure all P1 and P2 findings are cleared.
```

---

## 2. `terraform-module update`

Use when upgrading provider versions, modernizing variable typing, refactoring resource layout, or introducing state migration `moved {}` blocks.

### Quick Copy-Paste Prompt (Terse)

```text
Run `terraform-module update <module_path>`. Upgrade provider constraints to latest stable release in versions.tf. Run python3 scripts/detect-migrations.py <module_path> and generate moved.tf blocks to guarantee ZERO unexpected resource destroys. Modernize variable typing (ban lookup(), use optional() objects), preserve existing user contracts, update canonical README.md via scripts/generate-module-docs.py, update tests, and validate with python3 scripts/check-module-rules.py <module_path> --strict.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Senior Infrastructure & Terraform Engineer using the `terraform-module-builder` skill.
Execute `terraform-module update` for the module at: `<module_path>`.

Execute the following modernization and state-preservation steps:

### 1. Version Bump & Changelog Audit
1. Query Terraform Registry for the latest stable version of the module's provider(s).
2. Update `versions.tf` to bump the provider constraint (`version = ">= <new_latest>"`).
3. Audit provider changelogs for breaking attribute changes or deprecations.

### 2. Zero-Destroy State Migration Analysis
1. Detect renamed resources, resource splits, or `count` to `for_each` migrations:
   `python3 scripts/detect-migrations.py <module_path>`
2. Scaffold `moved {}` blocks in `moved.tf` for every modified resource address:
   ```hcl
   moved {
     from = aws_s3_bucket.this
     to   = aws_s3_bucket.main
   }
   ```
3. Guarantee that applying this update will result in state moves rather than destroy/recreate cycles.

### 3. Type Modernization & Refactor
1. Replace weak `list(any)` or `map(string)` with strongly-typed objects using `optional()`.
2. Eliminate any legacy `lookup()` expressions.
3. Verify tag merge order: ensure `merge(var.tags, local.governance_tags)` prevents user tags from clobbering mandatory governance tags.
4. Prune unused variables or dead data sources.

### 4. Documentation & Test Refresh
1. Refresh canonical tables:
   `python3 scripts/generate-module-docs.py <module_path>`
2. Document breaking changes or upgrade instructions in `MIGRATION.md`.
3. Update test assertions in `tests/unit.tftest.hcl` to match modernized inputs.

### 5. Validation & SemVer Classification
1. Run strict rules check:
   `python3 scripts/check-module-rules.py <module_path> --strict`
2. Emit a SemVer classification report:
   - **MAJOR**: Breaking input/output changes or removed resources.
   - **MINOR**: Backward-compatible new features or new optional inputs.
   - **PATCH**: Provider bump without interface changes or bug fixes.
```

---

## 3. `terraform-module audit`

Use when auditing a module or repository of modules for standards compliance, provider currency, tag governance, security baselines, and documentation drift without modifying code.

### Quick Copy-Paste Prompt (Terse)

```text
Run `terraform-module audit <module_path_or_repo>`. Perform a read-only compliance and security audit. Check for untrusted provider tiers, lagging provider versions, dead variables, tag clobber risks, unconsumed data sources, and documentation drift (via scripts/generate-module-docs.py --check). Run python3 scripts/check-module-rules.py <module_path_or_repo> and output categorized P0/P1/P2 findings.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as an Enterprise Cloud Infrastructure Auditor using the `terraform-module-builder` skill.
Audit the Terraform module or repository at: `<module_path_or_repo>`.

Perform a comprehensive, read-only audit covering the following areas:

### 1. Automated Rule Checks
Execute the rule engine:
`python3 scripts/check-module-rules.py <module_path_or_repo>`

### 2. Manual & Static Inspection Checklist
Verify the following non-negotiable rules:
- **Provider Tiers**: Are all providers from `official`, `partner`, or `partner-premier` tiers? (Flag community/untrusted providers as P1).
- **Version Currency**: Are provider constraints lagging or locking out modern releases?
- **Child Module Governance**: Do registry module invocations specify explicit `version` constraints?
- **Tag Governance**: Does the module protect mandatory governance tags against user overrides (`merge(var.tags, local.governance_tags)`)?
- **Dead Code**: Are there unwired variables or unreferenced data sources in `data.tf`?
- **Security Baselines**: Are CMEK/KMS, TLS 1.2+, and access logging supported and enabled by default?
- **Credential Safety**: Are there hardcoded passwords, tokens, or fake default credentials?
- **Documentation Drift**:
  Run `python3 scripts/generate-module-docs.py <module_path_or_repo> --check` to verify that `README.md` tables match actual HCL variables and outputs.

### 3. Reporting
Format all findings into a prioritized report:
- **P0 (Severe / Critical)**: Security vulnerabilities, cleartext credentials, breaking state destruction without `moved {}`.
- **P1 (High / Blocker)**: Untrusted providers, missing mandatory security controls, broken validation.
- **P2 (Advisory / Best Practice)**: Outdated provider versions, `lookup()` usage, unpinned child modules, documentation drift.

Do NOT modify any files during this audit.
```
