# Library Conventions

The structural shape a ported library must have. `scripts/verify-port.py` enforces the
mechanical parts of this document.

---

## 1. Repository layout

```text
.github/
  actionlint.yaml          # documented suppressions only
  workflows/
    init.yml               # version & registry resolution — every pipeline starts here
    check.yml              # release guards, one job each + verdict
    lint.yml               # config/doc linters as a matrix
    <lang>-build.yml       # dependency -> build ∥ test
    <lang>-lint.yml        # linters as a matrix
    docker.yml / buildah.yml
    chart.yml
    scan.yml / sbom.yml / secret-scanning.yml / trivy-cache.yml
    terraform-*.yml
    deploy-*.yml
    release.yml / notify.yml
    mono.yml
    ci.yml / cd.yml        # the library's own pipeline
scripts/*.sh               # shared shell, shellcheck-clean
templates/                 # report templates (e.g. trivy-junit.tpl)
.shellcheckrc
VERSION
README.md CHANGELOG.md MIGRATION.md
```

## 2. Job anatomy

Every job declares, in this order:

```yaml
job-name:
  name: "Phase: Human Readable"
  needs: [upstream]                  # unless a root job
  if: ${{ ... }}                     # tolerating skipped upstreams where needed
  runs-on: ${{ vars.CI_RUNNER || 'ubuntu-26.04' }}
  timeout-minutes: 15
  permissions:                       # least privilege, never the default set
    contents: read
  container:
    image: ${{ vars.IMAGE_REGISTRY }}/${{ vars.TOOLKIT_BUILD_IMAGE }}
    credentials:
      username: ${{ secrets.IMAGE_REGISTRY_USERNAME }}
      password: ${{ secrets.IMAGE_REGISTRY_PASSWORD }}
  defaults:
    run:
      working-directory: ${{ inputs.project-path || vars.PROJECT_PATH || '.' }}
```

The only job permitted to omit `container:` is one that builds a container image
(pitfall §5), plus pure-orchestration jobs that run no tooling. Both state why in a comment
above the job.

## 3. Permissions

GitLab has no per-job token scoping; GitHub does, and the default is broad. **Every job
declares `permissions:` explicitly**, listing only the scopes it actually uses. A job that
declares nothing inherits the repository default, which is usually far more than it needs.

Least privilege cuts both ways: an over-privileged job is a finding, and so is an
under-privileged one. Deriving the scope from what the job *does*:

| The job… | Needs |
| --- | --- |
| Checks out code, lints, builds, scans | `contents: read` |
| Pushes an image or chart to the registry | `contents: read` + `packages: write` |
| Publishes a JUnit report as a Check | `contents: read` + `checks: write` |
| Downloads artifacts from **another** run (`gh run download`) | `contents: read` + `actions: read` |
| Resolves the merged pull request (release provenance) | `contents: read` + `actions: read` + `pull-requests: read` |
| Creates a git tag or a GitHub Release | `contents: write` |
| Comments on or labels a pull request | `pull-requests: write` |
| Only reads the `needs` context (a verdict/fan-in job) | `contents: read` |

Two rules that are easy to get wrong:

- **Uploading an artifact needs no permission.** `actions/upload-artifact` works under
  `contents: read`. Only *cross-run download* needs `actions: read`. A publish job that
  declares `contents: write` merely to upload an artifact is over-privileged.
- **Pushing to a *different* repository is not a permissions problem.** `GITHUB_TOKEN` is
  scoped to the current repository whatever you declare, so a GitOps commit into another
  repo needs a PAT or app token (`secrets.GITOPS_TOKEN`). Raising `contents: write` does not
  help and misleads the next reader.

### Reusable workflows cannot raise the caller's token

A reusable workflow's `permissions:` is a **ceiling request, not a grant**. The token it
receives is the caller's, and GitHub will not grant a scope the caller did not have. If the
library declares `packages: write` but the calling workflow declares only `contents: read`,
the push fails at runtime with a permissions error that names neither file.

So the README must state, per scenario, the `permissions:` block the caller needs:

```yaml
# In the consuming repository's pr.yml
permissions:
  contents: read
  packages: write      # docker.yml / chart.yml push
  actions: read        # release.yml artifact restore
  checks: write        # *-build.yml test reporting
```

Document the caller-side block alongside every Quick Start and integration example.

## 4. Actions register

The full set a ported library should need. Anything outside this list needs a stated reason
in the port report — see SKILL.md §1.6.

| Action | Tier | Why it earns a place |
| --- | --- | --- |
| `actions/checkout` | first-party | Nothing else fetches the repository. |
| `actions/cache/restore` · `actions/cache/save` | first-party | Split restore/save is how the dependency job owns the write and every other job reads. |
| `actions/upload-artifact` · `actions/download-artifact` | first-party | The artifact API is not reachable from a `run:` step. |
| `docker/setup-buildx-action` | vendor | BuildKit driver setup. |
| `docker/login-action` | vendor | Registry auth for the one non-containerised job. |
| `docker/build-push-action` | vendor | Build and push with cache export; a hand-rolled `docker buildx` loses the cache wiring. |
| `mikepenz/action-junit-report` | community | Publishes a GitHub Check with per-test annotations. A `run:` step cannot create Checks. |
| `softprops/action-gh-release` | community | Creates the tag, the Release and uploads assets in one call. |

Deliberately **not** used, because the build container already provides the tool:
`setup-python`, `setup-node`, `setup-go`, `setup-java`, `setup-uv`, `setup-helm`,
`setup-crane`, `setup-terraform`, `setup-tflint`, `mikefarah/yq`. If a port introduces one,
it has usually skipped the container (SKILL.md §1.4) rather than found a real need.

### Pinning

```yaml
- uses: actions/checkout@v7                                          # first-party: major tag
- uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v3
```

Non-`actions/*` entries carry a 40-character SHA plus the version comment. Two workflows must
never sit on different majors of the same action. Dependency-update automation is currently
deferred, so maintainers update these pins together as one reviewed change.

## 5. The `init` contract

One job resolves everything version- and registry-shaped, and exposes it as workflow
outputs. Downstream workflows **consume**, never recompute.

Minimum outputs: `tag`, `release-version`, `is-release`, `version-suffix`,
`image-tag`, `image-push-tag`, `image-repository`, `image-dev-repository`,
`image-push-repository`, `chart-name`, `chart-version`, `chart-app-version`,
`chart-push-version`, `chart-repository`, `chart-dev-repository`,
`chart-push-repository`, `major-version`, `minor-version`, plus release provenance
(`upstream-run-id`, `merged-pr-number`, `candidate-image-tag`, `candidate-chart-version`).

`init` validates required organisation variables in its first step and fails with a message
naming each missing one.

## 6. The two-tier release model

Port this behaviour exactly; it is the reason the library exists.

- **Pull request** builds the candidate: `<version>-<run_number>.<pr_number>`, pushed to the
  dev repository, scanned there.
- **Merge to the release branch** promotes: resolve the merged PR → find its successful run
  → promote that artifact **by digest** → tag, release, notify. Nothing is rebuilt.
- **A manual run on a release branch** forces a candidate suffix and the dev repositories,
  so it cannot overwrite a published artifact.

## 7. Variables and secrets

GitHub splits what GitLab unifies. A GitLab variable set at group level and one set in a job
share one namespace with built-in precedence; GitHub's `vars.X` and `inputs.x` are separate
namespaces. Writing `inputs.x || vars.X || 'literal'` to hand-roll that precedence declares
one fact three times, and forces the input to `default: ""` so the chain can fall through —
which means the input block can no longer be read on its own.

**Do not write that chain.** Sort each value by who owns it:

| The value is… | Then it is | Default |
|---|---|---|
| one repository's layout — chart directory, project root, Dockerfile path, file names | an **input** | a real `default:` in the declaration |
| shared infrastructure every repo in the org points at — registry host, Trivy or Sonar server, GitOps endpoint | a **`vars.*`** | a literal fallback, or none |
| an image coordinate | a **`vars.*`** | **none** — SKILL §1.4 |
| a credential | a **secret** | none |

A layout value gets its default where a reader looks for it:

```yaml
      chart-dir:
        required: false
        type: string
        default: "./chart"
        description: "Directory containing Chart.yaml, relative to the repository root."
```

The cost is real and deliberate: a repository whose chart is not at `./chart` repeats
`chart-dir:` in every caller workflow that touches the chart, where one organisation
variable would have covered them all. That is accepted in exchange for a declaration that
states its own default.

**Mandatory means `required: true` with no default.** `required: false` plus `default: ""`
on a value the workflow cannot run without is a declaration that lies: GitHub accepts the
caller, and the empty value fails deep inside a script — or worse, silently builds a wrong
path, registry reference or cache key. Reserve `default: ""` for inputs where empty is a
real, handled state, and say in the description what empty means.
- One registry credential pair, named `IMAGE_REGISTRY_USERNAME` / `IMAGE_REGISTRY_PASSWORD`,
  used library-wide.

> **Organisation limits.** GitHub allows up to 1,000 organisation variables and 1,000
> organisation secrets, 48 KB each; a single workflow reads at most **100 organisation
> secrets** (alphabetically first, if more are granted). A library of this shape uses
> ~45 variables and ~11 secrets. Re-verify the caps in current GitHub documentation before
> quoting them.

## 8. Step summaries

Every job appends to `$GITHUB_STEP_SUMMARY`:

```yaml
- name: Publish <Thing> Summary
  if: always()
  env:
    OUTCOME: ${{ job.status }}
  run: |
    set -euo pipefail
    {
      echo "### 🔎 <Thing>"
      echo ""
      case "${OUTCOME}" in
        success) echo "✅ Passed." ;;
        failure) echo "❌ Failed. <the exact command that fixes it>" ;;
        *)       echo "Status: ${OUTCOME}" ;;
      esac
      echo ""
    } >> "${GITHUB_STEP_SUMMARY}"
```

A summary that says only "failed" is not finished. It must carry the remedy — the command to
run, the file to edit, the section to add. Failures additionally emit
`::error title=...::`, so the run page shows them without opening the summary tab.

## 9. Shared scripts

- Anything used by more than one workflow, or longer than ~40 lines, lives in `scripts/`.
- Every script: `#!/usr/bin/env bash`, `set -euo pipefail`, a header comment naming the
  GitLab job it ports and its required environment, and `shellcheck`-clean at default
  severity.
- Scripts read configuration from environment with `: "${VAR:=default}"` / `: "${VAR:?message}"`.
- Scripts write their own step summary when they own the user-facing result.

## 10. Linting gates

All three must pass before a port is reported complete:

```bash
actionlint
yamllint -s .github/workflows/
shellcheck scripts/*.sh
```

`.shellcheckrc` may hold documented, library-wide suppressions (for example `SC2329` where
`cleanup` is invoked by `trap`). Per-finding suppressions are line-scoped with a reason.

## 11. The library's own pipeline

The library runs its own workflows against itself — `ci.yml` on pull requests, `cd.yml` on
merge — plus `actionlint` and `shellcheck` self-gates. The released version is the contents
of `VERSION`, bumped in the pull request that ships the change.
