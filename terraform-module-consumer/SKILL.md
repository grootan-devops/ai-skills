---
name: terraform-module-consumer
description: >-
  Create or update Terraform and Terragrunt infrastructure configurations by
  composing existing Terraform modules. Use for consumer stacks, module inputs
  and outputs, and environment wiring; not for authoring modules, adding direct
  managed resources, or applying infrastructure.
---

# Terraform Module Consumer

Build infrastructure configurations from existing modules. Work in the consumer
repository, not in the module library. Discover the target repository, requested
environments, module source, and existing state layout before changing files. Ask
for a missing choice only when the repository and request cannot establish it.

## Read the contracts

For every selected module, resolve its source and, for a remote source, an exact
revision, and verify both exist. Read the module's README, inputs, outputs, provider
and Terraform requirements, and migration notes at that revision; inspect the
implementation where documentation is incomplete. Use the target project's approved
module catalog when it has one; do not assume a cloud provider or the modules of any
example repository.

The composition rules — pinning, layout, one stack and state per environment,
backends and state keys, providers, secrets, lock files and validation — live in the
module library. Locate it, stopping at the first that exists: a path or repository/ref
named in the request; `$TERRAFORM_MODULES_REPO`; the repository and ref of the stack's
pinned module source; a `terraform-modules/` checkout beside this skill's directory;
otherwise the default, `https://github.com/grootan-devops/terraform-modules` at `main`.
Read its README documentation index and follow the row for consuming modules at that
ref, or the equivalent guide of the project's own catalog.

## Compose the stack

Follow the guide's layout for a new project; keep an existing project's layout,
naming and file organization unless the user asks for a migration. Add module calls,
never managed `resource` blocks, and leave unrelated existing resources alone. When
no module provides a needed capability or output, report the exact gap for
module-builder work and stop that part. Flag a state that would become too broad
instead of silently splitting an existing one. Preserve existing backends, state
keys and module addresses.

## Safety

Never put literal credentials in tracked files or echo secret values into tool output
or reports. Run a plan only when the user explicitly requests one for a named
environment, and review its create, update, replace and delete actions. This skill
never runs apply, destroy, import, or direct state commands. Do not commit or push
unless requested.

## Validate and report

Run the validation the guide describes for the changed roots and environments, and
report any gate that could not run. Finish with the selected module sources and
revisions, changed environments, validation results, state implications, and
unresolved module gaps.
