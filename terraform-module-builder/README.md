# Terraform Module Builder

This Skill creates, updates, and audits Terraform modules against the
checked-out terraform-modules repository. [SKILL.md](./SKILL.md) is the
execution policy; AGENTS.md is a discovery pointer, and PROMPT.md contains
copyable requests.

## Workflows

| Command | Result |
| --- | --- |
| terraform-module add <provider> <resource_type> <module_name> | Create a module with an inspected provider schema, documentation, and useful tests. |
| terraform-module update <module_path> | Target the latest stable provider and make a reviewed, surgical module change. |
| terraform-module audit <module_path_or_repo> | Report findings without editing. |

The reference repository's README defines its module API, naming, security,
documentation, release, and test standards. Its MIGRATION.md defines the
consumer migration procedure. Read both at the selected ref, then inspect
actual modules and tests before treating examples as shipped behavior. The
current reference repository is AWS-focused; other providers require their
own schema and contract evidence.

## Local tools

- scripts/check-module-rules.py checks a textual subset of module rules.
- scripts/detect-migrations.py compares variables, outputs, and top-level
  resource declarations with a Git ref and reports candidate migrations.
  It cannot prove plan safety or detect every Terraform instance-key change.
- scripts/generate-module-docs.py renders or checks Requirements, Inputs, and
  Outputs tables; review the rest of a module README manually.

Use make verify from terraform-modules for its contract, format, validation,
and available native-test gate. Inspect a representative consumer plan before
claiming that a refactor avoids replacement or destruction. A moved block only
covers a verified resource-address mapping. No workflow applies infrastructure
or commits and pushes work without an explicit request.

For Skill maintainers, the migration detector's Git-baseline regressions live
in `scripts/tests/`. From the `ai-skills` repository root, run
`python3 -m unittest discover -s terraform-module-builder/scripts/tests -v`.
The separate `terraform-modules/tests/verify_modules.py` remains that library's
native `make verify` entry point so its contract gate works in a standalone clone.

## References

- [Reference repository resolution](./references/reference-repo-link.md)
- [Provider schema](./references/provider-schema-guide.md)
- [Naming](./references/naming-standards.md)
- [Security capabilities](./references/security-capability-matrix.md)
- [State migration](./references/state-migration-guide.md)
- [Testing](./references/testing-strategy.md)
