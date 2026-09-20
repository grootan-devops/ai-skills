# Scenario Selection & Job Grouping — GitHub Actions

Covers what is specific to GitHub. The machine-readable map is
[`../workflow-map.json`](../workflow-map.json) — the only copy, loaded at runtime by
`ci_checks.py`.

> **The library documents its own behaviour.** Read `github-ci-library/README.md` and
> `MIGRATION.md` **every run**: the module catalog, per-module inputs and outputs, the execution
> matrix, and the two-tier release model live there and are authoritative. This page carries
> only what the library does not state about itself.

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
|---|---|---|
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

| What | Pin to |
|---|---|
| Library reusable workflows | a release tag — `@1.0.0` |
| Third-party actions in a consumer workflow | a full 40-char commit SHA, version in a trailing comment |

A tag on a *third-party* action is mutable: the author can repoint `v1` at new code that then runs
with your `GITHUB_TOKEN`. The library is a first-party, reviewed repository, so a release tag is
the right trade there — it keeps migrations legible.

## 6. Permissions

Declare at the workflow level, minimum first, and elevate per job. The library's own quick start
shows the pattern: `contents: read` for `pr.yml`, `contents: write` only for `release.yml`.

An absent `permissions:` block inherits the repository default, which on older repositories is
read/write on every scope. Full reasoning in [`security-addendum.md`](./security-addendum.md).
