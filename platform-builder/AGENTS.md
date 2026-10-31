# Platform Builder

This is an agent-discovery entry point. Read [SKILL.md](./SKILL.md) for
platform detection, library resolution, task routing, and safety rules.
Read only the library docs it names for the selected platform and task.
GitOps environment and app commands route to `gitops/<backend>/workflows/`; their shared
rules are in `core/references/`.
