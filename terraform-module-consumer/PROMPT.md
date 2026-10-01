# Terraform Module Consumer: Copyable Prompts

Replace placeholders before use. These requests authorize local configuration
edits and validation only. A plan must be requested for a named environment;
this Skill never applies infrastructure.

## New Terragrunt project

```text
Use terraform-module-consumer to prepare <project> for <environment-list> with <module-source-and-exact-version> to provide <requested-infrastructure>. Create a shared root.hcl, one resources/ Terraform composition stack, and an environment terragrunt.hcl for each environment. Inspect the module contracts before wiring inputs and outputs. Use the backend and secret delivery method I specify, or identify the missing details before creating those parts. Create no direct managed resources, preserve distinct state keys, and run available formatting and static validation. Do not plan, apply, commit, or push.
```

## Extend an existing project

```text
Use terraform-module-consumer to add <requested-infrastructure> to <project>/<environment> using <module-source-and-exact-version>. Inspect the current layout, backend, state key, module contract, and existing provider configuration. Make the smallest consumer-side change, preserve state addresses and project conventions, and create no direct managed resources. Run available formatting and static validation. Report any module capability or output gap. Do not plan, apply, commit, or push.
```

To request a plan separately, name the project and environment and ask to
review its proposed actions. An apply is outside this Skill.
