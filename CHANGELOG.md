# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-01

### Added

- Every skill checks for a newer ai-skills release each time it runs, and asks before updating. Each skill ships an identical `update_check.py`; the release lives only in the repository's root `VERSION`. A git checkout is compared by its tag, or else that root `VERSION`, and its branch commits, with the repository's latest release tag and remote branch. A copied install doesn't record its release, so the check names the latest release and offers the reinstall command. A test keeps the copies identical, no skill carrying its own `VERSION`, and the check the first step of every `SKILL.md`.
- `k8s-upgrade`: upgrade a Kubernetes cluster one minor version at a time in six stages run one per request (`init`, `lint`, `check`, `prepare`, `upgrade`, `validate`), each refusing to start until the stages before it passed. It baselines critical components, pods, PVCs, databases and PVC usage, takes backups, upgrades the control plane and then the nodes with a gate per node, and validates against the baseline with a throwaway smoke test. The kubeadm provider is supported; EKS, AKS, GKE and Scaleway Kapsule are planned.
- `platform-builder` manages GitOps environments and the apps deployed through them (Argo CD on `argocd-gitops-tpl-library`; Komodo planned). The commands are:
  - **`platform env add|update|audit`**: bootstrap, update and audit an environment.
  - **`platform env clone`**: create an environment of a product from one of its branches, its databases included.
  - **`platform app add`**: trace a chart, CI URL or onboarded project, classify the app (public chart, private `helm-tpl-library` chart or raw manifests) and make it meet the cluster's workload rules (`core/references/kubernetes-rules.md`).
  - **`platform app promote`**: move a sandbox stack into its own environments and migrate its data.
- `platform-builder`: `env add`, `app add`, `env clone` and `app promote` wire each app's CI deploy job from the CI library's GitOps deploy template, at the release the application project already pins. The only question is whether each environment's job runs automatically or manually. `core/scripts/ci_pin.py` finds the platform and the pin, and checks the template ships in that release.
- `platform-builder`: a readiness gate (`core/references/app-readiness.md`) takes a project from `platform onboard` to an environment:
  - **Reads the project.** `core/scripts/app_facts.py` reads its chart, image and CI-library pin from its own files, finding the chart the way the audit does.
  - **Checks the registry.** It confirms the chart version and image tag for the environment's channel are published, read-only, with the user's existing login.
  - **Offers the first run.** If they are not published, it offers to run the project's pipeline, and does so only on a yes.

  `app_add.py --app-facts` then takes the chart source, the service values and the main image from the project, so they are not asked.
- `platform-builder`: the GitOps repository link is validated once, before any other step reads or writes it (`gitops/argocd/scripts/repo_check.py`). The check confirms the repository is reachable with the user's own credentials and that at least one branch is an `argocd-gitops-tpl-library` environment. On such a branch, the root and `extras/` charts pin one release under one name, and `values.yaml` has an `apps` map. It also compares each environment branch with its siblings: library pins, values keys, files and the recorded branch. It stops for a repository of another kind or an empty one, and it asks before going on past broken branches, which are never used as templates. `platform app promote` now asks for the repository link too.
- `platform-builder`: a developer reviews new work in the GitOps repository before it goes live (`gitops/argocd/references/review-gate.md`). `platform app add` commits a new app on a `review-<product>-<env>-<app>` branch and opens an MR/PR into the environment branch. The dev reviews it, pushes changes if needed and approves in chat; the skill then re-renders, merges and syncs. `platform env add`, `env clone` and `app promote` push the new environment branch and hand it over for review; the root Application is created only after approval. `gitops/argocd/scripts/review_links.py` prints the branch, MR/PR and file links. Changes to apps that already run keep the usual path.
- `platform-builder`: once new apps are live, the report opens with what the developer keeps (`gitops/argocd/scripts/app_report.py`). For each app: its URL (Argo CD's live external URLs, else the hosts in its own files), its Argo CD Applications with UI links, links to its values file or manifests folder and to the line that declares it in the root `values.yaml`, then the root Application and the `argocd app sync` commands to run later, in order.
- `platform-builder`: new GitOps values files follow the layout of the branch's existing ones (`gitops/argocd/scripts/yaml_style.py`): indent, list indent, blank lines between top-level keys and, depth by depth, between nested blocks, and the quote character. `app_add.py` learns it from the branch's own `values/` files, else from another environment branch in the clone, and prints which files it followed.
- `platform-builder`: `core/scripts/naming.py` derives a new environment's label, chart, branch, namespace and root Application name. The cluster label is the one the GitOps repository's README records, else the repository's name, else the user is asked. The label prefixes the chart name (`<label>-<product>`), the library's `cluster` value stays unset, and the default branch is `<product>/<env>`.

### Changed

- Every skill's README lists its commands, what each does, and what it asks for when an input is missing. A command works on the project in the current directory when it is one, and asks for its path otherwise. Every other input is read from the repositories, or asked for, never guessed.
- `platform-builder` is laid out for GitOps backends the way it is for CI platforms. Shared GitOps references and scripts are in `core/` (`gitops_library.py` holds the shared library handling), and Argo CD's references, workflows and scripts are in `gitops/argocd/`.
- `platform-builder`: an onboard's report ends with the next step, which is to publish once and then run `platform env add` or `platform app add`.
- CI runs the Argo CD GitOps suite in the platform-builder job, and the new `k8s-upgrade` suites. The workflows use `github-ci-library` 1.9.0. The entry-file test covers the GitOps and `k8s-upgrade` references and workflows.

### Removed

- Every skill's `PROMPT.md`. Agents route on the command (for example `platform app add`) through `SKILL.md`, so the copyable prompts only repeated the workflows. Each skill's README now has the command list.
- `platform-builder`: the `platform ship` command. `platform env add` and `platform app add` add the environment and the app to the GitOps repository, and wire each app's CI deploy job.
- The `gitops-manager` skill. Its commands are `platform-builder` commands now: `argocd env add|update|audit|clone` became `platform env …`, and `argocd app add|promote` became `platform app …`. Its scripts moved into `platform-builder` (see MIGRATION.md).

## [1.8.0] - 2026-10-27

### Added

- Every library-backed skill reads a library at the release the project pins and, for a new project or an upgrade, at the library's latest release tag. `platform-builder` finds the pin in the GitLab includes, the GitHub reusable workflows and the chart's `tpl-library` dependency, `gitops-manager` in the environment's root and `extras/Chart.yaml`. `libraries.py` and `audit.py` take `--latest` for an upgrade, which `platform update` uses. A library release needs no skill release.
- `platform-builder`: the checks read their library rules from the data file that release ships, `docs/workflow-map.json` of the CI library and `docs/chart-contract.json` of `helm-tpl-library`; `chart-docs.sh` takes its helm-docs flags from the contract of the chart's own dependency. A release without the file, or with a `schema` the skill does not know, stops the run before any check and says what to upgrade.
- `platform-builder`: a "Mixed Library Pins" finding when includes or workflows pin different releases of one library; the run reads the oldest.
- `platform-builder`: Terraform repository detection. A repository with `*.tf` files at its root and no application manifest, Dockerfile or chart audits as the `terraform` shape; before, it was unrecognised and no shape check ran on it.
- `gitops-manager`: `app_add.py` takes the Application name, namespace and release name from `helm template` of the library release the environment pins, and the values-file path, sync presets and release-shape keys from its `docs/environment-map.json`. `override_check.py` takes what an environment may override from the same map, and the service library's root keys from the `tpl-library` release the service chart pins.

### Changed

- CI calls the github-ci-library 1.7.0 reusable workflows instead of 1.5.0.
- `COMPATIBILITY.md` lists no library releases; it says how the skills choose a release and that each library's README and MIGRATION.md own its versions, requirements and upgrades. The README carries no release number, and the install commands check out the newest tag.
- `platform-builder`, `gitops-manager`: the bundled workflow maps and the copies of the libraries' chart, naming and override rules are gone; the entry files point at the libraries' docs instead of restating them, and a test keeps versions and release claims out of them.
- `platform-builder`: the migration chain takes the release list from the library's remote, so a shallow clone no longer drops the releases between the pin and the target, and a pre-release is not counted as a release.

### Removed

- `platform-builder`: chart rules the library does not have: the `myorg` and `[PRODUCT_NAME]` placeholders, the parity exception for `component` and `subComponent`, the `configmapEnvs` `@default` rule and `values-*.yaml` overlays.

### Fixed

- `platform-builder`: "Chart Image Differs From CI Push Path" no longer reports a correct chart when `IMAGE_REPOSITORY` lists several repositories, or full repositories for a credential group; the chart may pull from any listed one.
- `platform-builder`: the base-image and dependency-cache guidance points at the selected CI library's Dockerfile standards instead of naming images and library behaviour that change with its releases.
- `observability-builder`: the skill version comes from the ai-skills release, not from a README line that was not kept up to date.

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
