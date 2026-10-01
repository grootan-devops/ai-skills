---
name: gitops-manager
description: >-
  Bootstrap and manage Argo CD GitOps environments that consume
  argocd-gitops-tpl-library. Use for `argocd env add`, root Application setup,
  `argocd app add` (bootstrapping a service into an environment: its `apps` entry
  and values file), and writing or reviewing an environment's values overrides for
  service charts; not for Komodo or CI-library authoring.
---

# GitOps Manager

This skill prepares, audits, and, when explicitly requested, activates Argo CD environments.
For local preparation, collect the GitOps repository URL, Argo CD URL, project, and environment.
An authenticated Argo CD identity is needed before reading cluster state or activating an app.

For the preflight, validation and activation steps, read
[the Argo CD environment steps](./references/argocd-environment.md). The repository layout,
starter files, naming and root Application manifest come from the library docs — the README
index tasks for bootstrapping an environment and the root Application; use them rather than
inventing a different consumer layout.

When consuming `argocd-gitops-tpl-library`:

1. Resolve the library source: a local checkout or repository/ref named in the request,
   otherwise the default, `https://github.com/grootan-devops/argocd-gitops-tpl-library` at
   `main`. Explicit branches, tags, and commits are exact; a local path includes its current
   uncommitted files. A repository URL without a ref uses that repository's actual default
   branch. Report the source and ref you read, and never mix linked documentation from
   another ref.
2. Read that source's `README.md` first, then only the linked docs relevant to the task —
   environment bootstrap, adding a service, or its values file. Resolve relative links from the same checkout/ref; do not follow a link to
   another branch or recursively crawl unrelated docs. When upgrading, read that source's
   `MIGRATION.md` if it exists at the selected ref; if it does not, use the README and relevant
   topic guides from that same checkout and do not infer migration steps. For older releases
   with a monolithic README or no linked guide, find the relevant sections there.
3. Treat version strings in examples as examples only. Pin the highest stable chart version
   actually published to the OCI registry; do not derive a dependency version from a sample
   workflow pin or an unreleased source branch.

To bootstrap a service into an existing environment (`argocd app add`), follow the library's
README index task for adding a service and [the service bootstrap steps](./references/service-bootstrap.md),
then use `scripts/app_add.py`: it adds the service's `apps` entry to the root `values.yaml` and
scaffolds its `values/` file from the service chart, without overwriting anything. Ask for
the chart source, a published version and every environment value it leaves as a placeholder.

When writing or reviewing the values file an environment applies to a service chart, follow
the library docs on what a values file overrides and run `scripts/override_check.py`
against the chart. Report anything else as belonging in the chart, and change it only when
the user agrees.

Do not guess cluster identity or destination. Stop before any step whose required repository,
registry, or Argo CD access is unavailable. Prepare files and run Helm validation locally;
`argocd env add`, `update` or `argocd app add` alone does not authorize a commit, push,
Application creation, or sync. Perform each remote action only when the user's request explicitly includes it, then
verify its result as described in the reference. Never expose credentials in files or output.
