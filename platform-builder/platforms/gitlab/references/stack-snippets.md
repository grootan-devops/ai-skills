# Stack Snippets — GitLab CI

YAML for the requirements in
[`core/references/language-stacks-core.md`](../../../core/references/language-stacks-core.md).
Read that first — the *why* lives there, and is not repeated here.

Most of the work is done by the shared template library: a consumer `.gitlab-ci.yml` declares
`extends:` against library job templates rather than writing steps. Keep the project file to the
minimum footprint.

---

## The include set — start complete, subtract with a reason

The common failure is the *opposite* of over-declaring: an include list carrying only
`common`, the language module, `image/*` and `chart/` looks tidy and silently gives up
SonarQube, secret scanning, licence compliance, SBOM and releases. A service repository
starts from the getting-started guide linked from the library README and removes only what it can justify:

> [!IMPORTANT]
> `project:` is the library's path **on the GitLab instance that runs this pipeline**, not
> the repository it is developed in. GitLab's `include: project:` resolves only within its
> own instance, so a GitHub URL there fails. `ref:` is the version this run resolved — read
> it from the `name @ ref` line `core/scripts/libraries.py` prints, never invent one. On an
> update it becomes the newest release tag, and the `MIGRATION.md` sections between the old
> ref and it are the work.
>
> [!IMPORTANT]
> The ref must be a published tag. `Common:Check:Library:Pin` runs in the `check` stage for
> every consumer and fails a branch, a commit or a pre-release — for `project:`/`ref:` and
> equally for a `remote:` raw URL, where the ref is a path segment
> (`…/gitlab-ci-library/<ref>/<file>`). `ALLOW_UNSTABLE_LIBRARY_REFS: "true"` downgrades it
> to a warning for testing only; never scaffold it.
>
```yaml
include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/common/.gitlab-ci.yml'  # required by every pipeline
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/nodejs/.gitlab-ci.yml'  # or python/ golang/ java/
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/image/.docker.gitlab-ci.yml'  # builder (or .buildah)
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/image/.gitlab-ci.yml'  # image lifecycle
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/chart/.gitlab-ci.yml'  # chart lifecycle
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/sonarqube/.gitlab-ci.yml'  # needs sonar.properties in the repo
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/secret-scanning/.gitlab-ci.yml'  # git history; applies to every repo
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/license/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/sbom/.gitlab-ci.yml'
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/<resolved library ref>/release/.gitlab-ci.yml'
```

For an **image-only** repository (a Dockerfile with no application dependency manifest), include
only `common`, the selected image builder, the image lifecycle, secret scanning, and release
modules. Do not include language, SonarQube, licence, or SBOM modules: there is no source
dependency graph for them to inspect. The image vulnerability scan remains required.

Two that are **not** defaults, because they are conditional on the repo:

| Module | Add when | Do not add when |
| --- | --- | --- |
| `readme/.migration-guide.gitlab.yml` | the repo publishes a versioned contract others upgrade against | it is an application nobody pins — `Migration:Check Existence` is `allow_failure: false`, so it blocks every release for a `MIGRATION.md` no one reads |
| `deploy/gitops/.komodo` / `.argocd` | `platform ship` has real target inputs | the GitOps repo, branch and stack/app are still unknown — see `workflow-map.json` |

`WORKFLOW.options` must then match this list exactly (§3.1). Adding a module without
adding its option hides working jobs; the reverse produces a dead button.

**Module prerequisites** — a module whose repo-level file is missing fails at runtime, not
at pipeline creation, so the engine cannot catch it and you must:

| Module | Requires in the repo |
| --- | --- |
| `sonarqube/` | `sonar.properties` (the job passes `-Dproject.settings=sonar.properties`), plus `SONARQUBE_TOKEN` and `SONAR_URL` |
| `readme/.migration-guide.gitlab.yml` | `MIGRATION.md` with a `## [<prev>...<curr>]` heading |
| `license/`, `sbom/`, `image/` | `ignored-cves.yml` if any CVE or licence is waived |

---

## The four jobs

```yaml
Project:Version:Init:
  extends: .Node:Project:Version:Init   # version from package.json, not a hardcoded literal

Node:Dependency:Download:
  extends:
    - .Node:24                  # runtime anchor FIRST -- see below
    - .Node:Dependency:Download

Project:Build:
  extends:
    - .Node:24
    - .Node:Build               # or .Python:Build, .Go:Build, .Java:Build
  needs:
    - job: Common:Init
      artifacts: true
      optional: true            # gated out of scan-only workflows
    - job: Node:Dependency:Download
      artifacts: false          # dependencies are shared through Runner Cache, not artifacts
      optional: false           # the cache warmer is genuinely required

Project:Unit:Test:
  extends:
    - .Node:24
    - .Node:Test:Unit
  needs:
    - job: Project:Build
      artifacts: true
      optional: false
```

Use the same consumer-wrapper pattern for Python and Java:

```yaml
Python:Dependency:Download:
  extends:
    - .Python:12
    - .Python:Dependency:Download

Java:Dependency:Download:
  extends:
    - .Java:25
    - .Java:Dependency:Download
```

Go is different: `Go:Dependency:Download` is already a concrete library job, so consumers do
not define another wrapper for it.

### Extends order: runtime anchor first

`.Node:24` / `.Python:12` / `.Go` set `image:` and the cache; the job template sets stage,
rules, `needs:` and artifacts. List the **runtime anchor first and the job template second**,
matching every consumer example linked from the library README. YAML `extends:` merges left to right
with later entries winning, so the reversed order lets the runtime anchor overwrite keys the
job template meant to own.

### `PROJECT_CACHE_KEY` is shared by the project/stack cache consumers

```yaml
variables:
  PROJECT_CACHE_KEY: "node"  # optionally add a monorepo scope, e.g. node-admin
```

Set a nonempty stack key (`node`, `python`, `go`, `java`, or `terraform`) or a project/service
identifier (such as `chat`). For monorepos, append a service scope when independent caches are
needed, such as `node-admin`. Use the exact same value on the dependency-download job and every
cache reader, including `Image:Build`. The key is stable across lockfile changes; the package
manager follows the committed lockfile when warming the cache. Do not use `cache:key:files` for
this GitLab cache handoff. Never revert or overwrite a custom `PROJECT_CACHE_KEY` configured by
the project team.

### Never override `image:` in a consumer job

```yaml
# WRONG -- a library defect copied into every consumer
Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download
  image:
    name: "${NODE_JS_24_MICRO_BASE_IMAGE_REPO}:${NODE_JS_24_MICRO_BASE_IMAGE_TAG}"
    entrypoint: [""]
```

```yaml
# RIGHT -- the runtime anchor owns the image
Node:Dependency:Download:
  extends:
    - .Node:24
    - .Node:Dependency:Download
```

The runtime anchor (`.Node:24`, `.Python:12`, `.Go`) owns `image:`. A consumer that
re-declares it has taken ownership of a decision the library exists to make, and now
pins an image the library can no longer move — which is the whole point of the anchor.

**When a job fails because of the image, fix the library or the base image. Never the
consumer.** A consumer-side workaround is a fork by another name (§0.2 rule 6): it works,
so nobody reports the defect, and every new repo copies it from the last one. The override
outlives the fix by years.

The canonical example is the empty-entrypoint workaround above. The micro base images
declare an `ENTRYPOINT` that is a directory, so a job started from the **string** form
dies before its first script line:

```bash
sh: line 1: /usr/local/node/bin: Is a directory
ERROR: Job failed: exit code 126
```

The fix is one line in the anchor — declare `image:` in the map form with
`entrypoint: [""]`, which `.Python:12` currently does. Consumers then need
nothing. If you meet `exit code 126` with no script output, check whether the anchor for
that stack uses the string form, and fix it there.

**Diagnosing an image problem is a legitimate reason to try an override locally. Shipping
one is not.** Confirm the cause, fix the library, and leave the consumer clean.

### Declare nothing the template already gives you

Re-stating an inherited key is not harmless: it is a second copy that stops tracking the
library when the library changes. In particular do **not** re-declare:

- `NODE_ENV: test` on `Project:Unit:Test`, `cache: policy: pull`, or the template's `needs:`.
(`PROJECT_CACHE_KEY` is the exception that is always declared — see below.)

> **`optional:` is mandatory on every `needs:` entry** and is a real decision, never a default.
> `optional: true` where the upstream is gated out of some workflows; `optional: false` where
> the artifact is genuinely required. Omitting it defaults to `false`, which fails pipeline
> creation with *"job needs a job that is not in the pipeline"* the moment that upstream is
> gated out — the exact failure the Go and Python lint anchors used to produce on a `lint` run.

### Overriding DAG `needs:` in multi-test and polyglot pipelines (SonarQube & Image:Build)

The library templates define default DAG dependencies designed for canonical single-stack repositories:

- `Sonarqube` defaults to `needs: [Common:Init, Project:Build, Project:Unit:Test]`.
- `Image:Build` defaults to `needs: [Common:Init, Project:Build, Node:Dependency:Download, Python:Dependency:Download]`.

When a repository deviates from this single-job layout — such as:

1. **Multiple or Split Test Jobs:** e.g., a full-stack project or monorepo with `Project:Unit:Test:Frontend` and `Project:Unit:Test:Backend` (or `Project:Unit:Test:Node` and `Project:Unit:Test:Python`).
2. **Multiple or Custom Build Jobs:** e.g., `Project:Build:Frontend` and `Project:Build:Backend`, or a custom bundling step.
3. **Interpreted Stacks without `Project:Build`:** e.g., Python services where no compilation step exists, or custom frontend asset builds.

**The Failure Mode:**

Because the library marks `Project:Unit:Test` and `Project:Build` as `optional: true`, GitLab CI's DAG scheduler does **not** wait for `Project:Unit:Test:Frontend` or `Project:Unit:Test:Backend`. It treats the missing canonical job as simply absent, and schedules `Sonarqube` or `Image:Build` **immediately** after `Common:Init`!

- `Sonarqube` executes before unit tests finish, completely missing JUnit XML results and test coverage reports (`0% coverage`), and wasting runner compute if tests fail.
- `Image:Build` executes before frontend build assets (`dist/`) are generated, causing Dockerfile `COPY dist/ ...` to fail with missing files.

**The Solution:**

Override `Sonarqube.needs` and `Image:Build.needs` at the project level in `.gitlab-ci.yml`:

```yaml
# When multiple test jobs exist, Sonarqube MUST depend on all of them
Sonarqube:
  needs:
    - job: Common:Init
      artifacts: true
      optional: true
    - job: Project:Build:Frontend
      artifacts: true
      optional: true
    - job: Project:Unit:Test:Frontend
      artifacts: true
      optional: true
    - job: Project:Unit:Test:Backend
      artifacts: true
      optional: true

# When custom build jobs exist, Image:Build MUST depend on the jobs providing its assets
Image:Build:
  needs:
    - job: Common:Init
      artifacts: true
      optional: true
    - job: Project:Build:Frontend
      artifacts: true
      optional: false  # genuinely required if dist/ is copied into the Docker image
    - job: Python:Dependency:Download
      artifacts: false # dependencies are shared through runner cache, not artifacts
      optional: true
```

> [!IMPORTANT]

> - Always specify `artifacts: true` on jobs that produce reports or compiled bundles (`junit.xml`, coverage reports, `dist/`).
> - Keep `artifacts: false` on dependency download jobs (which share caches, not artifacts).
> - Set `optional: true` on upstream jobs that may be excluded when triggering isolated workflows (e.g. `WORKFLOW: "sonarqube"`).

## `USE_DOCKER_BUILDX` — not a default, and rarely the answer

`common/.gitlab-ci.yml` sets `USE_DOCKER_BUILDX: "false"`. **Leave it.** Do not set it to
enable BuildKit syntax, which is the usual reason people reach for it — that reason is
already satisfied without it.

### It is not what enables `RUN --mount`

`.image-common` already exports `DOCKER_BUILDKIT: 1`, so the plain `docker build` branch
runs under BuildKit and `RUN --mount=type=bind,...` / `type=cache` work there. A comment
like *"the Dockerfile bind-mounts the cache, which requires BuildKit"* is true about
BuildKit and wrong about this flag. Before setting it, confirm the build actually fails
without it.

### What it does change

```bash
docker buildx create --use --driver docker-container --name buildkit-builder \
  --driver-opt image="${BUILDKIT_IMAGE_REPO}:${BUILDKIT_IMAGE_TAG}"
docker buildx build ... --load \
  --cache-from="type=registry,ref=${IMAGE_REGISTRY}/${IMAGE_PUSH_REPOSITORY}:buildcache-${CI_COMMIT_REF_SLUG}" \
  --cache-from="type=registry,ref=${IMAGE_REGISTRY}/${IMAGE_PUSH_REPOSITORY}:buildcache-${CI_DEFAULT_BRANCH}" \
  --cache-to="type=registry,ref=${IMAGE_REGISTRY}/${IMAGE_PUSH_REPOSITORY}:buildcache-${CI_COMMIT_REF_SLUG},mode=max"
```

Three consequences, none of them local to the job:

1. **It writes to your image repository.** `cache-to … mode=max` pushes a
   `buildcache-<branch-slug>` tag **per branch**, containing every intermediate layer. The
   repository accumulates cache tags nobody prunes, and a build job that previously only
   needed pull access now needs push.
2. **It adds a dependency before your Dockerfile is read.** A `moby/buildkit` container is
   pulled through the Dependency Proxy per job. If that image is unavailable the build
   fails having never looked at your code.
3. **`--load` materialises the image into the daemon** so the following `docker save` can
   run — extra time and disk that grows with image size.

`mode=max` also means intermediate layers leave the runner. Anything a build argument put
into a layer is in a registry tag that outlives the job, so never pair this with a secret
passed as `ARG`.

### If you do enable it, four things must move together

Because the flag and the Dockerfile are coupled, flipping one without the other breaks the
build in a way the error does not explain:

| Requirement | Why |
| --- | --- |
| The Dockerfile genuinely needs the `docker-container` driver | otherwise `DOCKER_BUILDKIT: 1` was already enough |
| `.dockerignore` **admits** every path a `--mount=type=bind` reads (e.g. `!.npm`, `!.npm/**`) | the mount source comes from the build context; denied means an empty mount, not an error |
| `Image:Build` restores whatever cache the mount expects | `Image:Build` is not a `.Node:24` job, so it carries no language cache of its own — give it a `cache:` block with the same `${PROJECT_CACHE_KEY}` and cache path as the dependency job |
| The registry credentials allow **push** from the build job | `cache-to` writes, and a failure there can fail the build after a successful image |

Comment the flag with the specific thing that requires it, not "for BuildKit" — the next
person needs to know what breaks if they remove it.

### Prefer no in-image install at all

A bind-mounted package cache exists to make an in-image install fast and offline. Where a
stack produces real build output, the stronger move is to have nothing to install in the
image: `Project:Build` artifacts `dist/` (or the jar, or the binary), the Dockerfile
`COPY`s it, and the image resolves nothing. That needs no BuildKit, no builder container,
no registry cache tags and no `.dockerignore` coupling.

Reach for `USE_DOCKER_BUILDX` only when a build genuinely cannot be expressed either way.

## Known library defects to check before blaming the consumer

Two failures that look like consumer mistakes and are not. Confirm against the library
at its pinned ref before editing a project file:

| Symptom | Cause | Consumer action |
| --- | --- | --- |
| `sh: line 1: <path>: Is a directory` / `exit code 126`, no script output | the stack's runtime anchor declares `image:` in string form, leaving the micro image's `ENTRYPOINT` in place | **fix the anchor** — map form with `entrypoint: [""]`, as `.Python:12` does. Never override `image:` in the consumer |
| `betterleaks: command not found` | the tool is absent from the `bt-container` tag the library pins | none — drop `secret-scanning/` or get the image fixed |

The shape is the same in both: **the job fails before any project-specific logic runs.** When a failure happens that early, suspect the library or its build image, and
reproduce locally before changing the repo. A consumer workaround for a library defect is
a fork by another name (§0.2 rule 6), and it outlives the fix.

## Packaging handoff: the cache is the handoff, never an artifact

`language-stacks-core.md` §3 says the image build must not resolve dependencies over the
network. On GitLab the mechanism for that is the **package-manager cache**, bind-mounted
into the image build by BuildKit — *Pattern A, cache-only*, which the CI library's README-linked Docker guide
documents in full for Python and which every stack follows.

> [!IMPORTANT]
> **Never publish a dependency directory as an artifact.** Not `node_modules/`, not
> `.venv/`, not `vendor/`. The library says so in its own wiring: every `needs:` in
> `nodejs/`, `python/` and `golang/` uses `artifacts: false`, and the cache blocks carry
> `cache: policy: pull`. Artifacting the tree uploads tens of thousands of
> files to coordinator storage on every run to deliver what the cache already holds, and
> the runner then has to download it again. `check_dependency_artifacts` flags it.

Two pieces are required. Both are about the **image build** — neither is a separate
install job:

**1. `Image:Build` restores the cache into its workspace.** It is not a `.Node:24` job, so
it inherits no cache of its own. Give it the same key as `Node:Dependency:Download` and
the path the Dockerfile bind-mounts:

```yaml
Image:Build:
  cache:
    key: ${PROJECT_CACHE_KEY}
    when: always
    policy: pull
    paths:
      - .npm/
```

**2. The Dockerfile installs offline from the bind-mounted cache:**

```dockerfile
COPY package.json package-lock.json ./

RUN --mount=type=bind,source=.npm,target=/tmp/.npm,rw \
    npm ci --omit=dev --offline --no-audit --no-fund --cache /tmp/.npm
```

`--offline` is not optional decoration: without it the install silently falls through to
the network and resolves something the pipeline never scanned, which is the failure the
whole pattern exists to prevent. The cache is mounted, never `COPY`d, so it contributes
zero layer bytes. `.dockerignore` must admit `.npm`, not `node_modules` —
`ignore-files-standard.md` §2.

`RUN --mount` needs BuildKit, which `.image-common` already provides via
`DOCKER_BUILDKIT: 1`. It does **not** need `USE_DOCKER_BUILDX` — see that section below
before setting it.

The Python form uses `Python:Dependency:Download`, an `Image:Build` cache with the same
`PROJECT_CACHE_KEY` and `.uv` path, and:

```dockerfile
ARG PYTHON_312_MICRO_BASE_IMAGE=grootantech/python-3-12:latest
FROM ${PYTHON_312_MICRO_BASE_IMAGE}

USER 0

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

RUN --mount=type=bind,source=.uv,target=/tmp/.uv,rw \
    uv sync --frozen --no-dev --no-install-project --no-install-workspace --offline --cache-dir /tmp/.uv && \
    chown -R 10001:10001 /app

COPY --chown=10001:10001 app/ /app/app/
COPY --chown=10001:10001 main.py config.py /app/

USER 10001:10001

EXPOSE 3000

CMD ["python", "main.py"]
```

For this layout, the `.dockerignore` allowlist must admit `pyproject.toml`, `uv.lock`,
`.uv/**`, `app/**`, `main.py`, and `config.py`; adjust the application paths for the actual
repository. The cache mount must remain read-write (`rw`).

### There is no install job — `Project:Build` is optional

**Do not confuse the removed `Project:Install` with dependency warming.** The
stack-scoped `Node:Dependency:Download`, `Python:Dependency:Download`, and
`Java:Dependency:Download` consumer jobs warm the cache and remain in these pipelines.
The old `.Node:Install` and `.Python:Install` templates redundantly repeated the production
install already done by the Dockerfile. Do not add a separate `Project:Install` job; use the
language-scoped dependency download job for cache preparation.

**`Project:Build`** (`.Node:Build`, `.Python:Build`, …) is a skeleton with **no** script —
stage, rules, cache and `needs` only — for a repo with a genuine compile or bundle step.
The consumer supplies the script and the artifact:

```yaml
Project:Build:
  extends:
    - .Node:24
    - .Node:Build
  script:
    - npm ci --include=dev --offline --no-audit --no-fund
    - npm run build
  artifacts:
    paths:
      - dist/
```

Declare it only when something is actually built. A plain-JavaScript service with no
`build` script in `package.json` has no build step to skeleton, so it declares neither
`Project:Build` nor a unit-test job. `Node:Dependency:Download` still warms the cache, and
`Image:Build` consumes it. The engine
now applies that same test — it asks for `Project:Build` only where a build script exists.

## Per-stack overrides

Only `Project:Build` and `Project:Unit:Test` may override `script:`. Shared library jobs
(`Node:Dependency:Download`, `Python:Dependency:Download`, `Java:Dependency:Download`,
`Chart:*`, `Release:*`, scanners) must not be overridden.

```yaml
# Python: no Project:Build at all for a containerised service
Project:Unit:Test:
  extends: .Python:Test:Unit
  script:
    - uv run pytest --cov --junitxml=junit.xml

# Go
Project:Unit:Test:
  extends: .Go:Test:Unit
  script:
    - go test -race -coverprofile=coverage.out ./...

# Java
Project:Build:
  extends: .Java:Build
  script:
    - mvn -B package -DskipTests
```

## Chart-only repository

```yaml
variables:
  CHART_DIR: '.'                # Chart.yaml at the repo root
  WORKFLOW:
    value: "full-pipeline"
    options: ["full-pipeline", "check", "lint", "secret-scanning", "chart-build-and-push"]

Trivy:Cache:Warm:
  rules:
    - when: never               # nothing here consumes the Trivy DB
```

## Script formatting

Single-line list items unless the logic is genuinely multi-line, in which case use `- |`:

```yaml
script:
  - echo "Checking ${RELEASE_VERSION} in ${CHANGELOG_FILE_NAME}..."
  - |
    if [[ ! -f ${CHANGELOG_FILE_NAME} ]]; then
      echo "${CHANGELOG_FILE_NAME} is missing in the repo"
      exit 1
    fi
```

Never wrap a lone command in a block scalar — it hides the command from a quick scan.
