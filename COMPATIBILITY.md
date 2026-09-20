# Ecosystem Compatibility

This is the canonical compatibility contract for the Grootan public platform libraries.

## Initial release set

| Component | Compatible release |
|---|---:|
| AI agent skills | `1.0.0` |
| GitHub CI/CD library | `1.0.0` |
| GitLab CI/CD library | `1.0.0` |
| Helm template library | `1.0.0` |
| ArgoCD GitOps template library | `1.0.0` |
| Terraform modules | `1.0.0` |

## Runtime contract

- Helm libraries require Helm 3.13 or newer and Kubernetes 1.28 or newer.
- Terraform modules require Terraform 1.5 or newer and AWS provider 6.x.
- Reusable workflows require the tools named by their workflow contracts; no toolkit
  image release is globally pinned.
- Consumers must pin exact library versions. Branches are not release references.

Patch and minor releases remain backward compatible within major version `1`.
Breaking public-interface changes require a new major version, migration notes, and
an updated matrix before release.

## Compatibility and deprecation policy

- Compatibility guarantees apply only to documented public interfaces and supported
  runtime ranges within the same major release.
- Deprecations are documented in the changelog and, when practical, retained through
  at least the next minor release before removal.
- Security, correctness, provider, platform, or upstream compatibility requirements
  may require faster removal or replacement of unsafe behavior.
- Breaking changes require a new major version, an updated compatibility matrix, and
  explicit migration guidance in the affected repository.
- Toolkit compatibility is capability-based: consumers must provide the tools and
  versions required by the relevant workflow contract rather than pinning a global
  toolkit image release.
