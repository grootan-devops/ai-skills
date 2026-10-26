# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.8.0] - 2026-10-24

### Added

- `platform-builder`: Terraform repository detection. A repository with `*.tf` files at its root and no application manifest, Dockerfile or chart audits as the `terraform` shape; before, it was unrecognised and no shape check ran on it.
- `platform-builder`: a GitLab `terraform` include set (`common`, `terraform`, `secret-scanning`, `release`). `terraform/.test.gitlab-ci.yml` is conditional, for a repository with a Terratest suite under `tests/`, and the map names `TF_STATE_NAME` as the terraform module's required variable.

### Changed

- `platform-builder`: the GitHub `terraform` shape expects `sonarqube.yml`, as the github-ci-library Terraform example has, and calls `terraform.yml`, which replaces `terraform-lint.yml` and `terraform-test.yml` in github-ci-library `1.7.0`.
- `COMPATIBILITY.md`: GitHub CI/CD library `1.7.0`, the first release with `terraform.yml`, and GitLab CI/CD library `1.10.0`, where no Terraform job runs in `build`.

### Fixed

- `platform-builder`: the GitLab audit no longer reports `terraform/.gitlab-ci.yml` as an unreachable module with an undeclared `build` option. The library's Terraform options are `full-pipeline`, `check` and `lint`, and no Terraform job runs in `build`.
- `platform-builder`: the GitHub `terraform` shape no longer names `terraform-module`, a workflow github-ci-library does not have.

## [1.7.0] - 2026-10-23

### Changed

- Updated [`go.opentelemetry.io/otel`](https://github.com/open-telemetry/opentelemetry-go) from [`v1.46.0` to `v1.47.0`](https://app.renovatebot.com/package-diff?name=go.opentelemetry.io%2Fotel&from=v1.46.0&to=v1.47.0)
- Updated [`go.opentelemetry.io/otel/exporters/otlp/otlptrace`](https://github.com/open-telemetry/opentelemetry-go) from [`v1.46.0` to `v1.47.0`](https://app.renovatebot.com/package-diff?name=go.opentelemetry.io%2Fotel%2Fexporters%2Fotlp%2Fotlptrace&from=v1.46.0&to=v1.47.0)
- Updated [`go.opentelemetry.io/otel/sdk`](https://github.com/open-telemetry/opentelemetry-go) from [`v1.46.0` to `v1.47.0`](https://app.renovatebot.com/package-diff?name=go.opentelemetry.io%2Fotel%2Fsdk&from=v1.46.0&to=v1.47.0)
- Updated [`go.opentelemetry.io/otel/trace`](https://github.com/open-telemetry/opentelemetry-go) from [`v1.46.0` to `v1.47.0`](https://app.renovatebot.com/package-diff?name=go.opentelemetry.io%2Fotel%2Ftrace&from=v1.46.0&to=v1.47.0)
- Updated [`go.opentelemetry.io/proto/otlp`](https://github.com/open-telemetry/opentelemetry-proto-go) from [`v1.11.0` to `v1.11.1`](https://app.renovatebot.com/package-diff?name=go.opentelemetry.io%2Fproto%2Fotlp&from=v1.11.0&to=v1.11.1)
- Updated [`grootantech/toolkit`](https://github.com/grootan-devops/toolkit) from `1.1.0` to `1.2.0`

## [1.6.0] - 2026-10-14

### Added

- `observability-builder`: OneUptime's AI / LLM views as an LLM-monitoring destination. `genai.content.destination` (`langfuse` or `ops`) sends prompts and answers to one tool per service, never both, and `backends/oneuptime.md` describes what the views read: call kinds, problems, conversations, usage and pricing.
- `observability-builder`: who spent what. The spender, the team and the conversation come from the app's own verified context, decided per entry point; on the ops path AI spans carry `user.id`, `team.id` and `gen_ai.conversation.id`, where OneUptime attributes spend.
- `observability-builder`: MCP servers and clients (`workloads/mcp.md`): a span per message on each side, context through `params._meta`, the JSON-RPC status rules, one span per tool call, and the `mcp_server_operation_duration_seconds` and `mcp_client_operation_duration_seconds` metrics.
- `observability-builder`: the `genai-langfuse-two-senders` audit rule; validate checks for the content destination; the receiver's `--genai-content-destination` flag and summary lines for who spent what, AI keys on other spans, retrieval steps, MCP and Langfuse span senders.

### Changed

- `observability-builder`: retrieval steps carry `gen_ai.operation.name=retrieval` and `gen_ai.retrieval.top_k`; memory, planning and agent-creation step kinds are added; a sub-agent call is one `invoke_agent` span; the turn's input and output go on the Langfuse trace; no cost is written unless the provider reports one.
- `observability-builder`: the ops allow-list keeps the MCP keys, the OpenInference and OpenLLMetry model and usage keys, and `user.id` and `team.id`; spans that aren't AI calls lose their `gen_ai.*`, `llm.*` and `traceloop.*` keys, which OneUptime would count as AI calls. The collector's GenAI routing does the same.
- `observability-builder`: one span sender to Langfuse per service is a stated rule; all three Langfuse modes stay, with `hybrid` the default.
- `observability-builder`: SDK notes for `openai` 3 and `mcp` 2 over `httpx2`, the MCP Python SDK 2.x client spans and protocol probe, OTLP exporter errors fed back into the log export, `ExceptionGroup` on FastAPI SERVER spans and the JavaScript exporters' warning on JSON replies. Direct export to OneUptime: ops metrics need a scraper, and the yml adds `deployment.environment` for exceptions. Path exclusions are decided when the response ends.
- README and COMPATIBILITY list the `1.6.0` release.

## [1.5.0] - 2026-10-10

### Added

- `observability-builder`: instruments any service or frontend for end-to-end observability from one `observability.yml` — access logs, structured application logs, OpenTelemetry traces with nested function spans, ops metrics on a Prometheus `/metrics` port, error logs, Pyroscope profiling, browser RUM and session replay, and LLM and agent telemetry with Langfuse — and audits existing telemetry for unsafe capture. Two workflows: `observability instrument` (detect, ask, audit, research, plan, implement, verify against a real receiver, report) and `observability audit`.
- `observability-builder`: the observability standard it applies, bundled in `references/` (signals, workloads, backends, collector, data model, safe capture, SDK notes), and the `observability.yml` JSON Schema in `assets/`.
- `observability-builder`: `detect.py`, `validate.py`, `audit.py`, `contract.py` and `hold_port.py`; `receive.py`, a local OTLP, Pyroscope and session-replay receiver with a Langfuse stand-in and canary tracking; `llm_stub.py`, an OpenAI- and Anthropic-compatible model stub, so verification never calls a real provider.
- `observability-builder`: Langfuse export, content-guard and score pieces for Node.js, Go and Java, and the Java JFR-to-pprof profile exporter.
- `observability-builder`: `PROMPT.md` and script regression tests.

### Changed

- Pinned the repository's reusable workflow callers to `github-ci-library` `@1.5.0`.
- PR verification runs the `observability-builder` script tests.
- README and COMPATIBILITY list the `1.5.0` release set and the new skill; `observability-builder` bundles its standard instead of reading a library.

## [1.4.0] - 2026-10-01

### Added

- `terraform-module-consumer`: composes existing, versioned Terraform modules into Terraform or Terragrunt environment configurations, with state-safe validation and no direct managed resources or apply.
- `platform-builder`: working rules for scope, change size, removed content, runtime dependencies and lockfiles; project values (registry, pull secret, repository path, `partOf`, component, subComponent) are suggested and confirmed with the user.
- `platform-builder`: audit checks for the canonical PID 1 shape (`ENTRYPOINT ["/usr/bin/dumb-init", "--"]` with the process or a start script in `CMD`), chart `command`/`args` overrides and start scripts that do not `exec`.
- `platform-builder`: chart checks for Service ports, named probe and route ports, empty pod `securityContext`, disabled mounts, hard-coded PVC claims, non-HTTP release overlays, per-mode `subComponent`, schema enums and `minLength` on empty defaults, unused optional blocks, undocumented job/cronjob/persistence leaves, and credentials in ConfigMaps, `env` values or `args` across all containers.
- `platform-builder`: checks that the chart image equals the CI push path, placeholder registry and pull secret, AI/agent files present or committed (reported, never removed), changelog H1, SemVer for `Project:Version:Init`, and unknown `extends:` targets.
- `platform-builder`: `chart-lint.sh`, `chart-docs.sh`, `names.py` and `verify_siblings.py`.
- `gitops-manager`: `argocd app add` — service bootstrap steps and `app_add.py`, which adds a service's `apps` entry (single release or a release of a multi-release group) to an environment's root `values.yaml`, scaffolds its `values/` file from the service chart with placeholders for environment values and credentials, never overwrites, and can render-check the new Application.
- `gitops-manager`: `override_check.py`, which reports environment overrides that belong in the chart.

### Changed

- Every skill holds only its workflow instructions and scripts. Standards, contracts and starter templates are documented in the libraries the skills read at the resolved ref:
  - `helm-tpl-library`: chart description and README, the values replica, naming, container keys, derived names, routes, mounts and file mounts, rendering behaviour, secrets, schema, security posture, probes, renames, ignore files and charts with several releases;
  - `gitlab-ci-library` and `github-ci-library`: the include set and scenario files, project jobs, `WORKFLOW` options, job responsibilities, per-stack rules, Dockerfile standards (PID 1, base images, comments, `# renovate:`, cache handoff, `.dockerignore`, project base images) and the consumer security review;
  - `argocd-gitops-tpl-library`: the `apps` registry, what an environment values file overrides, the environment starter files and the GitOps README cluster convention;
  - `terraform-modules`: module consumption, provider-schema mapping, state migration, test shapes, other-provider tables and the architecture diagram template.
- The skills name decision areas, never library file names: each reads a library's README index at the resolved ref and follows only the rows a task needs, so a library can rename, split or extend its guides without a skill release.
- `platform-builder`: SKILL.md carries the update and audit steps (engine and judged findings) that the removed references held.
- `gitops-manager`, `terraform-module-builder` and `terraform-module-consumer` name their library's public repository as the default source, at `main`, as `platform-builder` does in `core/libraries.json`; a source named in the request still wins. `terraform-module-builder` reads the current repository when it is terraform-modules and edits only a local checkout, and `terraform-module-consumer` reads the docs at the stack's pinned module source before any local checkout. The README lists where each skill resolves its libraries, and every `gitops-manager` prompt has a library source slot.
- `platform-builder`: subComponent and chart names are suggestions, not a fixed vocabulary; subComponent is required only for multi-mode charts.
- `platform-builder`: jobs, cronjobs, persistence, metrics and `global.tracing` are optional features, absent unless used; `global.metrics` may be omitted without a monitor entrypoint.
- `platform-builder`: PID 1 findings are P1 against the canonical shape; comments in CI files are one line saying why.
- `platform-builder`: README drift is detected by regenerating with helm-docs instead of file timestamps.

### Removed

- `platform-builder`: `core/references/`, `platforms/*/references/` and `assets/`; `gitops-manager`: `assets/bootstrap/`; `terraform-module-builder`: `references/` and `assets/` — their content moved to the libraries above. The lists of known library defects are dropped; the one still open (`Terraform:Check:README` gating) is fixed in `gitlab-ci-library`.
- `platform-builder`: the `USE_DOCKER_BUILDX` guidance and finding, merge-request instructions, the requirements-file and `pyproject.toml` indentation checks, and automatic removal of agent tooling.

### Fixed

- `platform-builder`: GitLab `!reference` tags parse; non-image `ARG` defaults and the project's own registry are no longer reported as external images; the Cross-Service Override Law check no longer fires on every sibling reference; a dependency-download job is required only when a language module is included; library pins are read from `remote:` include URLs.
- `platform-builder`: suggested micro base image names match the published `grootantech/micro-*` repositories.

## [1.3.0] - 2026-09-25

### Added

- Added Dual-Format (Quick and Full) `PROMPT.md` guides across `platform-builder`, `gitops-manager`, and `terraform-module-builder`.
- Added unit test suite in `terraform-module-builder/scripts/tests/test_detect_migrations.py` covering dynamic Git root discovery.
- Added focused Terraform checker and documentation regressions, linked-worktree and submodule migration tests, and PR CI gates for the Terraform and platform Skill suites.

### Changed

- Aligned platform builder checks with `tpl.container.image.repository` auto-derivation in `helm-tpl-library`.
- Implemented Clean Architecture Split: decoupled central skills and test suites from CI library templates.
- Pinned repository CI reusable workflow callers to `github-ci-library` `@1.3.1`.
- Clarified Terraform state-plan review and permitted apply only after a separate request and confirmation of the exact saved plan and target.

### Fixed

- Corrected Terraform checker false positives for sensitive outputs and minimum provider bounds; detect conditional governance-tag overrides.
- Detect unreferenced Terraform data sources in any root-level module file.
- Removed the migration detector's misleading clean/patch verdict for changes it cannot assess, including defaults and instance keys.
- Corrected the state-migration guide's reference to procedures absent from the module library's current `MIGRATION.md`.
- Corrected module documentation drift checks and stopped generating unverified security claims and relative-source examples for missing READMEs.

## [1.2.0] - 2026-09-23

### Added

- Added the focused `gitops-manager` skill for authenticated Argo CD environment bootstrap,
  standard consumer chart scaffolding, and root Application creation; deprecated the broad
  `gitops-app-manager` skill for new work.

### Removed

- Removed the `gitops-app-manager` skill; use `gitops-manager` for Argo CD environment bootstrap.

### Changed

- Update platform-builder's mock-chart default to `tests/` and document the nested suite path.

- Align GitLab onboarding guidance and checks with stack-scoped dependency jobs, stable shared
  cache keys, and read-write BuildKit mounts; keep GitHub cache-handoff guidance platform-specific.
- Load shared-library documentation progressively from README task indexes at the selected ref.
- Validate linked documentation, examples and topic coverage in both CI-library verifiers.
- Require meaningful module-index descriptions and preserve explicit source refs independently of example pins.
- Align chart publishing guidance with GitHub OCI and GitLab's empty-registry package fallback.
- Report the resolved remote default branch, preserving branch names containing slashes, when a supplied URL omits its ref.

## [1.1.0] - 2026-09-22

### Changed

- Updated the platform-builder Helm ignore baseline for GitHub Actions-era repository metadata.
- Pinned the consuming GitHub Actions workflows to `github-ci-library` 1.0.0.

## [1.0.0] - 2026-09-19

### Added

- Initial public release.
