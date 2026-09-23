# GitLab → GitHub Actions Construct Mapping

Authoritative keyword table. Load before translating anything. If a construct is missing
here, it has not been ported before — add it with its adaptation, or stop and ask.

---

## 1. Pipeline structure

| GitLab | GitHub Actions | Notes |
| --- | --- | --- |
| `stages: [...]` | `needs:` edges between jobs | No stage keyword. A job starts when *its* dependencies finish, not when a stage drains. Keep the stage order as documentation only. |
| `stage: build` | Position in the `needs:` graph | Record the original phase in the job name or a table in the README-linked pipeline lifecycle guide so the mapping stays legible. |
| `.hidden-template:` + `extends:` | A reusable workflow (`on: workflow_call`) | Each hidden template that a project materialises becomes a callable workflow, or a job inside one. |
| `extends: [.a, .b]` | Job-level duplication, or a matrix leg | Reusable workflows cannot inherit. Duplicate the few lines, or collapse variants into a matrix. |
| `include: {project, ref, file}` | `uses: org/repo/.github/workflows/x.yml@ref` | The caller pins the ref. |
| `!reference [.job, script]` | A file in `scripts/`, invoked by path | YAML anchors do not cross workflow files. |
| YAML anchors (`&x` / `*x`) | Same-file only | Anchors work within one workflow file but never across files. |
| `.pre` / `.post` stages | A job with no `needs:` / a job with `needs: [all]` + `if: always()` | |
| Parent/child pipelines (`trigger:`) | A workflow returning `strategy.matrix` | **No equivalent.** A reusable workflow cannot be called from a matrix, so the parent returns the matrix and the caller fans out. |

## 2. Triggers & rules

| GitLab | GitHub Actions |
| --- | --- |
| `rules: if: $CI_PIPELINE_SOURCE == "merge_request_event"` | `on: pull_request: branches: [master]` |
| `rules: if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` | `on: push: branches: [master]` |
| `rules: if: $CI_PIPELINE_SOURCE =~ /^(web\|api)$/` | `on: workflow_dispatch:` |
| `rules: if: $CI_COMMIT_TAG` | `on: push: tags:` |
| `rules: exists: [main.tf]` | `if: hashFiles('main.tf') != ''` at job level |
| `rules: when: never` | Omit the job, or `if:` false |
| `rules: when: manual` | `workflow_dispatch`, or an `environment:` approval gate |
| `rules: when: on_failure` | `if: failure()` |
| `rules: when: always` | `if: always()` |
| `WORKFLOW` dropdown variable | **No equivalent.** One workflow file per scenario + `workflow_dispatch` inputs. |
| `workflow: rules: variables: PIPELINE_NAME` | `run-name:` at workflow level |
| `interruptible: true` | `concurrency: {group, cancel-in-progress: true}` |
| `auto_cancel: on_new_commit` | `concurrency` at workflow level |

## 3. Dependencies & artifacts

| GitLab | GitHub Actions |
| --- | --- |
| `needs: [{job, artifacts: true}]` | `needs: [job]` + `actions/download-artifact` |
| `needs: [{job, optional: true}]` | `needs: [job]` + `if: !cancelled() && needs.job.result != 'failure'` |
| `dependencies: []` | Do not download artifacts (the default) |
| `artifacts: paths:` | `actions/upload-artifact` |
| `artifacts: expose_as:` | The artifact `name:` |
| `artifacts: expire_in: 1 week` | `retention-days: 7` |
| `artifacts: when: always` | `if: !cancelled()` on the upload step |
| `artifacts: reports: junit:` | `mikepenz/action-junit-report` (publishes a Check + annotations) |
| `artifacts: reports: dotenv:` | **Job `outputs:`**. There is no cross-job env file; write `$GITHUB_OUTPUT` and declare `outputs:`. |
| Artifacts from another pipeline | `gh run download <run-id>` with `actions: read` |

## 4. Caching

| GitLab | GitHub Actions |
| --- | --- |
| `cache: key: files: [uv.lock]` | `actions/cache` with `key: py-${{ hashFiles('uv.lock') }}` |
| `cache: policy: pull-push` | `cache/restore` + `cache/save` in the dependency job |
| `cache: policy: pull` | `cache/restore` only |
| `cache: key: prefix:` | A literal prefix in the key plus `restore-keys:` |

> Never use a static cache key. A key that does not hash the lockfile silently serves a
> stale dependency set.

## 5. Execution environment

| GitLab | GitHub Actions |
| --- | --- |
| `image: repo:tag` | `container: {image, credentials}` |
| `image: {name, entrypoint: [""]}` | `container: {image, options: --entrypoint=""}` |
| `services: [docker:dind]` | **Drop `container:`** and use the runner's daemon. A containerised job has no Docker socket. |
| `default: image:` | Repeat `container:` per job. There is no workflow-level default. |
| `default: before_script:` | A repeated step, or a `scripts/` helper |
| `variables:` (group level) | Organisation `vars.*` |
| `variables:` (job level) | `env:` at job or step level |
| `tags: [runner-tag]` | `runs-on: ${{ vars.CI_RUNNER }}` |
| `retry: {max: 2, when: [...]}` | **No equivalent.** Document it; do not simulate with a loop. |
| `timeout: 1h` | `timeout-minutes: 60` |
| `allow_failure: true` | `continue-on-error: true` |
| `allow_failure: exit_codes: [2]` | **No equivalent.** Capture `$?`, branch, expose `fail-on-warnings`. |

## 6. Registries & credentials

| GitLab | GitHub Actions |
| --- | --- |
| `$CI_REGISTRY` | `vars.IMAGE_REGISTRY` |
| `$CI_JOB_TOKEN` for the project's own registry | `secrets.IMAGE_REGISTRY_USERNAME` / `_PASSWORD`. **`GITHUB_TOKEN` is not a registry credential for a private third-party registry** — never substitute it silently. |
| `$CI_DEPENDENCY_PROXY_*` | No equivalent. Pull through the organisation registry instead. |
| OCI when `CHART_REGISTRY` is set; otherwise the current project's Helm package registry | OCI only, requiring `CHART_REGISTRY` and chart-specific repository/credentials. No HTTP selector or image-credential fallback. |
| GitLab generic package registry | GitHub Release assets |
| `release-cli create` | `softprops/action-gh-release` (creates the tag too) |
| `docker login` inside a container job | `crane auth login` — the container has no daemon |
| `docker manifest inspect` | `crane manifest` |

## 7. Variables — the CI_* surface

| GitLab | GitHub Actions |
| --- | --- |
| `$CI_PROJECT_DIR` | `${{ github.workspace }}` |
| `$CI_PROJECT_PATH` | `${{ github.repository }}` |
| `$CI_PROJECT_PATH_SLUG` | `${{ github.repository }}` with `/` → `_` |
| `$CI_COMMIT_SHA` | `${{ github.sha }}` |
| `$CI_COMMIT_REF_NAME` | `${{ github.ref_name }}` |
| `$CI_COMMIT_REF_PROTECTED` | `${{ github.ref_protected }}` |
| `$CI_DEFAULT_BRANCH` | `${{ github.event.repository.default_branch }}` |
| `$CI_PIPELINE_ID` / `$CI_PIPELINE_IID` | `${{ github.run_id }}` / `${{ github.run_number }}` |
| `$CI_JOB_ID` | `${{ github.job }}` (name, not id) |
| `$CI_MERGE_REQUEST_IID` | `${{ github.event.pull_request.number }}` |
| `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | `${{ github.event.pull_request.base.ref }}` |
| `$CI_MERGE_REQUEST_DIFF_BASE_SHA` | `${{ github.event.pull_request.base.sha }}` |
| `$CI_API_V4_URL` | `${{ github.api_url }}`, or the `gh` CLI |
| `$CI_PROJECT_URL` | `${{ github.server_url }}/${{ github.repository }}` |
| `$CI_JOB_STARTED_AT` | No direct equivalent; use `github.event.repository.updated_at` or `date -u` |
| `$GITLAB_CI` | `${{ github.actor }}`-independent: use `env.CI` |

## 8. Reporting

| GitLab | GitHub Actions |
| --- | --- |
| Job log sections | `::group::` / `::endgroup::` |
| Pipeline/MR widget report | `$GITHUB_STEP_SUMMARY` (markdown) — **required on every job** |
| `artifacts: reports: junit` annotations | `mikepenz/action-junit-report` with `annotate_only` |
| Failure visibility | `::error title=X::message` and `::warning title=X::message` |

## 9. Constructs with no equivalent — the honest-adaptation register

| GitLab | Adaptation | Documented where |
| --- | --- | --- |
| Parent/child pipelines | `mono.yml` returns a `strategy.matrix` of changed projects | README → integration examples → monorepos |
| `WORKFLOW` dropdown | One workflow file per scenario + `workflow_dispatch` | README → pipeline lifecycle → Available Scenario Workflows |
| `allow_failure: exit_codes` | `fail-on-warnings` input, default `false` | README → security and scanning → Scan Exit Codes |
| `retry: when: [runner_system_failure]` | None. GitHub retries nothing automatically. | Note it in the port report |
| `resource_group` | `concurrency:` (approximate — it serialises, it does not queue) | Note the difference |

Add a row here whenever a new construct is adapted. An adaptation that is not in this table
is an undocumented behaviour change.
