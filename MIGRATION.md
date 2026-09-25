# Migration Guide

This document records required consumer actions when upgrading between releases.
Breaking changes must include an entry before release.

## 1.3.0

Terraform module interfaces require no consumer migration. Prompt scaffolds now feature Dual-Format options (Quick for streamlined pasting and Full for complete governance). Reusable workflows are pinned to `@1.3.1`. Terraform module work still stops after local validation by default; an explicitly requested apply now requires confirmation of the exact saved plan and target after review.

For a new module, write and review its README narrative and public release-tag examples before running `generate-module-docs.py`. The tool now updates only the generated tables in an existing README; it no longer creates a file with unverified security claims or a relative module source.

## 1.2.0

### Skill Removals

- The broad `gitops-app-manager` skill has been removed. Switch to `gitops-manager` for Argo CD environment bootstrap, standard consumer chart scaffolding, and root Application creation.

### Documentation & Templates

The documentation restructuring requires no consumer configuration changes. Start at the README index and follow its
task-specific documentation links; update any bookmarks to moved sections. Library source/ref precedence is unchanged.

Updated chart generation follows the resolved library's registry contract. GitLab users
upgrading the CI library must review its chart-registry migration: a nonempty `CHART_REGISTRY`
now selects OCI, while an unset/empty value selects the current project's Helm Package Registry.

## 1.1.0

No migration is required. The platform-builder Helm ignore baseline now excludes
repository metadata files that must not be packaged into charts.

## 1.0.0

No migration is required for the initial release.
