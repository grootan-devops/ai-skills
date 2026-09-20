# GitHub Job Anatomy — the same eight decisions, expressed in Actions

Read [`gitlab-job-anatomy.md`](./gitlab-job-anatomy.md) first: the *decisions* are identical and
are not repeated here. This file is only what changes when the answer is written as a GitHub
Actions job, and the traps that have no GitLab counterpart.

`construct-mapping.md` is the keyword table; `known-pitfalls.md` is the failure list. Both are
required reading before writing a workflow.

---

## 1. Does the job belong in the library — and is it one job or several?

Same test. One extra opportunity: GitHub charges nothing for job parallelism, so a GitLab job
whose `script:` runs several independent checks becomes **several jobs or a matrix**, not one.
One failing check costs one job and names itself in the run graph. Set `fail-fast: false` on
every matrix — one run should report every problem, not the first.

## 2. Hidden or concrete → reusable workflow or job

GitHub has no hidden-job concept. The equivalent split is:

| GitLab | GitHub |
|---|---|
| hidden template `.Node:Build` the consumer extends | a `workflow_call` workflow with `inputs:` the caller passes |
| concrete job `Chart:Lint` | a job inside a reusable workflow, needing no input |

A caller passes only what it alone can know — which artifact, which version. Everything the
organisation knows is a `vars.*`, never an input. Target **fewer than 8 inputs per workflow**,
and zero required inputs an upstream `init` job could have produced.

## 3. Which stage → which workflow file, and `needs:`

There are no stages. The GitLab stage becomes the workflow file that owns that phase
(`check.yml`, `lint.yml`, `ci.yml`, `chart.yml`, `scan.yml`, `release.yml`) plus a `needs:` edge.
Put a new job in the workflow that owns its phase; a new file is for a new scenario, not a new
job.

## 4. Which image → `container:` on every job

A GitHub runner is not the toolkit image. State it explicitly, on every job:

```yaml
    container:
      image: ${{ vars.IMAGE_REGISTRY }}/${{ vars.TOOLKIT_BUILD_IMAGE }}
      credentials:
        username: ${{ secrets.IMAGE_REGISTRY_USERNAME }}
        password: ${{ secrets.IMAGE_REGISTRY_PASSWORD }}
```

- **No `|| 'fallback'` on an image coordinate.** An unset container variable must fail the pull
  by name; a plausible-but-wrong default is worse than a missing one. Behavioural defaults
  (`PROJECT_PATH`, `CI_RUNNER`, file names) keep their fallbacks.
- The toolkit image already carries the toolchain, so **a containerised job needs no `setup-*`
  action.** Reaching for `setup-python` or `setup-node` here is a porting mistake, not a
  convenience.
- A job that must talk to a Docker daemon (image build) runs **without** `container:`.

## 5. `needs:` — and the two traps GitLab does not have

```yaml
    needs: lint
    if: ${{ !cancelled() && needs.lint.result != 'failure' }}
```

- **There is no `optional:`.** A job whose dependency was *skipped* is skipped too, unless the
  condition says otherwise. `if: ${{ !cancelled() && needs.X.result != 'failure' }}` is the
  idiom for "a failed dependency blocks me, a skipped one does not".
- **`if: always()` on cleanup jobs**, or they are skipped exactly when they are needed.
- Everything from the GitLab file still holds: explicit edges, no waiting on work you do not
  consume, and a gate placed where it stops waste earliest without serialising overlappable work.

## 6. `rules:` → `on:` and `if:`

Workflow-level `on:` filters replace the pipeline-source half of a rules anchor; job-level `if:`
replaces the rest. There is no anchor to extend, so the condition is written once per job — keep
it short enough to read, and push repeated conditions up into the caller's `on:`.

GitLab's `WORKFLOW` dropdown has no equivalent: it becomes **one workflow file per scenario**
plus `workflow_dispatch`, never a single gated dispatcher that greys out half its graph.

## 7. `cache:` and `artifacts:`

`actions/cache` and `actions/upload-artifact` — first-party, and the only action family allowed
to float on a major tag. Same halves as GitLab: one warmer, many readers; artifacts for what a
later job opens. Set `retention-days` explicitly, and `if-no-files-found: error` when the
artifact is required — the default silently uploads nothing.

## 8. Every job declares `permissions:`

GitHub's default token is broad; the library never relies on it.

| The job… | Needs |
|---|---|
| checks out, lints, builds, scans | `contents: read` |
| pushes an image or chart | `+ packages: write` |
| publishes a JUnit/scan report as a Check | `+ checks: write` |
| downloads another run's artifacts | `+ actions: read` |
| creates a tag or Release | `contents: write` |

Both directions are findings: under-privilege fails at runtime with a 403, over-privilege
misleads every later reader. A reusable workflow **cannot raise the caller's token** — it is a
ceiling request, so the caller-side block must be documented per scenario. Cross-repository
writes need a PAT; raising `contents: write` does not help and misdescribes the job.

---

## Every job writes a step summary

This has no GitLab counterpart and is not optional:

```bash
{ echo "### 🏷️ Git tag"; echo; echo "✅ \`${TAG}\` is available."; echo; } >> "${GITHUB_STEP_SUMMARY}"
```

Say what passed, what failed, and the exact command that fixes it. Failures also emit
`::error title=...::` so they surface on the run page, not only in the summary tab.

## Shell and action rules

- `set -euo pipefail` opens every `run:` block, except where a non-zero exit is captured
  deliberately (`set -uo pipefail`).
- **Environment over interpolation**: pass `${{ ... }}` into `env:` and read `${VAR}` in the
  script. Interpolating an expression straight into shell is an injection surface and defeats
  shellcheck.
- **Never nest a heredoc inside `run: |`** — the inner body's indentation ends the YAML block.
  Emit lines with `echo`.
- No explanatory comments inside a `run:` body; rationale goes above the job.
- Shared or >40-line scripts live in `scripts/*.sh`, invoked after checking the library out at
  `github.job_workflow_sha`:

```yaml
      - name: Checkout CI Library
        uses: actions/checkout@v7
        with:
          repository: ${{ vars.CI_LIBRARY_REPO }}
          ref: ${{ github.job_workflow_sha }}
          path: .ci-library
```

- Actions, in order of preference: **none** (the toolkit image has the tool) → first-party
  `actions/*` → the tool vendor's own action → a well-established community action. Everything
  outside `actions/*` is pinned to a full 40-character SHA with the version in a trailing
  comment, and pinned to the latest release at authoring time.
