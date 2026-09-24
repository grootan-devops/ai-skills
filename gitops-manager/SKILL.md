---
name: gitops-manager
description: >-
  Bootstrap and manage Argo CD GitOps environments that consume
  argocd-gitops-tpl-library. Use for `argocd env add` and root Application setup;
  not for Komodo or CI-library authoring.
---

# GitOps Manager

This skill handles Argo CD environment bootstrap and root-Application setup. For a new
environment, collect the required GitOps repository URL and Argo CD URL before any
repository or endpoint work. If either is missing, ask for both and stop until supplied.

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

Do not create or modify an environment until the preflight passes. Stop on missing CLI
authentication, inaccessible repository, invalid endpoints, or missing cluster metadata;
never guess the cluster name or Argo CD cluster destination. After successful validation,
generate the files, run Helm checks, push the environment branch, create the root Application,
and verify its Argo CD status as described in the reference guide. Do not expose credentials
in generated files, command output, or the README.
