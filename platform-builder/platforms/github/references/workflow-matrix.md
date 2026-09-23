# Scenario Selection & Job Grouping — GitHub Actions

Covers what is specific to GitHub. The machine-readable map is
[`../workflow-map.json`](../workflow-map.json) — the only copy, loaded at runtime by
`ci_checks.py`.

> **The library documents its own behaviour.** Start at the resolved `github-ci-library/README.md`
> index, then follow its pipeline lifecycle, relevant module and matching example links.
> Keep the same ref for all pages; read migration notes when comparing versions. This page
> carries only what the library does not state about itself.

---

## 1. The consumer writes scenarios, not jobs

The library ships **reusable workflows**. A consumer repository composes them:

```yaml
jobs:
  init:
    uses: grootan-devops/github-ci-library/.github/workflows/init.yml@1.0.0
    secrets: inherit

  image:
    needs: [init, build]
    uses: grootan-devops/github-ci-library/.github/workflows/docker.yml@1.0.0
    secrets: inherit
    with:
      image-tag: ${{ needs.init.outputs.image-push-tag }}
      image-repository: ${{ needs.init.outputs.image-push-repository }}
```

Two rules follow, and both are easy to get wrong:

- **`init.yml` runs first and resolves everything once** — version, tag, image repository, dev
  repository, candidate tag. Never recompute those in a caller; consume `needs.init.outputs.*`.
  A caller that derives its own tag will disagree with the one the release promotes.
- **Every caller passes `secrets: inherit`.** The library reads organisation secrets directly.

GitHub has no GitLab Dependency Proxy. Use direct registry-qualified Dockerfile base images;
never add or pass `CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX` in GitHub workflows.

## 2. One scenario file per concern

GitLab exposes a `WORKFLOW` dropdown; GitHub greys out skipped jobs in the run graph, which makes
a single mega-workflow unreadable. The library's answer is one file per scenario, each with a
clean graph and a specific `run-name`.

| Scenario | Trigger | Calls |
| --- | --- | --- |
| `pr.yml` | `pull_request: [master]` | init + lint + *-build + docker + chart + scan + check |
| `release.yml` | `push: [master]` | init + docker/chart promote + release + notify |
| `build.yml` · `check.yml` · `lint.yml` | dispatch | the matching single concern |
| `image-scan.yml` · `chart-scan.yml` · `license-scan.yml` | dispatch | `scan` with `scan-type:` image / config / license |
| `sbom.yml` · `secret-scan.yml` · `sonarqube.yml` | dispatch | that module alone |
| `deploy.yml` | dispatch | deploy-argocd-gitops / deploy-komodo-gitops |

Which scenarios a repository should contain is keyed by shape in `workflow-map.json`. The engine
reports both directions: a scenario the shape expects but is absent, and one present that the
shape cannot use.

An **image-only** repository has a `Dockerfile` but no application dependency manifest or
chart. It builds, smoke-tests and scans the OCI image. Do not add `license-scan.yml`, `sbom.yml`,
a language build, or SonarQube: those source-oriented workflows require a dependency manifest.
Secret scanning still applies because it inspects Git history rather than language dependencies.

When a pull-request workflow uses `paths:`, include `.github/**` so changes to workflows,
actions, and repository automation always run the PR checks that validate them.

### Dependency-cache scope for Docker builds

The Python and Node reusable workflows restore/save `.uv-cache` and `.npm` using lockfile-based
GitHub Actions cache keys. A job can restore a matching cache entry, but a directory restored
in one job is not present on another job's filesystem automatically. In the current library,
`docker.yml` does not restore or download either directory; its BuildKit registry cache is for
image layers only. Therefore, do not claim that
`python-build.yml` or `node-build.yml` warms the Docker build context, and do not generate an
offline `RUN --mount` that assumes this cache handoff exists. If an application requires that
pattern, report that the current image workflow needs an explicit cache/artifact handoff. Keep
GitLab's `PROJECT_CACHE_KEY` convention out of GitHub Actions; these are different cache
mechanisms and workflows.

## 3. Release promotes, it does not rebuild

`docker.yml` and `chart.yml` in `is-release: true` mode resolve the candidate the pull request
already built and scanned, and promote it **by digest**. The released bytes are provably the
scanned bytes.

This is the single most important property to preserve when editing a consumer pipeline. A
"simplification" that rebuilds on the release path silently breaks the guarantee — the artifact
shipped is no longer the artifact verified.

A manual dispatch on `master` is safe by construction too: `init.yml` forces a
`-<run_number>.r<run_attempt>` candidate suffix and routes pushes to the **dev** repositories, so
it can never overwrite a published artifact.

## 4. There is no `optional:` on `needs:`

GitLab's `needs: [{job, optional: true}]` has no GitHub equivalent — a skipped dependency skips
its dependents. The library's convention is:

```yaml
if: ${{ needs.build.result != 'failure' }}
```

which tolerates a **skipped** upstream while still failing on a **failed** one. That is what lets
a single-focus scenario (`lint.yml`, say) run without waiting on jobs it never invoked.

Cleanup and teardown jobs need `if: always()`, or they are skipped exactly when needed.

## 5. Pinning

A reference is a **release tag or a commit SHA**. Nothing else, and the owner does not
change the answer.

| Reference | Verdict |
| --- | --- |
| `@1.0.0`, `@v7`, `@v2.1.0-rc.1` | fine — a release tag |
| `@a1b2c3…` (40 hex) | fine — an exact tree |
| `@dev`, `@latest`, any other alias | **P1** — pin to a SHA |
| `@main`, `@master`, `@HEAD` | **P0** — a branch runs whatever is on it when the job starts |

Publishing a release tag is a deliberate act, and moving one afterwards is visible in the
repository. A branch or a floating alias states no version at all: it resolves at run time,
to code the author can change, running with your `GITHUB_TOKEN`. Where an action publishes
no tags, a SHA with the version in a trailing comment is the only option.

### 5a. CI enforces this, not just the audit

`check.yml` runs a `Library Pinning` guard for every consumer: a job-level
`uses: …/.github/workflows/x.yml@<ref>` must pin a published tag. A branch, a commit SHA and
a pre-release tag all fail it. So a scaffold that emits `@dev` produces a repository whose
own pipeline refuses it — resolve a real tag, and only fall back to a branch when the
library has published none.

There is one escape hatch and it is not a default: `allow-unstable-library-refs: true` on
the `check.yml` caller — **testing only**, for a pull request tracking a library branch
while that branch is being written. Never scaffold it. If the user asks for it, add the
comment `# TESTING ONLY -- remove before merging.` above it, because the guard's whole value
is that nobody forgets.

The same rule reaches Helm: `chart-dependency-check.sh` rejects a `Chart.yaml` dependency
whose `version:` is a range (`^1.2.0`, `~1.2`) or a pre-release, as well as one pointing at
the dev repository. Scaffold chart dependencies with the exact version.

## 6. Permissions

`permissions: contents: read` at the workflow level, and elevate on the individual job — a
job calling a reusable workflow takes a `permissions:` block like any other. Putting the union
at the top hands `packages: write` to the lint job and the secret scan, which push nothing and
are the jobs most likely to run third-party code.

The module and example guides linked from the library's README carry the per-workflow requirement: which scope each called workflow
needs, so a caller can grant exactly that. Under-grant and the call fails at **startup**, not
midway — a reusable workflow cannot request a scope its caller did not have.

An absent `permissions:` block inherits the repository default, which on older repositories is
read/write on every scope. Full reasoning in [`security-addendum.md`](./security-addendum.md).

## 7. File layout

One blank line between top-level sections — `name`, `on`, `permissions`, `concurrency`,
`defaults`, `env`, `jobs` — and between jobs. `run-name` sits directly under `name`: both name
the run, so they are one section. A comment introducing a section belongs to it, so the blank
line goes **above** the comment.

```yaml
name: Security · Secret Scan

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  secret-scan:
    uses: grootan-devops/github-ci-library/.github/workflows/secret-scanning.yml@1.0.0
    secrets: inherit
```

The same reason as the Dockerfile grouping rule in
[`language-stacks-core.md`](../../../core/references/language-stacks-core.md) §3.1: a run of
keys with no break reads as one thing, and the next person edits the wrong one. `on:` and
`permissions:` govern the whole file and answer different questions — when it runs, and what
it may touch. Crammed together they scan as a single block of preamble.

## 7a. A scanning repository needs a scheduled cache warm

`trivy-cache.yml` warms the Trivy databases into the Actions cache. Scaffold a companion
`cache-warm.yml` **only when the repository actually scans** — when a scenario calls
`scan.yml` with `scan-type: image`, or calls `sbom.yml`.

Ask before adding it. A repository whose image is a CI toolkit that never leaves the build
farm is a legitimate reason not to scan at all, and one that ships no image has nothing to
scan; in both cases `trivy-cache.yml`, `cache-warm.yml` and the scan scenario all come out
together. Do not leave a `cache-warm.yml` behind for a repository that stopped scanning —
it warms a cache nothing reads.

When scanning is on, the warm workflow is not optional, and its absence is silent. The
library writes every cache only from the default branch, because GitHub scopes an entry to
the ref that wrote it: a run reads its own ref, its base branch and the default branch, so
a write from anywhere else is a duplicate nobody can use. Pull requests restore the cache
and never write it. If nothing runs on the default branch, the entry never exists and every
run re-downloads the vulnerability database.

A schedule executes on the default branch, which is what makes it the right trigger:

```yaml
# .github/workflows/cache-warm.yml
name: Cache · Trivy Database
run-name: "Cache · ${{ github.event_name }} · ${{ github.sha }}"

on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:

concurrency:
  group: "cache-warm-${{ github.ref }}"
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  trivy-cache:
    permissions:
      contents: read
      actions: write
    uses: grootan-devops/github-ci-library/.github/workflows/trivy-cache.yml@<ref>
    secrets: inherit
    with:
      enable-java-db: false
```

`enable-java-db: true` only for a JVM artifact — the Java database is roughly 900MB and
dominates the cached tree. Vary the cron minute per repository so an organisation's
repositories do not all warm at once.

This bites hardest on the promote-only release shape (§3): that `release.yml` never calls
`trivy-cache.yml`, so the scheduled run is the only writer in the repository.

## 8. Concurrency

**Every workflow that starts itself declares a `concurrency:` block** — not just the
pull-request one. Two manual runs, or a schedule landing on top of one, race the same
caches and registry tags.

> [!WARNING]
> **A workflow that can be called declares none — even if it also starts itself.** Inside a
> called workflow `github.workflow` is the **caller's** name, so the group below evaluates to
> the caller's own group. The caller holds it while waiting for the callee, the callee queues
> behind the caller, and GitHub cancels the run:
>
> ```text
> Canceling since a deadlock was detected for concurrency group:
> 'CI · PR Verification-refs/pull/1/merge' between a top level workflow and 'Lint'
> ```
>
> A dual-purpose workflow — `workflow_dispatch` **and** `workflow_call` — is the trap: it
> looks like it needs a group, and every group it can express deadlocks. The caller's group
> already covers the whole run, standalone invocations included.

```yaml
concurrency:
  group: "${{ github.workflow }}-${{ github.ref }}"
  cancel-in-progress: true
```

`cancel-in-progress` is the decision, not the group:

| Workflow | `cancel-in-progress` |
| --- | --- |
| Verification — PR, lint, scan, audit | `true` — a superseded run is answering a stale question |
| Anything that publishes or provisions — release, deploy, terraform apply | `false`, and a group that does not collide with verification, e.g. `release-${{ github.ref }}` |

Cancelling a run that has already pushed a tag or created infrastructure leaves the
half-done state behind, and the next run inherits it.

GitLab's equivalent is per job, not per file: `interruptible: true` on verification jobs
(the library sets it as a default) and `resource_group:` where two pipelines must not touch
the same target at once.

## 9. Run names

**Every project-level workflow declares a `run-name`.** Without one the runs list shows the
workflow's `name` on every row, identical for each run, and the only way to tell two apart
is to open them.

```yaml
name: CD · Production Release
run-name: "CD · ${{ github.event_name }} · ${{ github.sha }}"
```

The label is the first segment of `name:` — `CI`, `CD`, `Lint`, `Check`, `Scan`, `Audit`.
Then the two facts a row cannot otherwise carry: **what triggered it** and **exactly which
commit ran**. Actor and branch are already columns in the UI, so repeating them spends the
row's width on what is visible anyway; `github.sha` is not shown anywhere on the list.

A reusable workflow — `on: workflow_call` — declares none. It has no run of its own; the
caller's `run-name` titles the whole run, and a `run-name` here would be dead text.
