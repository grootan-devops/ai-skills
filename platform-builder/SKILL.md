---
name: platform-builder
description: >-
  Create, update, and audit GitHub Actions or GitLab CI pipelines, packaging
  Dockerfiles, and Helm consumer charts against the selected CI and Helm
  libraries. Use for repository onboarding, library migrations, deployment
  wiring, and platform compliance reviews.
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

## Load the relevant reference

| Decision | Reference |
| --- | --- |
| CI scenarios, job selection, and platform pinning | `platforms/<github or gitlab>/references/workflow-matrix.md` and its `workflow-map.json` |
| Platform credential and permission mechanics | core/references/security-core.md, then the selected platform's security-addendum.md |
| Language build, cache, and packaging | core/references/language-stacks-core.md and the selected library's module guide |
| Helm values, helpers, schema, and naming | core/references/helm-chart-standard.md and the selected Helm library guides |
| Runtime files and storage | core/references/file-mounts-standard.md |
| Ignore files | core/references/ignore-files-standard.md |
| Version changes | core/references/migration-standard.md |
| GitLab-specific stack wiring | platforms/gitlab/references/stack-snippets.md |

Do not load both platform adapters for an ordinary single-platform task. The selected
library's implementation remains authoritative for its supported inputs and outputs;
workflow-map.json owns only Skill-side scenario selection.

## Make the change

Inspect existing CI, Docker, chart, environment, and test assets before writing. For
onboard, classify the repository shape, choose only executable workflows and modules,
and preserve discovered ports, settings, mounts, and runtime behavior. Add a chart's
optional jobs, cronjobs, metrics, or persistence only when the workload needs them; pair
each enabled value with its required tpl entrypoint. For a new chart, let the Helm
library derive the main image repository from partOf/component/subComponent when
image.repository is empty. Keep explicit repository values for images that need a
different path.

### Phase 2b: interactive choices before scaffolding

Before scaffolding a new image or chart, show the detected platform, repository
shape, harvested settings, selected library provenance, and proposed files. Ask
the relevant questions for each artifact being added. An existing runnable
fixture is evidence for enabling its test; state that proposed default when asking.
An explicit user request or clear existing workload configuration settles an
optional resource choice without asking it again.

| Present | Ask | If enabled |
| --- | --- | --- |
| Dockerfile | Smoke-test the built image? | Wire the GitHub `docker.yml`/`buildah.yml` `test: true` and `test-script` (default `ci_image_test.sh`), or GitLab `.Image:Test`. |
| Chart | Unit-test the chart? | Wire GitHub `chart.yml` `run-unittest: true` with `tests/*_test.yaml` suites, or the selected GitLab chart test job. |
| Chart | Does the workload require persistent storage (PVC)? | Add `persistence:` and `tpl.pvc`; otherwise omit both. |
| Chart | Does the workload require batch jobs, recurring cronjobs, or Prometheus metrics scraping? | Add only required values and matching `tpl.job`, `tpl.cronjob`, or `tpl.servicemonitor` entrypoints. |

If a runnable smoke script or chart test suite already exists, preserve and wire
it by default. If the user enables a new test, create a fixture that checks the
application's actual contract. Do not declare a test job without a runnable
fixture. Questions about optional chart resources are unnecessary when the
workload evidence or explicit request already answers them.

For update, compare the current consumer with the selected library versions and
intermediate migration notes. Change only required refs, schemas, wiring, and verified
defects. Preserve intentional cache keys, custom jobs, values, annotations, probes, and
formatting. Ask only when a genuine ambiguity or a conflicting customization cannot
be resolved from the repository. Do not reset files to starter templates.

For ship, prepare wiring for the requested environment and validate it. Do not infer
a production target, credential, or cluster. A local change does not authorize a
deployment, commit, or push.

For audit, run the engine and review what it cannot establish: secret handling,
token scope, rendered chart security, untrusted input paths, and unsupported library
options. Label findings as engine results or manual judgments and give file evidence.
Do not edit the audited target.

## Validate and report

Run core/scripts/audit.py with --strict and the same local paths or refs used for the
change. Check rendered Helm output and schema when a chart changed, and the selected
platform's native lint or CI checks when a pipeline changed. Report exact library
provenance, commands and outcomes, remaining risks, and any publishing prerequisite.
Do not claim a local library change is present in a remote workflow or chart release.
