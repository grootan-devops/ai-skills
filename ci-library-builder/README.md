# ci-library-builder

Develops the shared CI **template libraries** — not the pipelines that consume them.

| | GitLab | GitHub |
| --- | --- | --- |
| Library | `gitlab-ci-library` | `github-ci-library` |
| Unit of reuse | hidden `.Job:` template + concrete `Job:` | reusable `workflow_call` workflow + job |
| Verifier | `scripts/verify-gitlab-library.py` | `scripts/verify-github-library.py` |

Use `platform-builder` to onboard a *consuming* repository. Use this skill to change the library
that repository includes.

## Why it exists

A job added to a shared library is copied into every pipeline that includes it, and lives for
years. The cost of getting the stage, the image, the `needs:` edge or the hidden/concrete split
wrong is paid by every consumer, quietly. This skill encodes the decisions that have already been
argued out, so the next job matches the last one — on both platforms, in the same change.

## Quick start

```bash
# audit both libraries — writes nothing
python3 scripts/verify-gitlab-library.py ../../ci-templates
python3 scripts/verify-github-library.py ../../github-ci-library
```

Then read `SKILL.md` §3 and work the eight decisions in order.

## What the verifiers check

**GitLab** — `$?` captured on its own script line (the bug that makes a gate unable to fail);
`image:` on a concrete job instead of its anchor; a stage outside `stages:`; an `extends:` that
resolves nowhere; a `needs:` entry without `optional:`, or naming a job nothing defines;
language-prefixed project-level job names; unconditional jobs; `cache:` without `policy:`;
`artifacts:` without `expire_in:`.

**GitHub** — the mechanical half of `library-conventions.md` and `docs-contract.md`: SHA-pinned
actions, declared `permissions:`, container coordinates without fallbacks, input counts, step
summaries, and the documentation contract.

Both exit 1 on a P0, and neither replaces `yamllint`, `actionlint` or `shellcheck`.

## Layout

```text
SKILL.md        the runbook
AGENTS.md       the same skill for agents that read AGENTS.md
references/     gitlab-job-anatomy, github-job-anatomy, construct-mapping,
                known-pitfalls, library-conventions, docs-contract
scripts/        verify-gitlab-library.py, verify-github-library.py
```

## History

Absorbs the former `github-port` skill: its four references and its port verifier moved here
unchanged, so porting a job between the two libraries is now one capability of library
development rather than a separate skill.
