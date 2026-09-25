# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
