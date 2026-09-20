# Platform Builder

> **Entry point for agents that read `AGENTS.md` (Codex, Gemini CLI, and similar).**
> Claude Code reads `SKILL.md` via its YAML frontmatter. Both describe the same skill —
> **[`SKILL.md`](./SKILL.md) is the single source; read it now and follow it.**
> This file exists only so the skill is discoverable under either convention, and
> deliberately duplicates nothing beyond the map below.

## What this skill does

Generates, standardises, and audits CI pipelines, packaging-only Dockerfiles, and Helm charts
across **GitLab CI** and **GitHub Actions**. It detects which platform a repository targets and
loads only that platform's rules.

## Layout

```
SKILL.md                                  the runbook — start here
core/
  libraries.json                          default library sources
  references/  security-core, language-stacks-core, helm-chart-standard*,
               migration-standard*, file-mounts-standard*, ignore-files-standard*
  scripts/     platform.py, libraries.py, checks_common.py, audit.py
platforms/<gitlab|github>/
  references/  security-addendum, workflow-matrix (+ stack-snippets on GitLab)
  workflow-map.json, ci_checks.py
assets/        nginx-default.conf
aliases/       thin per-platform entry points
```

## Commands

```bash
python3 core/scripts/platform.py [repo]                  # which platform is this?
python3 core/scripts/libraries.py [repo] [--json]        # resolve library sources
python3 core/scripts/audit.py [repo] [--strict] [--json] # full compliance audit
```

Requires Python 3.9+ and PyYAML. No agent-specific tooling: everything is plain Markdown and
plain Python, so the skill behaves identically under Claude, Codex, and Gemini.
