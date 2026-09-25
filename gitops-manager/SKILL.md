---
name: gitops-manager
description: >-
  Bootstrap and manage Argo CD GitOps environments that consume
  argocd-gitops-tpl-library. Use for `argocd env add` and root Application setup;
  not for Komodo or CI-library authoring.
---

# GitOps Manager

This skill prepares, audits, and, when explicitly requested, activates Argo CD environments.
For local preparation, collect the GitOps repository URL, Argo CD URL, project, and environment.
An authenticated Argo CD identity is needed before reading cluster state or activating an app.

For the complete preflight, repository layout, chart scaffolds, validation, and activation
sequence, read [the Argo CD environment guide](./references/argocd-environment.md). Use its
starter files in `assets/bootstrap/` rather than inventing a different consumer layout.

When consuming `argocd-gitops-tpl-library`:

1. Resolve the requested local checkout or repository/ref first. Explicit branches, tags,
   and commits are exact; a local path includes its current uncommitted files. A repository
   URL without a ref uses that repository's actual default branch. With no source specified,
   use `main`. Never mix linked documentation from another ref.
2. Read that source's `README.md` first, then only the linked docs relevant to environment
   bootstrap. Resolve relative links from the same checkout/ref; do not follow a link to
   another branch or recursively crawl unrelated docs. When upgrading, read that source's
   `MIGRATION.md` if it exists at the selected ref; if it does not, use the README and relevant
   topic guides from that same checkout and do not infer migration steps. For older releases
   with a monolithic README or no linked guide, find the relevant sections there.
3. Treat version strings in examples as examples only. Pin the highest stable chart version
   actually published to the OCI registry; do not derive a dependency version from a sample
   workflow pin or an unreleased source branch.

Do not guess cluster identity or destination. Stop before any step whose required repository,
registry, or Argo CD access is unavailable. Prepare files and run Helm validation locally;
`argocd env add` or `update` alone does not authorize a commit, push, Application creation,
or sync. Perform each remote action only when the user's request explicitly includes it, then
verify its result as described in the reference. Never expose credentials in files or output.
