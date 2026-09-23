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
python3 scripts/verify-gitlab-library.py ../../gitlab-ci-library
python3 scripts/verify-github-library.py ../../github-ci-library
```

Then read `SKILL.md` §3 and work the eight decisions in order.

Read each library's short README index, then follow only the relevant module, configuration
and integration-example links. Resolve links relative to their containing page, at the same
local checkout or explicit ref (`main` by default for remote sources). Local uncommitted docs
remain visible; older monolithic READMEs are read by section. Migration notes are needed for
upgrades and compatibility checks, not every edit.

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

Both also validate README-linked documentation: required topic coverage, local links and
anchors, YAML examples, and library workflow references. Run only that check with
`python3 scripts/verify-docs.py <library_dir>`; use `--no-contract` for Helm or another
repository that does not expose the CI topic catalog. The validator reads the docs tree for
coverage; this is not the reading strategy used by an agent doing a focused task.

Run the documentation and required-dependency regression cases with:

```bash
python3 -m unittest discover -s scripts/tests
```

## Layout

```text
SKILL.md        the runbook
AGENTS.md       the same skill for agents that read AGENTS.md
references/     gitlab-job-anatomy, github-job-anatomy, construct-mapping,
                known-pitfalls, library-conventions, docs-contract
scripts/        verify-gitlab-library.py, verify-github-library.py, verify-docs.py,
                documentation.py, tests/
```

## History

Absorbs the former `github-port` skill: its four references and its port verifier moved here
unchanged, so porting a job between the two libraries is now one capability of library
development rather than a separate skill.
