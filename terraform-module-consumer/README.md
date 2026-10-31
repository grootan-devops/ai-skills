# Terraform Module Consumer

This Skill creates or updates infrastructure configuration by composing
existing, versioned Terraform modules. [SKILL.md](./SKILL.md) is the execution
policy.

## Requests

| Ask for | Result | Asks for, when missing |
| --- | --- | --- |
| A new Terragrunt project | A shared `root.hcl`, one `resources/` composition stack and a `terragrunt.hcl` per environment, wired to existing modules. | the environments and the infrastructure they need; backend and secret delivery |
| Extend an existing project | The smallest consumer-side change, keeping state addresses and project conventions. | the infrastructure to add and its environment |

A request works on the project in the current directory when it is one (`root.hcl`,
`terragrunt.hcl` or `*.tf` files that call modules), and otherwise asks for its path. Module
sources default to the release the project pins, or the latest release, at an exact version.
Plans run only on a separate request naming the project and environment.

The composition rules live in the module library. The Skill reads them at the
selected ref by following its README documentation index row for consuming modules.

New projects default to a shared `root.hcl`, one `resources/` Terraform
composition root, and sibling environment directories containing
`terragrunt.hcl`. Existing native Terraform projects keep their format unless
a migration is requested. Module implementations normally remain in their
separate, versioned source repositories.

The Skill requires module calls for managed resources, protects existing state
keys, keeps secrets out of tracked configuration, and validates local changes.
Plans run only on request for a named environment. It never applies
infrastructure. If a required module capability is missing, the Skill reports
the gap for module-builder work.
