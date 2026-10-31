---
name: platform-builder
description: >-
  Create, update, and audit GitHub Actions or GitLab CI pipelines, packaging
  Dockerfiles, and Helm consumer charts against the selected CI and Helm
  libraries, and the GitOps environments the projects deploy to (Argo CD on
  argocd-gitops-tpl-library; Komodo planned). Use for repository onboarding,
  library migrations, deployment wiring, platform compliance reviews, any change
  to a Dockerfile, chart, chart README or CI file (including one made while
  deploying), and for `platform env add|update|audit|clone` and `platform app
  add|promote`: bootstrapping, cloning and auditing GitOps environments, adding
  or promoting apps that meet the cluster's workload rules, and wiring each
  app's CI deploy job, automatic or manual.
---

# Platform Builder

## Check for updates first

At the start of every invocation, run `python3 core/scripts/update_check.py` from this skill's folder.

- **An update is available (exit 1):** tell the user the installed and latest release and the
  command it prints, and ask whether to update now. Run the update only on a yes, then reload
  this skill before you go on.
- **The installed release is unknown (exit 2, a copied install):** tell the user the latest
  release and the reinstall command it prints, and ask whether to reinstall now. Run it only
  on a yes, then reload this skill before you go on.
- **They decline, or it could not check (exit 2):** carry on with the installed release and
  name it in your report.

## Route and discover

| Request | Action |
| --- | --- |
| `platform onboard [repo]` | Inspect the repository and add only the CI, image, and chart capabilities it needs. |
| `platform update [repo]` | Apply a surgical migration against selected library versions. |
| `platform audit [repo]` | Report findings without modifying the target. |
| `platform env add\|update\|audit\|clone`, `platform app add\|promote` | GitOps environments and apps: see [GitOps environments and apps](#gitops-environments-and-apps). |

Run core/scripts/platform.py for platform signals and core/scripts/libraries.py for the
applicable library sources. An explicit platform or library source wins; when a repository
has both CI systems or neither and the target is unclear, ask. For another CI system, such
as Azure DevOps, report that this Skill has no adapter rather than generating GitHub or
GitLab syntax for it.

Resolve with `python3 core/scripts/libraries.py <repo> --platform gitlab|github`; `audit.py`
takes the same sources. Each library is read at one release, highest first:

| Priority | Source |
| --- | --- |
| 1 | `--<library> SOURCE` or `--lib NAME=SOURCE` for this invocation |
| 2 | `$PLATFORM_BUILDER_LIB_<NAME>` (uppercase, `-` becomes `_`) |
| 3 | `<repo>/.platform-builder.json` under `libraries` |
| 4 | the release the repository pins: its CI includes or reusable workflows, its chart's library dependency |
| 5 | the library's latest release tag |

`--latest` skips layer 4, for an upgrade. `libraries.py --help` gives the source syntax;
a local path is read in place, uncommitted work included, and a ref on it is refused. The
resolver stops at a release that ships no data file for the checks, or one with a schema
this Skill does not know, and says what to upgrade: stop with it and tell the user. Stop
too if a required source cannot be read.

Read each library's README index and only the linked topic guides the task needs, all at
the resolved release; use its MIGRATION.md for upgrades. Executable templates, schemas
and tests establish shipped behaviour; the docs explain intended contracts. Record
disagreements instead of substituting another ref. Pin exactly the release the docs were
read from, in the form that library's pinning rules give, and never copy a version from a
README example. A local library checkout is evidence for this run, not a publishable pin.

## Read the library docs

Every standard, process and starter template lives in the selected libraries, not in this
Skill. For each library, start at its README index and follow only the rows a decision needs —
pipeline setup, project jobs, Dockerfile and base images, security review, chart structure and
values, naming, mounts and configuration — all at the resolved ref. For an upgrade, read its
MIGRATION.md sections between the consumer's pin and the target. Read one CI library for a
single-platform task. The selected library's implementation remains authoritative for its
supported inputs and outputs. The audit reads the CI library's `docs/workflow-map.json` and
the Helm library's `docs/chart-contract.json` at the same release; nothing here copies them.

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
| Dockerfile | Smoke-test the built image? | Wire the image smoke test the selected CI library's image module documents. |
| Chart | Unit-test the chart? | Wire the chart unit tests the selected CI library's chart module documents, with `tests/*_test.yaml` suites. |
| Chart | Does the workload require persistent storage (PVC)? | Add `persistence:` and `tpl.pvc`; otherwise omit both. |
| Chart | Does the workload require batch jobs, recurring cronjobs, or Prometheus metrics scraping? | Add only required values and matching `tpl.job`, `tpl.cronjob`, or `tpl.servicemonitor` entrypoints. |

If a runnable smoke script or chart test suite already exists, preserve and wire
it by default. If the user enables a new test, create a fixture that checks the
application's actual contract. Do not declare a test job without a runnable
fixture. Questions about optional chart resources are unnecessary when the
workload evidence or explicit request already answers them.

For update, run `core/scripts/audit.py --latest` against the consumer: it reads every library
at its latest release, and its `migrations[]` output lists the release sections between the
consumer's pin and that release. Work through every intermediate release in order (a branch
or SHA pin may not give a complete chain; report that) and compare each step with the actual
templates, schema and consumer files. Change only required refs, schemas, wiring, and
verified defects. Preserve intentional cache keys, custom jobs, values, annotations, probes,
mounts and formatting; present a customization that conflicts with a required step for a
decision. An update edits existing files; never reset them to starter templates.

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
publishing prerequisite. An onboard that produced a chart and an image ends its report with
the next step: run the project's full pipeline once so the chart and image are published,
then `platform env add` or `platform app add <repo>`, which checks the publish and can offer
to run that pipeline. Do not claim a local library change is present in a remote workflow or
chart release.

## GitOps environments and apps

GitOps work is laid out like the CI adapters: rules shared by every GitOps engine live in
`core/`, and each engine has its own folder under `gitops/<backend>/`. Argo CD is the only
backend today.

| Folder | What it holds |
| --- | --- |
| `core/references/` | [naming](./core/references/naming.md) (labels, chart, branch, namespace, root name), [kubernetes-rules](./core/references/kubernetes-rules.md) (the workload rules every app meets), [app-readiness](./core/references/app-readiness.md) (an application project is onboarded and published), [deploy-job](./core/references/deploy-job.md) (CI deploy job wiring) |
| `core/scripts/` | `gitops_library.py` (pinned GitOps and service library packages and their data files), `naming.py`, `ci_pin.py` (an application project's CI platform and library pin), `app_facts.py` (its chart, image and pin, and whether they are published) |
| `gitops/argocd/` | `references/` ([environment](./gitops/argocd/references/environment.md), [service-bootstrap](./gitops/argocd/references/service-bootstrap.md), [discovery](./gitops/argocd/references/discovery.md)), `workflows/`, and `scripts/` (`repo_check.py`, `argocd.py`, `app_add.py`, `override_check.py`) |

| Command | Follow |
| --- | --- |
| `platform env add`, `update`, `audit` | [environment](./gitops/argocd/references/environment.md) |
| `platform env add` with its first app | [02 new environment and app](./gitops/argocd/workflows/02-new-env-and-app.md) |
| `platform env clone <source-branch> <env>` | [04 clone an environment](./gitops/argocd/workflows/04-clone-env.md) |
| `platform app add` | [01 add an app](./gitops/argocd/workflows/01-add-app-existing-env.md), which uses [service-bootstrap](./gitops/argocd/references/service-bootstrap.md) and `app_add.py` |
| `platform app promote <sandbox-project>` | [03 promote from sandbox](./gitops/argocd/workflows/03-promote-from-sandbox.md) |

Workflows 01–04 end by wiring each app's CI deploy job
([05 deploy job](./gitops/argocd/workflows/05-deploy-job.md)). The one question they ask
about it is, per environment, whether the job runs automatically or manually. A deploy job
for an app that is already deployed follows 05 on its own.

### From onboarding to an environment

1. `platform onboard` writes the project's chart, Dockerfile and CI.
2. The project's first full CI run publishes the chart and the image.
3. `platform env add` or `platform app add` with the project as the source. The
   [app-readiness](./core/references/app-readiness.md) gate runs first:
   - **Not onboarded:** offer `platform onboard`.
   - **Not published:** offer to run the project's pipeline, and run it only on a yes.
   - **Ready:** `app_add.py --app-facts` takes the chart source and image from the project,
     so neither is asked.
4. The same command wires the deploy job, automatic or manual.

### GitOps library

1. **Resolve the release.**
   - Use a local checkout, or a repository and ref, if the request names one.
   - Otherwise use the release the environment pins in its root and `extras/Chart.yaml`. Both
     must pin the same release.
   - Otherwise, for a new environment, use the release the repository's other branches pin.
   - Otherwise use the latest release tag of
     `https://github.com/grootan-devops/argocd-gitops-tpl-library` (`git ls-remote --tags`).
   - Never use `main` unless asked, and never the highest version in the registry, which can
     be an unreleased candidate.
   - Report the source and ref you read, and never mix in linked documentation from another
     ref.
2. **Read** that source's `README.md` first, then only the linked docs the task needs. When
   upgrading, read its `MIGRATION.md` at the selected ref if there is one; don't infer
   migration steps.
3. **Treat versions in examples as examples only.** Pin the release you read, and verify it
   exists in the OCI repository the library's README names.
4. **The CI libraries, for a deploy job,** are read at the release the application project
   already pins ([deploy-job](./core/references/deploy-job.md)), never at another release.

The scripts read the data files the pinned release ships (the environment map, the service
library's chart contract). They stop on a release that ships none, or one with a schema they
don't know.

### GitOps working rules

- **Validate the GitOps link first.** Once the user gives the repository link, run
  `gitops/argocd/scripts/repo_check.py` before any other step reads or writes it
  ([discovery](./gitops/argocd/references/discovery.md) §10). It checks that the repository
  is reachable, that its branches are environments of the library, and that they follow their
  siblings' pattern. Stop when it is not a GitOps repository, or is empty. Ask before going on
  past problems.
- **Discover, don't assume.** Environment specifics (routing, hostnames, TLS, storage, pull
  secrets, shared databases) come from sibling apps and branches
  ([discovery](./gitops/argocd/references/discovery.md)). When the repository and these
  files disagree, the repository wins, and you say so.
- **Helm over manifests.** Public charts get vanilla values plus the minimum changes, with
  the `kustomized-helm` plugin for what values can't set. Private `helm-tpl-library` charts
  get minimal values and no plugin. Raw manifests are written to meet
  [kubernetes-rules](./core/references/kubernetes-rules.md). Anything that can't meet the
  rules is raised with the user, never silently left out.
- **A developer reviews new work before it goes live**
  ([review gate](./gitops/argocd/references/review-gate.md)). A new app in an existing
  environment ships through a review branch and an MR/PR, and is merged and synced only after
  the dev approves in chat. A new environment's branch is pushed and reviewed, and its root
  Application is created only after approval. Changes to apps that already run keep the usual
  path: diff, push on a yes, sync.
- **Don't guess cluster identity or destination.** Stop before any step whose repository,
  registry, cluster or Argo CD access is unavailable.
- **Local preparation needs no approval.** These need the user's explicit request, each time
  and for that action:
  - commit and push
  - opening and merging an MR/PR
  - creating a root Application (never `--upsert`)
  - sync
  - namespace and database changes
  - migration pods
  - CI variables and secrets
  - running an application project's CI pipeline
  - running a deploy

  Verify each one as the workflow describes.
- **Never delete data.** List leftovers (PVCs, buckets, databases) for the user to decide on.
- **Never expose credentials** in files, commands or output.
