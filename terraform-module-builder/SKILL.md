---
name: terraform-module-builder
description: >-
  Create, update, and audit Terraform modules against the checked-out
  terraform-modules repository. Use for module APIs, provider upgrades,
  migration safety, and module validation. Apply only after an explicit
  request, review of the exact saved plan, and confirmation of its target.
---

# Terraform Module Builder

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

Resolve terraform-modules using [reference-repo-link.md](./references/reference-repo-link.md)
before choosing a contract. Read its README sections relevant to the request, its
MIGRATION.md for upgrades, and the actual module code, tests, and provider schema at
the selected ref. Shipped behavior and executable checks establish what works today;
explicit standard-versus-current-state notes in the library README establish intended
future conventions. Report a disagreement instead of silently copying either version.
Do not transplant AWS-specific rules into Azure, GCP, or Kubernetes modules without
schema and repository evidence.

Load only the relevant detail:

- [provider-schema-guide.md](./references/provider-schema-guide.md) for a new resource
  or provider upgrade.
- [naming-standards.md](./references/naming-standards.md) for public names and tags.
- [security-capability-matrix.md](./references/security-capability-matrix.md) when a
  security control's provider support is uncertain.
- [state-migration-guide.md](./references/state-migration-guide.md) for resource
  address, key, default, or provider changes.
- [testing-strategy.md](./references/testing-strategy.md) when adding or changing tests.

## Execute

For add, select the latest stable trusted provider release, inspect its machine-readable
schema and Registry documentation, design inputs/outputs and supported security controls,
then implement the smallest complete module, documentation, and useful tests. Never invent
a provider argument or a credential default.

For update, resolve the latest stable provider release even when the requested edit is
otherwise narrow. Compare its schema and changelog with the current constraint before
changing code; report compatibility and SemVer impact. Run the migration detector to
find candidate API and resource-address changes. It is a textual aid, not a proof of
state safety. Inspect each affected consumer state/plan or a representative fixture,
write moved blocks only for verified address mappings, and keep an explicit record of
any remaining replacement or destruction risk. Changed defaults and `count`/`for_each`
keys also need plan review even when resource labels stay the same. A moved block only
maps an address; it does not prevent replacement caused by changed arguments.

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

## Validate and finish

Use the scripts in this Skill for the targeted module:

    python3 scripts/check-module-rules.py <module_path> --strict
    python3 scripts/detect-migrations.py <module_path> --compare-ref HEAD
    python3 scripts/generate-module-docs.py <module_path> --check

Run the reference repository's make verify gate when its prerequisites are available.
Review the before/after API, migration notes, tests, and a Terraform plan where state
safety matters. Report exactly what ran and what remains unverified. Local edits do not
authorize a commit, push, release, or infrastructure mutation.
