---
name: terraform-module-builder
description: >-
  Create, update, and audit Terraform modules against the checked-out
  terraform-modules repository. Use for new modules, module APIs and
  documentation, provider upgrades, resource-address and state migrations,
  and module validation.
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

terraform-modules holds the modules and every standard they are built to. Locate the checkout
to work in, stopping at the first that exists: a path named in the request; the current
repository when it is terraform-modules or a fork of it; `$TERRAFORM_MODULES_REPO`; a
`terraform-modules/` checkout beside the directory this skill is installed in. Otherwise ask
for the path, offering to clone the default, `https://github.com/grootan-devops/terraform-modules`;
an audit may read that repository at `main` instead. Never guess.

Read its README documentation index and follow only the rows the request needs — public API,
names and tags, security controls, module README, releases and state changes, tests — all at
the selected ref; for an upgrade, read `MIGRATION.md` between the versions. Shipped
behaviour and executable checks establish what works today; the README's "current state"
callouts record where modules diverge from the standard, and the standard wins. Report a
disagreement instead of silently copying either version. Do not transplant AWS-specific rules
into another provider without schema and repository evidence.

Lift every project, company or customer name in a request into `var.application`,
`var.environment` and `var.name`, and say in one line which variable now carries it; no brand
label goes into module code, examples or diagrams.

## Execute

For add, select the latest stable trusted provider release, inspect its machine-readable
schema and Registry documentation, design inputs/outputs and supported security controls,
then implement the smallest complete module, documentation, and useful tests. Never invent
a provider argument or a credential default.

For update, resolve the latest stable provider release even when the requested edit is
otherwise narrow. Compare its schema and changelog with the current constraint before
changing code; report compatibility and SemVer impact. Run the migration detector to
find candidate API and resource-address changes
(`scripts/detect-migrations.py <module_path> --compare-ref HEAD`), and scaffold a verified
move with `--scaffold-move <FROM> <TO>`. It is a textual aid, not a proof of state safety. Inspect each affected consumer state/plan or a representative fixture,
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
