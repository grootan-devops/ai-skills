# GitLab Job Anatomy — the eight decisions, in order

Every job in `gitlab-ci-library` is the answer to the same eight questions. Answer them in
this order: a later answer never changes an earlier one, and skipping one is how a job ends up
in the wrong file with the wrong image.

---

## 1. Does this job belong in the library at all?

A job earns its place only if the library can make the decision *for* every consumer. If the
answer depends on the repository — which tests, which build command — the library supplies a
hidden template and the consumer supplies the `script:`.

If the platform already provides the outcome another way, the correct job is no job. Deleting
a candidate is a better outcome than adding it; record the reasoning, never the job.

## 2. Hidden (`.Job:`) or concrete (`Job:`)?

This is the decision most often got wrong, and the library is consistent about it:

| Kind | Spelling | Consumer writes | Used when |
| --- | --- | --- | --- |
| Hidden template | `.Node:Dependency:Download` | `extends:` + maybe `script:` | the consumer must choose something — a runtime, a build command, a test command |
| Concrete job | `Chart:Lint` | nothing; `include:` is enough | the library decides everything; the job is identical in every repo |

Evidence from the library: `java/` and `nodejs/` are almost entirely hidden (the consumer picks
the runtime anchor and the script); `chart/`, `sbom/`, `license/`, `secret-scanning/`,
`sonarqube/` and `release/` are almost entirely concrete (nothing to choose).

Two rules that follow:

- **A hidden template is never named for a stage it does not own.** `.Node:Build` is a base for
  `Project:Build`, not a job.
- **Concrete jobs in the shared library carry no language segment**, except the existing
  `Go:Dependency:Download`, whose name is part of the library's own `.Go` and
  `.go-lint-common` `needs:` wiring. This rule applies to library-owned concrete jobs; do not
  apply it to consumer wrappers. A consuming GitLab project defines stack-scoped jobs such as
  `Python:Dependency:Download`, `Java:Dependency:Download`, or `Node:Dependency:Download`,
  extending the matching runtime anchor first and hidden dependency template second. Do not
  replace those wrappers with a generic `Dependency:Download`.

## 3. Which stage?

`stages:` in `common/.gitlab-ci.yml` is the closed list, in order:

```text
.pre  init  prepare  lint  check  test  build  push  security  qa  report
deploy  release  notify  trigger  destroy  .post
```

Pick by **what the job asserts**, never by when it happens to be convenient:

| Stage | Admits |
| --- | --- |
| `init` | computing facts every later job reads (`Common:Init`) |
| `prepare` | warming caches, downloading dependencies |
| `lint` | linters and formatters — nothing that needs dependencies installed |
| `check` | existence and drift assertions that must fail in seconds (tag taken, chart published, docs stale) |
| `test` | unit tests |
| `build` | producing an artifact (`dist/`, image tar, packaged chart) |
| `push` | publishing that artifact |
| `security` | scanning what was built or pushed |
| `qa` | quality gates over the whole repo (SonarQube) |
| `release` | tagging, release notes, package registry |
| `deploy` | GitOps delivery |

A job doing two of these is two jobs. One failing check must cost one job, and the run graph
must name it.

## 4. Which image?

**Default first.** `default.image` is the toolkit build image; it already carries `git`, `helm`,
`yq`, `jq`, `curl`, `trivy`, `cyclonedx`, `hadolint`, `betterleaks` and `biome`. A job that needs
only these declares no `image:` at all.

Declare an image **only** when the job needs a runtime or CLI the toolkit lacks:

| Need | Where the image lives |
| --- | --- |
| Node, Python, Go, Java runtime | the runtime anchor — `.Node:24`, `.Python:12`, `.Go`, `.Java:25` |
| `buildah` | `.buildah` anchor in `image/.buildah.gitlab-ci.yml` |
| `sonar-scanner` | the `Sonarqube` job's own `image:` |
| a Docker daemon | `services:` with the dind image, not `image:` |

**`image:` belongs to an anchor, never to a concrete job, and never to a consumer.** Grep the
library: every `image:` is on an anchor or on `default:`. If a job fails because of the image,
fix the anchor or the base image — a consumer-side or job-side override is a fork by another
name, and it outlives the fix by years.

Before adding an image, check the toolkit actually lacks the tool. A second image costs a pull
on every run.

## 5. `needs:` — and fail-fast

Every job declares its dependencies explicitly. Stage order alone is not wiring.

```yaml
needs:
  - job: Common:Init
    artifacts: true
    optional: true
```

- **`optional:` is mandatory on every entry and is a real decision.** `true` where the upstream
  is gated out of some workflows; `false` where the artifact is genuinely required. Omitting it
  defaults to `false` and fails pipeline creation the moment that upstream is gated out.
- **`optional: true` is about composition, not failure tolerance.** It means *"this job may not
  be in this pipeline"*. When the job is present and fails, the dependent still does not run.
- **`artifacts:` is `false` unless the job actually opens the payload.** Needing an upstream's
  *success* is not needing its files.
- **Nothing runs once its input is known bad.** Audit both directions: a missing edge (work that
  runs when it cannot succeed) and a spurious edge (work that waits for something it never reads).
  A ten-second job blocked behind a whole stage is a defect.
- **Place a gate where it stops waste earliest without serialising work that could overlap.**
  Gating the build on unit tests costs the test duration on every run and stops nothing
  shippable; gating the push costs nothing and still lets nothing ship untested.

## 6. `rules:` — never unconditional

A job with no `rules:` runs in every pipeline, including ones where it is meaningless. Extend
one of the existing anchors in `common/.gitlab-ci.yml` rather than writing a new condition:

```text
.build-test-rules  .lint-workflow-rules  .unit-test-workflow-rules  .common-init-rules
.chart-*-rules     .image-*-rules        .release-rules             .deploy-workflow-rules
.sbom-scan-workflow-rules  .secret-scan-workflow-rules  .license-scan-workflow-rules
.sonarqube-workflow-rules  .terraform-*-rules  .trivy-cache-rules  .lifecycle-rules
```

Every anchor ends `- when: never`. A new job that needs a genuinely new condition adds a new
anchor beside these — it does not inline `rules:` and it does not copy an existing block.

If the job belongs to a `WORKFLOW` option, the option must appear in the anchor's
`$WORKFLOW == "..."` list, or the option is a dead button.

## 7. `cache:` and `artifacts:` — different jobs, different halves

**Cache** is for dependency trees that the next job re-reads:

```yaml
cache:
  key: ${PROJECT_CACHE_KEY}
  when: always
  policy: pull                     # pull-push ONLY in the job that warms it
  paths: [${PROJECT_PATH}/.npm/]
```

In the GitLab library, the dependency-download job warms this key (`pull-push`) and jobs that
consume the cache, including `Image:Build`, restore the **same** key and cache path (`pull`).
Do not add `cache:key:files` or a lockfile digest to only one side of that handoff; the
language module's `PROJECT_CACHE_KEY` is the cache identity.

One job warms (`policy: pull-push`), every other job reads (`policy: pull`). Two warmers means
two writers racing for one key.

**Artifacts** are for outputs a later job opens:

```yaml
artifacts:
  name: Image Tar
  expose_as: Image Tar
  when: always                     # `always` when the report is worth reading on failure
  expire_in: 1 week                # always set; the default retention is not a decision
  paths: [${PROJECT_PATH}/${IMAGE_TAR_FILE_NAME}]
```

- A report a human reads on failure: `when: always`, short `expire_in`.
- A build output a later job consumes: default `when`, `expire_in` long enough for the pipeline.
- Never ship a cache as an artifact, or an artifact as a cache — the handoff between build and
  package is the cache; the handoff between build and publish is the artifact.

## 8. Which file?

| The job is… | It goes in |
| --- | --- |
| language-specific | `nodejs/`, `python/`, `golang/`, `java/` |
| about the container image | `image/.gitlab-ci.yml`, or `.docker`/`.buildah` for a builder |
| about the Helm chart | `chart/.gitlab-ci.yml` |
| a security or compliance scan | `sbom/`, `license/`, `secret-scanning/` |
| shared by every pipeline (anchors, rules, variables) | `common/.gitlab-ci.yml` |
| delivery | `deploy/gitops/.argocd…` or `.komodo…` |

**Create a new module file only when a consumer would want the jobs without the rest of an
existing one** — that is the whole meaning of the `include:` list. A new file means a new
`include:` entry, a new `WORKFLOW` option if it is user-triggerable, README and CHANGELOG
entries, and a MIGRATION note if it changes an existing pipeline. If none of that is warranted,
the job belongs in an existing file.

---

## Shell rules inside `script:`

- **Never capture `$?` on its own script line.** GitLab echoes each item before running it, and
  that echo resets `$?`. `EXIT=$?` on its own line is always `0`, and the gate silently never
  fails. Put the command, the capture and the exit in one `- |` block.
- Prefer `set -e` semantics; where a non-zero exit is captured deliberately, capture it in the
  same block that produced it.
- A job that writes a report file also `cat`s it, so the log is readable without downloading an
  artifact.
