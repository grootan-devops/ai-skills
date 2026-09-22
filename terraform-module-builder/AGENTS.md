# Terraform Module Builder

> **Entry point for agents that read `AGENTS.md` (Codex, Gemini CLI, and similar).**
> Claude Code reads `SKILL.md` via its YAML frontmatter. Both describe the same skill —
> **[`SKILL.md`](./SKILL.md) is the single source; read it now and follow it.**
> This file exists only so the skill is discoverable under either convention, and
> deliberately duplicates nothing beyond the map below.

## What this skill does

Automates the full lifecycle of production Terraform modules across **AWS**, **Azure**,
**GCP** and **Kubernetes**: resolves the latest provider version and introspects its schema,
generates decomposed HCL and a canonical 8-section `README.md`, scaffolds native and Terratest
suites, scaffolds `moved.tf` blocks for zero-destroy state migrations, and audits against the
security baselines.

## Source of truth

The engineering standards are **not** in this skill. They live in the `terraform-modules`
reference repository — its `README.md` (module contract, naming and tagging, security
baselines, documentation standard, release levels, test gate) and its `MIGRATION.md`
(upgrade behaviour). Resolve that repository per
[`references/reference-repo-link.md`](./references/reference-repo-link.md) and read it;
this skill holds only what the library cannot state for itself.

## Layout

```text
SKILL.md                                  the runbook — start here
references/  reference-repo-link          where the library is, and what to read in it
             provider-schema-guide        extracting and reading `providers schema -json`
             naming-standards             brand abstraction; naming bounds outside AWS
             security-capability-matrix   deriving a control status; Azure/GCP starters
             state-migration-guide        detecting an address change before it ships
             testing-strategy             the test templates the skill emits
scripts/     check-module-rules.py, detect-migrations.py, generate-module-docs.py
assets/      architecture-template.svg, architecture-template.png
```

## Commands

Three workflows, described in full in `SKILL.md` §1. Invoked bare, the skill prompts for one
of them rather than guessing:

```bash
terraform-module add    <provider> <resource_type> <module_name>   # end-to-end creation
terraform-module update <module_path>                              # upgrade & modernise
terraform-module audit  <module_path_or_repo>                      # read-only verification
```

The scripts behind them run standalone:

```bash
python3 scripts/check-module-rules.py <module_path> [--strict]   # rule + security audit
python3 scripts/detect-migrations.py  <module_path>              # moved.tf state safety
python3 scripts/generate-module-docs.py <module_path> [--check]  # canonical README
```

Requires Python 3.9+ and Terraform on `PATH`. No agent-specific tooling: everything is plain
Markdown and plain Python, so the skill behaves identically under Claude, Codex, and Gemini.
