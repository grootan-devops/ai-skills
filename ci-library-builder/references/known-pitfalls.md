# Known Pitfalls

Every entry here is a real failure mode that is **silent, platform-specific, or misleading**.
Read before writing a reusable workflow; each one otherwise costs a debugging cycle.

---

## 1. `uses: ./...` inside a reusable workflow resolves to the *caller's* repository

A relative reusable-workflow path is resolved against the repository of the workflow run,
not the repository containing the file that wrote it. A library workflow that calls
`uses: ./.github/workflows/scan.yml` works when the library tests itself and breaks for
every consumer.

**Rule:** never nest reusable workflows by relative path inside a library. Share logic as a
script (§2) and duplicate the thin job wrapper.

```yaml
# Wrong, inside org/ci-library/.github/workflows/sbom.yml
scan:
  uses: ./.github/workflows/scan.yml

# Right: a self-contained job that runs the shared script
scan:
  steps:
    - run: bash "${GITHUB_WORKSPACE}/.ci-library/scripts/scan/trivy.sh"
```

## 2. Shared scripts must be checked out at `github.job_workflow_sha`

Reusable workflows cannot share YAML, so long scripts either get duplicated into every
workflow or live in `scripts/`. To fetch them **version-locked to the ref the caller
pinned**, check the library out at `github.job_workflow_sha` — the commit SHA of the
reusable workflow file itself.

```yaml
- name: Checkout CI Library
  uses: actions/checkout@v7
  with:
    repository: ${{ vars.CI_LIBRARY_REPO }}
    ref: ${{ github.job_workflow_sha }}
    path: .ci-library
    token: ${{ secrets.CI_LIBRARY_TOKEN || github.token }}
```

`actionlint`'s context schema does not know this property. Suppress it once, with the
reason, in `.github/actionlint.yaml`:

```yaml
paths:
  ".github/workflows/**.yml":
    ignore:
      - 'property "job_workflow_sha" is not defined'
```

## 3. A skipped dependency skips its dependents

`needs: lint` means "wait for lint". If `lint` is *skipped* — because the caller set
`lint: false` — GitHub skips the dependent job too. A hard `needs:` therefore silently
disables the build when an optional upstream is switched off.

```yaml
# Wrong: build never runs when lint is disabled
build:
  needs: lint
  if: ${{ !inputs.is-release }}

# Right: lint failure blocks, lint skip does not
build:
  needs: lint
  if: ${{ !cancelled() && !inputs.is-release && needs.lint.result != 'failure' }}
```

This is GitLab's `needs: optional: true` expressed correctly.

## 4. `download-artifact` fails when the run produced nothing

Downloading all artifacts errors with "Unable to find any artifacts" on a run that uploaded
none — a legitimate state for a release whose assets come from an upstream run.

```yaml
- name: Download This Run's Artifacts
  continue-on-error: true
  uses: actions/download-artifact@v8
```

## 5. A `container:` job has no Docker daemon

`container:` is exactly what replaces GitLab's `image:`, but a containerised job cannot
build images, and `docker login` / `docker manifest inspect` are unavailable.

- **Image build** → drop `container:` and run on the runner with buildx. This is the one
  justified exception to "every job is containerised"; state it in the file header.
- **Registry queries inside a container** → `crane auth login`, `crane manifest`,
  `crane digest`, `crane ls`, which talk to the registry directly.

Porting a job into a container without re-checking its registry calls is the easiest way to
break a working deploy workflow.

## 6. actionlint ignores file-level shellcheck `disable` directives

A `# shellcheck disable=SC2016` at the top of a `run:` body is honoured by standalone
shellcheck but **not** by actionlint's embedded pass.

- Prefer fixing the finding. Single-quoted `printf` formats containing markdown backticks
  are the common false positive — rewrite as `echo` with escaped backticks:

  ```bash
  { echo "### Title"; echo; echo "✅ \`${TAG}\` is available."; } >> "${GITHUB_STEP_SUMMARY}"
  ```

- Where the finding is genuinely wrong (a `$var` inside a `jq`/`yq` expression, a `$schema`
  JSON key), use a **line-scoped** directive immediately above the line.

## 7. A heredoc inside `run: |` breaks the YAML block scalar

The heredoc body's indentation is interpreted as YAML, terminating the block:

```yaml
run: |
  cat <<'JSON'
{
  "key": "value"      # ← ends the run: block, YAML parse error
}
JSON
```

Emit the lines with `echo` instead. This is the single most common YAML failure when
porting a GitLab job that generated a config file inline.

## 8. Secret names must be unified before wiring `container: credentials`

Every containerised job needs registry credentials, so a workflow that declared its own
`REGISTRY_USERNAME` while the rest of the library used `IMAGE_REGISTRY_USERNAME` fails at
image-pull time with a confusing error. Unify the names across the library **before** adding
containers, and record the rename in MIGRATION.md.

Workflows that declare an explicit `secrets:` block must list the registry secrets; those
that declare none inherit them.

## 9. `allow_failure: exit_codes: [2]` has no equivalent

A scanner that exits `2` for warnings will fail the job. Capture and branch:

```yaml
run: |
  set -uo pipefail
  bash scan.sh
  RESULT=$?
  case "${RESULT}" in
    0) echo "clean" ;;
    2)
      if [[ "${FAIL_ON_WARNINGS}" == "true" ]]; then exit 1; fi
      echo "::warning title=Scan::Completed with warnings only."
      ;;
    *) exit "${RESULT}" ;;
  esac
```

Never use bare `set -e` here; it exits before the branch is reached.

## 10. `artifacts: reports: dotenv` does not exist

GitLab propagates variables between jobs via a dotenv artifact. GitHub has no equivalent:
use `$GITHUB_OUTPUT` and declare job `outputs:`, then workflow-level `outputs:` to expose
them to the caller.

A frequent porting bug is computing a value in `init` and **not** exposing it, forcing every
downstream workflow to recompute it. Expose everything downstream needs — an unused output
costs nothing.

## 11. Image identity: promote by digest, not by tag

A tag is mutable. If a release promotes by tag, the released bytes are not provably the
scanned bytes.

- The build job outputs `image-ref-digest` (`registry/repo@sha256:...`).
- The scan consumes it as `image-ref`.
- The promote job uses `crane mutate --tag`, which copies by digest and rewrites only the
  version label.

## 12. `container.image` accepts `vars` and `secrets`, but fails before any step

Expressions work in `container.image` and `container.credentials`. But the image is pulled
*before* the first step, so a validation step cannot catch a missing variable — the run
fails with an unhelpful reference like `registry.contoso.com/`.

Mitigation is documentation, not code: link required-variable configuration prominently from the README and
include relevant changes in the MIGRATION guide. Do not add a default to paper over it (SKILL §1.4).

## 13. A static cache key silently serves stale dependencies

`key: py-cache` never invalidates. Always hash the lockfile:

```yaml
key: py-${{ hashFiles('uv.lock') }}
restore-keys: py-
```

Resolve dependencies **once** in a `dependency` job with `cache/save`; every other job uses
`cache/restore` only.

## 14. `fail-fast: true` is the matrix default

The default cancels sibling legs on the first failure, so a lint run reports one problem
instead of all of them. Set `fail-fast: false` on every matrix in a verification workflow.

## 15. A reusable workflow cannot raise the caller's token permissions

`permissions:` inside a reusable workflow is a ceiling request, not a grant. The job runs
with the **caller's** token. If the library declares `packages: write` and the caller
declares only `contents: read`, the push fails at runtime with an error that names neither
workflow file.

Symptoms: a registry push, release creation or Check publication that works when the library
tests itself and fails for a consumer, with a 403 that looks like a credential problem.

**Rule:** document the caller-side `permissions:` block next to every Quick Start and
integration example. See `library-conventions.md` §3.

Related: the repository's *default* `GITHUB_TOKEN` scope (Settings → Actions → Workflow
permissions) caps everything. An organisation set to "read repository contents" silently
denies `contents: write`, so `release.yml` cannot tag.

## 16. `contents: write` is not what artifact upload needs

`actions/upload-artifact` works under `contents: read`. Only *cross-run* download
(`gh run download <other-run-id>`) needs `actions: read`, and only tagging or releasing
needs `contents: write`.

A publish job that declares `contents: write` merely to upload an artifact is
over-privileged, and the declaration reads as if it writes to the repository when it does
not. `verify-github-library.py` flags this.
