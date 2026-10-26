# Compatibility

The skills carry no library versions. Each skill reads a library at the release the project
pins (its CI includes or reusable workflows, its chart or module dependency) and, for a new
project or an upgrade, at the library's latest release tag. A library release therefore needs
no skill release.

Each library documents its own release, runtime requirements, versioning and deprecation
policy, and upgrade steps, in its README and MIGRATION.md:

| Library | Read |
| --- | --- |
| GitHub CI/CD library | [github-ci-library](https://github.com/grootan-devops/github-ci-library) |
| GitLab CI/CD library | [gitlab-ci-library](https://github.com/grootan-devops/gitlab-ci-library) |
| Helm template library | [helm-tpl-library](https://github.com/grootan-devops/helm-tpl-library) |
| ArgoCD GitOps template library | [argocd-gitops-tpl-library](https://github.com/grootan-devops/argocd-gitops-tpl-library) |
| Terraform modules | [terraform-modules](https://github.com/grootan-devops/terraform-modules) |

The skills' checks read the rules they apply from a data file each library ships with its
release: `docs/workflow-map.json` in the CI libraries, `docs/chart-contract.json` in the Helm
template library and `docs/environment-map.json` in the ArgoCD GitOps template library. A
release that ships no data file, or one with a `schema` a skill does not know, stops the
skill with what to upgrade: the project's library, or the skills themselves.
