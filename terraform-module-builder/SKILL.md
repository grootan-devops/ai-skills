---
name: terraform-module-builder
description: >-
  Create, update, and audit Terraform modules against a module library's own
  standards: resource attributes taken from the provider's machine-readable
  schema, secure defaults, provider upgrades, resource-address migrations, and
  the library's validation gate. Use for any new module, module API or README
  change, provider bump, module audit, or change spread across many modules,
  for any provider, even when the request only says "add a bucket module" or
  "check this module". Works with terraform-skill and HashiCorp's
  terraform-style-guide and terraform-test for general Terraform craft.
---

# Terraform Module Builder

## Check for updates first

At the start of every invocation, run `python3 scripts/update_check.py` from this skill's folder.

- **An update is available (exit 1):** tell the user the installed and latest release and the
  command it prints, and ask whether to update now. Run the update only on a yes, then reload
  this skill before you go on.
- **The installed release is unknown (exit 2, a copied install):** tell the user the latest
  release and the reinstall command it prints, and ask whether to reinstall now. Run it only
  on a yes, then reload this skill before you go on.
- **They decline, or it could not check (exit 2):** carry on with the installed release and
  name it in your report.

## Route the request

| Request | Workflow |
| --- | --- |
| `terraform-module add <provider> <resource_type> <module_name>` | Design and create a module. |
| `terraform-module update <module_path>` | Upgrade and edit an existing module. |
| `terraform-module audit <module_path_or_repo>` | Inspect and report without writing. |

Ask only for a missing target or an ambiguous action. An update is a surgical edit:
preserve the existing public API and intentional customizations unless the requested
change or a verified defect requires otherwise.

## Resolve the authority

terraform-modules holds the modules and every standard they are built to. Resolve it, stopping
at the first that exists: a path or repository/ref named in the request; the current
repository when it is terraform-modules or a fork of it; `$TERRAFORM_MODULES_REPO`; a
`terraform-modules/` checkout beside the directory this skill is installed in; otherwise the
default, `https://github.com/grootan-devops/terraform-modules` at `main`. Adding or updating a
module edits a local checkout — the repository the request names, otherwise the resolved one
when it is local — so ask before cloning one. Never guess.

Read the repository's agent instruction files first (`CLAUDE.md`, `AGENTS.md` at its root),
then its README documentation index, following only the rows the request needs — public API,
names and tags, security controls, module README, releases and versioning, tests — all at the
selected ref; for an upgrade, read `MIGRATION.md` between the versions. Order of authority:
the repository's agent files and docs, then this skill, then the companion skills. A library
decides its own conventions; this skill brings the method, and the companions bring general
Terraform craft.

Shipped behaviour and executable checks establish what works today; the README's "current
state" callouts record where modules diverge from the standard, and the standard wins. Report
a disagreement instead of silently copying either version. Do not transplant AWS-specific
rules into another provider without schema and repository evidence.

Lift every project, company or customer name in a request into `var.application`,
`var.environment` and `var.name`, and say in one line which variable now carries it; no brand
label goes into module code, examples or diagrams.

## Companion skills

General Terraform craft comes from two maintained skills, so this one stays about the module
library and the provider schema:

| Need | Skill |
| --- | --- |
| Module design, review, security and testing strategy | `terraform-skill` (Anton Babenko) |
| HCL style and layout | `terraform-style-guide` (HashiCorp) |
| Native `terraform test` syntax, runs, assertions and mocks | `terraform-test` (HashiCorp) |

The HashiCorp plugin ships other skills (provider development, stacks, search-import, Azure
Verified Modules, refactor-module); use one only when the request is about it. Where a
companion and the repository disagree — for example `moved` blocks or wrapping registry
modules — the repository's docs win.

Check the companions are available before relying on them. In Claude Code they are plugins:
look for `terraform-skill@antonbabenko` and `terraform@hashicorp` under `enabledPlugins` in the
project's `.claude/settings.json`. If either is missing, suggest:

    claude plugin marketplace add antonbabenko/agent-plugins
    claude plugin install terraform-skill@antonbabenko --scope project
    claude plugin marketplace add hashicorp/agent-skills
    claude plugin install terraform@hashicorp --scope project

For other agents, suggest `npx skills add https://github.com/antonbabenko/terraform-skill` and
`npx skills add https://github.com/hashicorp/agent-skills --skill terraform-style-guide --skill terraform-test`.
Install only on a yes; without them, carry on and say which guidance was missing.

## Resource attributes

These are this skill's own rules, for every provider. The provider schema, not memory or a
blog post, decides what a module may set:

- Use the latest stable release of a trusted provider (official or partner tier, or a
  well-maintained community provider the repository already uses).
- Take `terraform providers schema -json` at that release as the source of truth for every
  argument, block and attribute, and the Registry docs for behaviour the schema cannot show.
  Map schema types to variable types through the repository's schema-mapping doc.
- A required argument becomes a required input, or an opinionated secure default when one
  exists for every caller.
- A computed-only attribute is never an input; export it when a neighbour or monitoring
  needs it.
- A sensitive attribute stays `sensitive` through the variables and outputs that carry it.
- A write-only argument (`*_wo`) needs the Terraform release that supports it and an
  `ephemeral` input, so the value never reaches state.
- Avoid deprecated arguments, blocks and resources; use the replacement the schema names.
- A nested block becomes a typed object with `optional()` defaults; a repeatable one becomes
  a map keyed by a stable name, so adding an entry never re-keys the others.
- Every security-relevant attribute (encryption, public access, network exposure, logging,
  deletion protection, TLS policy, IAM trust) is either set securely by default or exposed
  with a validation, and its status is recorded with the repository's capability statuses.
- Never invent an argument the schema does not have, and never invent a credential default.
- Outputs export the identifiers neighbours and monitoring need (ID, ARN, name, endpoint,
  security group, role, key) and never echo an input back.

## Execute

For add, apply the resource-attribute rules, design inputs, outputs and the supported
security controls, then implement the smallest complete module, documentation and useful
tests. Compose the repository's own building blocks by relative source rather than copying
them, and do not wrap third-party registry modules unless the repository allows it. Drive
`count` and `for_each` only from values known at plan time — booleans, literal strings, map
keys and list lengths — never from whether an ID or ARN from another resource is null.

For update, resolve the latest stable provider release even when the requested edit is
otherwise narrow. Compare its schema and changelog with the current constraint before
changing code; report compatibility and SemVer impact. Run the migration detector to find
candidate API and resource-address changes
(`scripts/detect-migrations.py <module_path> --compare-ref HEAD`), and scaffold a verified
move with `--scaffold-move <FROM> <TO>`. It is a textual aid, not a proof of state safety.
The repository's versioning policy decides first whether `moved` blocks and deprecations are
wanted at all: a library with no consumers carries neither. Where they are wanted, inspect
each affected consumer state/plan or a representative fixture, write moved blocks only for
verified address mappings, and keep an explicit record of any remaining replacement or
destruction risk. Changed defaults and `count`/`for_each` keys also need plan review even
when resource labels stay the same. A moved block only maps an address; it does not prevent
replacement caused by changed arguments.

An add or update stops after local validation and plan review by default. If the user
separately requests apply, identify the exact account, workspace, and environment;
create a saved plan for that target; inspect its resource actions and replacement paths;
and obtain confirmation of that exact plan before applying the saved file. Re-plan and
review again if configuration or state changes. Do not run destroy or direct state
commands as part of an add or update; those require their own explicit request and
impact review.

For audit, run read-only checks and report evidence and limits. Distinguish executable
checker findings from manual judgments. Do not assert that a provider is current or a
plan is safe without checking the selected version and relevant state.

## Changes across many modules

A change that touches many modules (a new standard input, a provider floor, a shared
feature) goes in this order, so every module ends up with the same shape:

1. Build one reference module end to end, with its tests and README.
2. Add a contract test in the repository that pins the shared shape, so a module that
   drifts fails the gate.
3. Fan the rest out to workers, each owning a disjoint set of files and briefed with the
   reference module and the contract. Shared files (docs index, changelog, examples) stay
   with you.
4. Review every worker's diff and re-run the gate before reporting.

## Validate and finish

Work through this in order, and report what ran and what remains unverified:

1. **Baseline:** run the repository gate on the unchanged tree before editing, so a failure
   afterwards is known to be yours.
2. **Skill scripts**, for each targeted module:

       python3 scripts/check-module-rules.py <module_path> --strict
       python3 scripts/detect-migrations.py <module_path> --compare-ref HEAD
       python3 scripts/generate-module-docs.py <module_path> --check

3. **Repository gate:** run it (for example `make verify`) when its prerequisites are
   available.
4. **CI's own tools at CI's versions:** the formatter, linter and IaC scanner the pipeline
   runs; Markdown lint over every file CI lints, not only the files you touched; and the
   secret scan over the whole commit range a pull request will carry, since a fix on top
   does not clear a finding in an earlier commit.
5. **Generated docs:** regenerate them and run their check.
6. **Fixtures:** keep credential-shaped literals out of tests, examples and docs; derive a
   fixture value (for example a hash of a fixed string) so secret scanners stay quiet.
7. **Review:** the before/after API, migration notes, tests, and a Terraform plan where
   state safety matters.

Local edits do not authorize a commit, push, release, or infrastructure mutation.
