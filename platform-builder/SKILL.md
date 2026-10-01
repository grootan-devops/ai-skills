---
name: platform-builder
description: >-
  Create, update, and audit GitHub Actions or GitLab CI pipelines, packaging
  Dockerfiles, and Helm consumer charts against the selected CI and Helm
  libraries. Use for repository onboarding, library migrations, deployment
  wiring, and platform compliance reviews — and for any change to a Dockerfile,
  chart, chart README or CI file, including one made while deploying.
---

# Platform Builder

## Route and discover

| Request | Action |
| --- | --- |
| `platform onboard [repo]` | Inspect the repository and add only the CI, image, and chart capabilities it needs. |
| `platform update [repo]` | Apply a surgical migration against selected library versions. |
| `platform ship [repo] <env>` | Prepare environment deployment wiring locally. |
| `platform audit [repo]` | Report findings without modifying the target. |

Run core/scripts/platform.py for platform signals and core/scripts/libraries.py for the
applicable library sources. An explicit platform or library source wins; when a repository
has both CI systems or neither and the target is unclear, ask. Read local library paths in
place, including uncommitted work. The resolver reports its source/ref and refuses a ref
on a local path. Stop if a required source cannot be read.
For another CI system, such as Azure DevOps, report that this Skill has no adapter rather
than generating GitHub or GitLab syntax for it.

Read each selected library's README index and only the linked topic guides needed for
the task, all from the same checkout/ref. Use its MIGRATION.md for upgrades. Executable
templates, schemas, and tests establish shipped behavior; the selected-ref docs explain
intended contracts. Record disagreements instead of silently substituting another ref.
Resolve with `python3 core/scripts/libraries.py <repo> --platform gitlab|github`.
The resolver and `audit.py` accept `--lib NAME=SOURCE` and the named flags
`--gitlab-ci-library SOURCE`, `--github-ci-library SOURCE`, and
`--helm-tpl-library SOURCE`. Resolve each library independently, highest first:

| Priority | Source |
| --- | --- |
| 1 | Named flag or `--lib NAME=SOURCE` for this invocation |
| 2 | `$PLATFORM_BUILDER_LIB_<NAME>` (uppercase, `-` becomes `_`) |
| 3 | `<repo>/.platform-builder.json` under `libraries` |
| 4 | `core/libraries.json` default |

`SOURCE` may be a local path or `file://` URL (read in place), a Git URL with an
optional `@ref` or `#ref`, or a GitHub/GitLab `/tree/<ref>` URL. A ref may be a
branch, tag, or commit. Named flags take precedence over a generic `--lib` for
the same name; the resolver is the authority for parsing and errors.

A local library checkout is evidence for this run, not a publishable consumer pin. Before
writing a GitHub reusable-workflow caller, check the selected library's pinning guard:
the current GitHub library requires a published stable release tag in normal consumers.
Branch and SHA refs need its explicit testing escape hatch and are not release-ready.
GitLab include refs and Helm chart versions follow their own selected-library contracts.
Never copy a version from a README example.

## Read the library docs

Every standard, process and starter template lives in the selected libraries, not in this
Skill. For each library, start at its README index and follow only the rows a decision needs —
pipeline setup, project jobs, Dockerfile and base images, security review, chart structure and
values, naming, mounts and configuration — all at the resolved ref. For an upgrade, read its
MIGRATION.md sections between the consumer's pin and the target. Read one CI library for a
single-platform task. The selected library's implementation remains authoritative for its
supported inputs and outputs; `platforms/<platform>/workflow-map.json` is the audit's own
scenario map.

## Working rules

- **Scope is the repository in front of you.** "All" means every file in it. Do not survey or
  edit sibling repositories unless the user names them.
- **Change only what the request covers.** A reported runtime error gets a fix for that
  service, not CI or infrastructure edits alongside it. Onboarding changes platform files
  only — CI, Dockerfile, chart, ignore files; report application-code risks instead of
  rewriting application code or scripts.
- **Never re-add what the user removed.** State the consequence of the removal instead.
- **Never drop a runtime dependency or a production capability to make an image fit.** If
  that is the only way, present it as a behaviour change and let the user decide.
- **Leave lockfiles and dependency manifests alone.** Dependency management is outside this
  Skill.
- **Project values are asked, not assumed.** Registry, pull secret, image repository path,
  `partOf`, component and subComponent come from the repository or from the user: suggest a
  value from the evidence, use it only when confirmed, otherwise use what the user says. Ask
  once per run and do not re-ask what was answered. Use the user's literal values, but fix a
  real typo (a wrong values key, an unquoted `{{ }}`) and say so.
- **Say before starting anything slow** — a long local image build, an index refresh, a
  background watcher.

## Make the change

Inspect existing CI, Docker, chart, environment, and test assets before writing. For
onboard, classify the repository shape, choose only executable workflows and modules,
and preserve discovered ports, settings, mounts, and runtime behavior. Take the chart
description from the project manifest or README, and ask when it is missing or generic. Ask
whether the application reads a configuration file; declare it as a file mount. Jobs, cronjobs,
persistence, metrics and `global.tracing` are optional: add each only when the workload
needs it, pair it with its tpl entrypoint and schema property, and otherwise leave it out of
`values.yaml` and `values.schema.json`.

The image the chart deploys must be the image CI pushes: set the registry, pull secret and
`image.repository` to the CI push path (ask when unknown). Choose the base image as the CI
library's docs describe, and ask for the registry before building a project base image. Fix runtime
permission problems (a cache, lock or pid path) with a chart mount, not in the image.
Comments in CI files are one line that says why something differs from the library default.

AI/agent tooling in the target repository (`.claude/`, `.agents/`, `.codex/`, `.gemini/`,
`.cursor/`, `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, `GEMINI.md`, `skills-lock.json`, nested
copies included) must never be committed. Warn about what is present — the audit lists it —
and ask the user whether to delete it. Delete nothing on your own.

### Phase 2b: interactive choices before scaffolding

Before scaffolding a new image or chart, show the detected platform, repository
shape, harvested settings, selected library provenance, and proposed files. Ask
the relevant questions for each artifact being added. An existing runnable
fixture is evidence for enabling its test; state that proposed default when asking.
An explicit user request or clear existing workload configuration settles an
optional resource choice without asking it again.

| Present | Ask | If enabled |
| --- | --- | --- |
| Chart | Product (`partOf`), component, subComponent — suggest each from the evidence | Use the confirmed values; subComponent may stay empty for a single-mode chart and is required per mode for a multi-mode one. |
| Dockerfile or chart | Image registry, pull secret and repository path, when the repository does not already state them | Set them so the chart pulls exactly what CI pushes. |
| Chart with routes | Route host and paths — suggest the library's pattern | Use what the user chooses. |
| Chart | Does the application read a configuration file? | Declare it as a file mount, following the Helm library docs. |
| Dockerfile | Smoke-test the built image? | Wire the GitHub `docker.yml`/`buildah.yml` `test: true` and `test-script` (default `ci_image_test.sh`), or GitLab `.Image:Test`. |
| Chart | Unit-test the chart? | Wire GitHub `chart.yml` `run-unittest: true` with `tests/*_test.yaml` suites, or the selected GitLab chart test job. |
| Chart | Does the workload require persistent storage (PVC)? | Add `persistence:` and `tpl.pvc`; otherwise omit both. |
| Chart | Does the workload require batch jobs, recurring cronjobs, or Prometheus metrics scraping? | Add only required values and matching `tpl.job`, `tpl.cronjob`, or `tpl.servicemonitor` entrypoints. |

If a runnable smoke script or chart test suite already exists, preserve and wire
it by default. If the user enables a new test, create a fixture that checks the
application's actual contract. Do not declare a test job without a runnable
fixture. Questions about optional chart resources are unnecessary when the
workload evidence or explicit request already answers them.

For update, resolve each target library and run `core/scripts/audit.py` against the
consumer with the same sources: its `migrations[]` output lists the release sections between
the consumer's pin and the target. Work through every intermediate release in order (a branch
or SHA pin may not give a complete chain; report that) and compare each step with the actual
templates, schema and consumer files. Change only required refs, schemas, wiring, and
verified defects. Preserve intentional cache keys, custom jobs, values, annotations, probes,
mounts and formatting; present a customization that conflicts with a required step for a
decision. An update edits existing files; never reset them to starter templates.

For ship, prepare wiring for the requested environment and validate it. Do not infer
a production target, credential, or cluster. A local change does not authorize a
deployment, commit, or push.

For audit, run the engine, then review what it cannot establish against the libraries'
security guides and the chart security posture: secrets by value, token scope, rendered
chart grants, untrusted input paths, and unsupported library options. Tag each finding
`[engine]` or `[judged]` with file evidence; a judged finding says what you saw and why it
concerns you, and is never presented as the engine's. Do not edit the audited target.

```text
[P0] [judged]  Embedded credential   chart/values.yaml:42
     -> DATABASE_URL value carries user:password@ before the host. Move it to the secret store.
```

## Validate and report

Run core/scripts/audit.py with --strict and the same local paths or refs used for the
change. When a chart changed, run `core/scripts/chart-lint.sh <chart>` (lint and render
`values.yaml` alone and with each overlay), regenerate the README with
`core/scripts/chart-docs.sh <chart>`, and run `core/scripts/verify_siblings.py` across the
product's charts after a rename or a mode split; `core/scripts/names.py` prints every name a
release derives. Run the selected platform's native lint or CI checks when a pipeline
changed. Report exact library provenance, commands and outcomes, remaining risks, and any
publishing prerequisite. Do not claim a local library change is present in a remote
workflow or chart release.
