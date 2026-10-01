# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- `gitops-manager`, `terraform-module-builder` and `terraform-module-consumer` name their library's public repository as the default source, at `main`, as `platform-builder` does in `core/libraries.json`; a source named in the request still wins. `terraform-module-builder` works in the current repository when it is terraform-modules, and `terraform-module-consumer` reads the docs at the stack's pinned module source before any local checkout. The README lists where each skill resolves its libraries.
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
