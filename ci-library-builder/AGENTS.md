# CI Library Builder

> **Entry point for agents that read `AGENTS.md` (Codex, Gemini CLI, and similar).**
> Claude Code reads `SKILL.md` via its YAML frontmatter. Both describe the same skill —
> **[`SKILL.md`](./SKILL.md) is the single source; read it now and follow it.**
> This file exists only so the skill is discoverable under either convention, and
> deliberately duplicates nothing beyond the map below.

## What this skill does

Develops the shared CI **libraries** themselves — adding, extending, porting and auditing jobs in
a GitLab CI template library and its GitHub Actions counterpart, keeping the two in step. For
scaffolding a *consuming* repository, use `platform-builder` instead.

## Layout

```text
SKILL.md                            the runbook — start here
references/  gitlab-job-anatomy     the eight decisions, in order          (required)
             github-job-anatomy     what changes in Actions, plus its traps (required)
             construct-mapping      GitLab keyword → GitHub equivalent      (required for a port)
             known-pitfalls         14 silent reusable-workflow failures    (required)
             library-conventions    GitHub job structure, containers, variables
             docs-contract          README / CHANGELOG / MIGRATION duties
scripts/     verify-gitlab-library.py   structural gates for the GitLab library
             verify-github-library.py   structural gates for the GitHub library
```

## Commands

Every command takes the path to the library it touches; nothing is assumed from memory.

```bash
ci-library verify   --gitlab <path> --github <path>
ci-library add job  "<description>" --gitlab <path> [--github <path>]
ci-library extend   <job-name>      --gitlab <path> [--github <path>]
ci-library port     <job-name>      --gitlab <path> --github <path>
ci-library audit    --gitlab <path> --github <path>          # read-only
```

The verifiers run standalone and are the first thing any command does:

```bash
python3 scripts/verify-gitlab-library.py <library_dir> [--strict] [--json]
python3 scripts/verify-github-library.py <library_dir> [--strict] [--json]
```

Exit 1 on any P0 (or on any finding with `--strict`). **A P0 in either library is a stop** — report
it and ask before building on top of it. Neither script replaces `yamllint`, `actionlint` or
`shellcheck`; run those too.

Requires Python 3.9+ and PyYAML. No agent-specific tooling: everything is plain Markdown and plain
Python, so the skill behaves identically under Claude, Codex, and Gemini.
