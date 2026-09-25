# Terraform Module Builder: Copyable Engineer Prompts

Replace placeholders before pasting. Use [SKILL.md](./SKILL.md) and the
selected terraform-modules checkout as the contract. These prompts authorize
local edits and validation only; infrastructure mutation needs an explicit request.

## `terraform-module add`

### Quick

```text
Run terraform-module add <provider> <resource_type> <module_name> in <repository>, using terraform-modules source <source-or-default>. Resolve the latest stable provider, inspect its schema and official resource documentation, compare existing modules and naming rules, then create the smallest complete module, docs, outputs, and meaningful tests. Run the module rules, docs check, and available native validation. Report its public API, provider pin, checks, and limitations. Do not apply, commit, or push.
```

### Full

```text
Act as a Terraform module engineer using terraform-module-builder. Add
<provider>/<resource_type> as <module_name> in <repository>.
Reference terraform-modules source/ref: <source-or-default>.

1. Resolve the reference repository and read its relevant README, sibling
   modules, naming conventions, tests, and security capability guidance.
   Identify any documented standard that differs from shipped code.
2. Identify the latest stable provider release from a trusted source. Inspect
   its official resource documentation and machine-readable provider schema
   using terraform init -backend=false and terraform providers schema -json.
   Map only supported arguments and nesting to inputs; mark sensitive values
   and outputs appropriately. Do not invent defaults or credentials.
3. Design the public API and expected behavior. Implement the smallest
   complete module with variables, resource blocks, outputs, examples/docs,
   and tests that exercise material behavior. Match repository conventions
   unless a documented standard explicitly supersedes them.
4. Run scripts/check-module-rules.py <module_path> --strict,
   scripts/generate-module-docs.py <module_path> --check, focused tests, and
   the reference repository's make verify when prerequisites are available.
   Report exact results, API decisions, provider version, and missing gates.
   Leave all changes local; do not run apply, commit, or push.
```

## `terraform-module update`

### Quick

```text
Run terraform-module update <module_path> against terraform-modules source <source-or-default>. Resolve the latest stable provider and inspect its upgrade notes and schema even for a narrow edit. Read the reference MIGRATION.md, compare the existing API and Git diff, use detect-migrations.py for candidate resource-address changes, and inspect a representative consumer plan before any change that might replace or destroy resources. Apply a surgical local edit, preserve intentional behavior, document risks and moved blocks only for verified mappings, and run rules/docs/tests/make verify where available. Do not apply, commit, or push.
```

### Full

```text
Act as a Terraform module engineer using terraform-module-builder. Update
<module_path> in <repository> for <requested-change>.
Reference terraform-modules source/ref: <source-or-default>.

1. Inspect the module's current provider constraints, public variables and
   outputs, resource addresses, defaults, examples, tests, MIGRATION.md,
   repository changes, and representative consumers. Keep a before/after API
   inventory; identify intentional customizations.
2. Resolve the latest stable provider release. Read official changelog and
   resource docs and inspect the selected provider schema. Compare this with
   the current constraint and identify compatibility or SemVer implications.
   Read the reference library's migration notes at the selected ref.
3. Run scripts/detect-migrations.py <module_path> --compare-ref HEAD. Treat
   results as candidates only. For every changed address, key, default,
   provider behavior, or lifecycle setting that might replace or destroy a
   resource, review a realistic consumer state and plan or representative
   fixture before making the change. Add moved blocks only for verified
   one-to-one address mappings. Show any unresolved destructive risk and ask
   before changing ambiguous consumer behavior.
4. Make only the required in-place edits. Preserve public API, custom
   defaults, tags, resource settings, and formatting unless a verified
   migration or defect requires a change. Document consumer migration steps
   and any replacement or destruction risk; never claim zero risk from a
   textual detector or a green module test alone.
5. Run strict module rules, migration detection, docs check, focused tests,
   and make verify when its dependencies exist. Review the final diff and
   report exact commands, results, provider version, and plan limitations.
   Do not run apply, destroy, state mutation, commit, push, or release unless
   explicitly requested.
```

## `terraform-module audit`

### Quick

```text
Run terraform-module audit <module_path_or_repo> against terraform-modules source <source-or-default>. Inspect checked-out code, provider constraints, docs, tests, and security controls; run read-only rules, migration, docs, and native checks where applicable. Report reproducible findings separately from manual judgments, with file evidence and unavailable-plan limits. Do not edit or apply.
```

### Full

```text
Act as a Terraform module auditor using terraform-module-builder. Audit
<module_path_or_repo> in <repository>, using reference source <source-or-default>.
Read the selected repository README and relevant module contract, provider
constraints, source HCL, variables, outputs, tests, docs, and migration notes.
Run scripts/check-module-rules.py --strict, detect-migrations.py where a
comparison is meaningful, generate-module-docs.py --check, and available
read-only native validation. Compare security controls with actual provider
schema support. Check latest-stable provider status only when verified from a
trusted source. Report findings with file locations, commands, severity,
library/provider provenance, and what a consumer plan would still need to
prove. Keep the repository and infrastructure unchanged.
```
