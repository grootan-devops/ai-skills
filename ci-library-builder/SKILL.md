---
name: ci-library-builder
description: >-
  Develops the shared CI template libraries themselves — adding, extending and auditing jobs in
  a GitLab CI template library and its GitHub Actions counterpart, keeping the two in step. Takes
  the path to one or both libraries, verifies them, then works out for a requested job: which
  stage, hidden template or concrete job, which image (default toolkit or a specific one), the
  script, needs and fail-fast wiring, artifacts, cache, rules, and which file it belongs in — or
  whether it should exist at all. Use when adding a job to ci-templates or github-ci-library,
  porting a job between them, or auditing either for structural defects.
---

# CI Library Builder

Builds and audits the **libraries**, not the pipelines that consume them. For scaffolding a
consuming repository, use `platform-builder`.

Two libraries, one set of decisions:

| | GitLab | GitHub |
|---|---|---|
| Library | `devops/ci-templates` | `github-ci-library` |
| Unit of reuse | hidden template `.Job:` + concrete `Job:` | reusable `workflow_call` workflow + job |
| Verifier | `scripts/verify-gitlab-library.py` | `scripts/verify-github-library.py` |

---

## 0. Reference Index — Load Before Acting

| Load this | Before you… |
|---|---|
| `references/gitlab-job-anatomy.md` | touch a GitLab job. **Required.** The eight decisions, in order. |
| `references/github-job-anatomy.md` | touch a GitHub job. **Required.** Only what differs, plus the Actions-only traps. |
| `references/construct-mapping.md` | translate any GitLab keyword to GitHub. **Required for a port.** |
| `references/known-pitfalls.md` | write or debug a reusable workflow. **Required.** Fourteen silent failure modes. |
| `references/library-conventions.md` | decide GitHub job structure, containers, variables, summaries. |
| `references/docs-contract.md` | update README, CHANGELOG or MIGRATION after a change. |

---

## 1. Inputs — always paths, always verified first

This skill takes **the path to each library it will touch**. Never work from memory of what a
library contains; the ref moves.

```bash
ci-library <command> --gitlab <path-to-ci-templates> --github <path-to-github-ci-library>
```

Either may be omitted when the work is one-sided. Before anything else:

```bash
python3 scripts/verify-gitlab-library.py <gitlab_path>
python3 scripts/verify-github-library.py <github_path>
```

**A P0 in either library is a stop.** Report it and ask whether to fix it first — adding a job on
top of a broken gate buries the defect. P1s are judgement calls: quote them and continue.

Then read, in the library itself and not from this file:

- `common/.gitlab-ci.yml` — `stages:`, `default:`, every `.*-rules` anchor, every variable.
- the module file the job would live in, in full.
- the library's own `README.md` and `MIGRATION.md`.

## 2. Commands

| Command | Arguments | Does |
|---|---|---|
| `ci-library verify` | `--gitlab` and/or `--github` | Runs both verifiers plus `yamllint` / `actionlint`; writes nothing. |
| `ci-library add job` | a description of the job, `--gitlab` and/or `--github` | Walks §3, proposes the job on each named platform, applies on approval. |
| `ci-library extend` | an existing job name | Same decisions, scoped to a change: what must move, and what must not. |
| `ci-library port` | a job name, `--gitlab` → `--github` | Ports one job or module between the libraries using `construct-mapping.md`. |
| `ci-library audit` | `--gitlab` and/or `--github` | Verifier plus the judgement checks no script can make (§4). Read-only. |

Invoked bare, or with a description but no library path, **ask** — never guess a path, and never
scaffold silently.

## 3. Adding or extending a job

Work the eight decisions in `gitlab-job-anatomy.md` **in order**, then their GitHub equivalents in
`github-job-anatomy.md`. The order matters: a later answer never revises an earlier one.

1. **Should it exist?** If the library cannot decide it for every consumer, it is a hidden
   template, not a job. If the platform already provides the outcome, the right job is no job.
2. **Hidden or concrete.** Consumer must choose something → hidden. Identical everywhere →
   concrete. A concrete job carries no language segment (`Dependency:Download`, not
   `Node:Dependency:Download`); `Go:Dependency:Download` is the deliberate exception.
3. **Stage**, from what the job asserts — never from what is convenient.
4. **Image**: default toolkit first. Declare one only when the toolkit genuinely lacks the tool,
   and put it on the anchor, never on a concrete job and never on a consumer.
5. **`needs:` and fail-fast**: explicit edges, `optional:` on every entry, `artifacts: false`
   unless the payload is opened, nothing running after a failed input, no waiting on work it does
   not read, gate placed to stop waste earliest without serialising overlappable work.
6. **`rules:`** — extend an existing anchor. Never unconditional, never an inline copy.
7. **`cache:` / `artifacts:`** — one warmer (`pull-push`), many readers (`pull`); artifacts always
   carry `expire_in`.
8. **Which file** — an existing module unless a consumer would want these jobs *without* the rest
   of that module. A new file is a new `include:` entry, a new `WORKFLOW` option, README,
   CHANGELOG and possibly MIGRATION.

Then, for the GitHub side of the same capability: the workflow file that owns the phase, `needs:`
plus `if: ${{ !cancelled() && needs.X.result != 'failure' }}`, `container:` with credentials and
no fallback on image coordinates, a declared `permissions:` block derived from what the job does,
and a `GITHUB_STEP_SUMMARY` block on every job.

**Both libraries or one?** A capability that exists on both platforms is added to both in the same
change, or the gap is stated explicitly in the report. Silent divergence between the two libraries
is the failure this skill exists to prevent.

## 4. Audit — what no script can check

The verifiers cover structure. These need judgement:

- A job doing two things that should be two jobs.
- A stage chosen for convenience rather than for what the job asserts.
- A second image where the toolkit already had the tool — a pull on every run for nothing.
- A `needs:` edge that waits for a job whose artifacts it never opens.
- A gate that serialises work which could have overlapped.
- A `WORKFLOW` option with no job behind it, or a job unreachable from any option.
- A capability present in one library and silently missing from the other.

## 5. Hard stops

Refuse and report rather than guess:

- A library path that does not exist, or is not the library it claims to be.
- A P0 from either verifier, until the user decides whether to fix it first.
- A GitLab construct with no honest GitHub equivalent that is not already in
  `construct-mapping.md` — add it to the table with its adaptation, or stop.
- A change that would make a previously-blocking check non-blocking.
- Anything requiring a credential decision (`CI_JOB_TOKEN` scoping, a PAT for cross-repo writes).

## 6. Before reporting done

```bash
python3 scripts/verify-gitlab-library.py <gitlab_path> --strict
python3 scripts/verify-github-library.py <github_path> --strict
yamllint -c <gitlab_path>/.yamllint.yml <gitlab_path>
actionlint && yamllint -s <github_path>/.github/workflows/ && shellcheck <github_path>/scripts/*.sh
```

Then the documentation contract in `docs-contract.md`: README for a new module or option,
CHANGELOG always, MIGRATION when an existing pipeline must change.
