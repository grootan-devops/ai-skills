# Migration Guide

This document records required consumer actions when upgrading between releases.
Breaking changes must include an entry before release.

To upgrade, apply every section after your installed version up to the target, oldest first.
Newer sections are split into **Required** (the upgrade breaks or misbehaves without it),
**Recommended** (aligns existing projects with the current standards) and **Verify**.

## 1.4.0

### Required

- Reinstall or re-link the skills from this release. `platform-builder` no longer ships
  `core/references/`, `platforms/*/references/` or `assets/`, `gitops-manager` no longer ships
  `assets/bootstrap/`, and `terraform-module-builder` no longer ships `references/` or
  `assets/`. Point anything that read those files, such as editor rules, project instructions
  or uploaded Project Knowledge, at the library documentation instead.
- The standards now come only from the libraries, read at the resolved ref. A library pinned in
  `.platform-builder.json` or `$PLATFORM_BUILDER_LIB_*` must be at least the release listed in
  [COMPATIBILITY.md](./COMPATIBILITY.md); an older pin lacks the guidance the skills follow.
  The same applies to the terraform-modules checkout the Terraform skills read.

### Recommended

- Run `platform update` on onboarded repositories. The audit now reports a PID 1 shape other
  than `ENTRYPOINT ["/usr/bin/dumb-init", "--"]` with the process in `CMD`, chart
  `command`/`args` overrides and start scripts that do not `exec` as P1 findings.
- `argocd app add` adds a service to an existing environment, and `terraform-module-consumer`
  composes infrastructure from released modules.

### Verify

- `python3 platform-builder/core/scripts/libraries.py <repo> --all` lists each library's
  README index and `MIGRATION.md` at the expected ref.

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
