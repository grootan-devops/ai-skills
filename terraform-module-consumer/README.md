# Terraform Module Consumer

This Skill creates or updates infrastructure configuration by composing
existing, versioned Terraform modules. [SKILL.md](./SKILL.md) is the execution
policy; [PROMPT.md](./PROMPT.md) has copyable requests.

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
