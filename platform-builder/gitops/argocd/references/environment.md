# Argo CD Environment Bootstrap (`platform env add`, `update`, `audit`)

Use this reference for `platform env add`, local preparation, and explicitly requested
activation of the root Application that watches a new GitOps branch.

## Inputs and stop conditions

Before preparing files, obtain both URLs:

- GitOps repository URL (the repository Argo CD will watch).
- Argo CD URL (the API/UI endpoint).

If either URL is absent, ask and pause. Collect the project/application name and
environment. Suggest `dev`, `qa`, `test`, `uat`, `pre-prod`, and `prod`; an explicitly empty
environment is allowed and removes the `environment` key from both values files. Ask for an
optional target branch override; otherwise the branch is `<product>/<env>`
([naming.md](../../../core/references/naming.md)). Validate the project and branch as Git/Helm-safe names and show the
resolved names before writing.

Stop the affected step if its CLI, authentication, URL, repository access, destination cluster,
or required metadata is unavailable. Do not claim activation is ready when a remote preflight
has not passed. Never ask users to paste passwords or tokens into chat.

## Preflight

1. Check `git` and Helm for local preparation; check `argocd` and the matching Git host CLI
   only before a remote step that needs them. Use `glab` for GitLab and `gh` for GitHub when
   available. Validate the repository URL once with `gitops/argocd/scripts/repo_check.py`
   ([discovery](./discovery.md) §10): it must be readable with the existing CLI or git
   credential helper, and be an environment repository of the library that follows its own
   pattern. Stop on exit 2 or 3. On exit 1, show the problems and go on only if the user says
   so.
2. Before activation, verify the Argo CD endpoint and logged-in identity with the installed CLI, for example
   `argocd version --server <ARGOCD_URL>` and
   `argocd account get-user-info --server <ARGOCD_URL>`. Use the CLI's supported TLS/gRPC
   flags when required by the deployment. Do not print tokens or credential-bearing URLs.
3. Read `README.md` from the GitOps repository's default branch and derive the names with
   [naming.md](../../../core/references/naming.md) (`core/scripts/naming.py`): the cluster
   label the README records, else the repository's name, else ask. The label prefixes the
   chart name (`<label>-<product>`) and the library's `cluster` value stays unset. Take the
   Argo CD destination from a sibling branch; when the README records an Argo CD cluster
   name, the destination must match it. Check it against the `NAME` column of
   `argocd cluster list --server <ARGOCD_URL>` before activation.
4. Before activation, check that the Argo CD URL is reachable and authenticated, and that the GitOps repository
   URL is valid and readable by the current Git identity. Confirm Argo CD can use the
   repository (including its registered credentials/access); if its repository status is
   missing or failed, stop and report the exact repository setup required.
5. Resolve the library documentation source using the source-selection rules in `SKILL.md`.
   Read the library README index and the linked bootstrap/configuration/extras pages. Read
   migration notes when updating an existing environment. For a new environment's dependency
   pin, take the highest non-prerelease SemVer release from the library's published GitHub
   releases/tags, then verify that exact version in the OCI repository the library's README
   names:

   ```sh
   helm show chart <oci-repository>/argocd-gitops-tpl-library --version <version>
   ```

   Do not use an unreleased branch's `Chart.yaml` version as a published dependency. If the
   release or OCI artifact cannot be verified, stop and ask rather than writing a guessed pin.
6. Check whether the target branch exists before local creation; check whether the root
   Application exists before activation. Do not
   overwrite an existing branch, change an existing app, or use `argocd app create --upsert`
   unless the user specifically asks to update that existing environment.

When creating a new local branch, base it on the repository's default branch. Show the
derived label, its source and the names before writing; if the user does not confirm them,
stop.

## Derived configuration and files

Take the names from [naming.md](../../../core/references/naming.md), plus the user's
project, environment, Git URL and optional branch; the default branch is `<product>/<env>`.
The library's starter files, layout and root Application manifest (Argo CD projects,
`argo-cd` namespace, sync options, destination by name) come from its docs at the resolved
ref: follow the README index tasks for bootstrapping an environment and the root Application.
Repository conventions on top of the library's — environment settings copied from a sibling
branch, `CreateNamespace=false`, the README's Kyverno exceptions table — are in
[discovery.md](./discovery.md). Keep credentials out of every file, the environment README
included.

## Validate locally; activate only when requested

1. Build both dependency trees and validate both charts. Run `helm lint --strict` and
   `helm template` for `.` and `./extras`, using the configured OCI registry access. Confirm
   the rendered Applications are the ones the library's Configuration and Extras pages
   describe for these values. Confirm default branch, namespace, app names,
   project, repo URL, server name, and sync/retry settings against the README and user inputs.
2. Show the resolved branch, changed-file list, generated README/root-app name, and validation
   results. Stop here for a request limited to `platform env add` or `update`; those commands
   authorize local edits and checks only.
3. If explicitly requested, commit and push the validated scaffold. Do not push credentials
   or generated dependency artifacts that the repository ignores.
4. Hand the pushed branch to a developer for review
   ([review-gate.md](./review-gate.md), part B) and wait for their approval in chat. Then,
   if explicitly requested, create the root Application only after confirming its target
   branch is already available to Argo CD. A request to create does not itself authorize
   pushing a missing branch. Create from the rendered manifest with `argocd app create -f
   <root-application.yaml> --server <ARGOCD_URL>`. Do not use `--upsert`. When the manifest
   enables automated sync, this step may deploy workloads.
5. Read the created Application with `argocd app get <name> --server <ARGOCD_URL>` and report
   its sync/health status and branch/commit. If creation or sync fails, stop; do not repeatedly
   retry or change credentials/configuration without user direction.

The `argocd app create` command targets the same Argo CD URL and cluster identity checked
during preflight. Never silently substitute another cluster or a Kubernetes context.
