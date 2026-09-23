# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-09-23

### Changed

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
