# Argo CD Environment Bootstrap

Use this reference for `argocd env add`, local preparation, and explicitly requested
activation of the root Application that watches a new GitOps branch.

## Inputs and stop conditions

Before preparing files, obtain both URLs:

- GitOps repository URL (the repository Argo CD will watch).
- Argo CD URL (the API/UI endpoint).

If either URL is absent, ask and pause. Collect the project/application name and
environment. Suggest `dev`, `qa`, `test`, `uat`, `pre-prod`, and `prod`; an explicitly empty
environment is allowed and removes the `environment` key from both values files. Ask for an
optional target branch override; otherwise use `<project>/<environment>`, or `<project>` when
the environment is empty. Validate the project and branch as Git/Helm-safe names and show the
resolved names before writing.

Stop the affected step if its CLI, authentication, URL, repository access, destination cluster,
or required metadata is unavailable. Do not claim activation is ready when a remote preflight
has not passed. Never ask users to paste passwords or tokens into chat.

## Preflight

1. Check `git` and Helm for local preparation; check `argocd` and the matching Git host CLI
   only before a remote step that needs them. Use `glab` for GitLab and `gh` for GitHub when
   available. Verify the repository URL with `git ls-remote` or a read-only clone. If it requires
   credentials and the existing CLI/git credential helper cannot access it, stop.
2. Before activation, verify the Argo CD endpoint and logged-in identity with the installed CLI, for example
   `argocd version --server <ARGOCD_URL>` and
   `argocd account get-user-info --server <ARGOCD_URL>`. Use the CLI's supported TLS/gRPC
   flags when required by the deployment. Do not print tokens or credential-bearing URLs.
3. Read `README.md` from the GitOps repository's default branch. It must record the cluster
   label and the Argo CD cluster name in the format the library documents for the root
   Application. The cluster label is used in generated Application names; the Argo CD cluster name
   must match the `NAME` shown by `argocd cluster list --server <ARGOCD_URL>`. Stop local
   preparation if that README or either value is missing, and check the cluster entry before
   activation. Do not infer these values from the URL, branch, or a different README.
4. Before activation, check that the Argo CD URL is reachable and authenticated, and that the GitOps repository
   URL is valid and readable by the current Git identity. Confirm Argo CD can use the
   repository (including its registered credentials/access); if its repository status is
   missing or failed, stop and report the exact repository setup required.
5. Resolve the library documentation source using the source-selection rules in `SKILL.md`.
   Read the library README index and the linked bootstrap/configuration/extras pages. Read
   migration notes when updating an existing environment. For the dependency pin, identify
   the highest non-prerelease SemVer release from the library's published GitHub releases/tags,
   then verify that exact version:

   ```sh
   helm show chart oci://registry-1.docker.io/grootantech/argocd-gitops-tpl-library --version <version>
   ```

   Do not use an unreleased branch's `Chart.yaml` version as a published dependency. If the
   release or OCI artifact cannot be verified, stop and ask rather than writing a guessed pin.
6. Check whether the target branch exists before local creation; check whether the root
   Application exists before activation. Do not
   overwrite an existing branch, change an existing app, or use `argocd app create --upsert`
   unless the user specifically asks to update that existing environment.

When creating a new local branch, base it on the repository's default branch. Normalize a
human cluster label to a DNS-safe value only after showing the mapping to the user; use the
README's Argo CD cluster name exactly. If the user does not confirm a required normalization,
stop.

## Derived configuration and files

Take the cluster label and Argo CD cluster name from the default-branch README, plus the
user's project, environment, Git URL and optional branch. The naming, branch defaults, layout,
starter files and root Application manifest (Argo CD projects, `argo-cd` namespace, sync
options, destination by name) are in the library docs at the resolved ref — follow the README
index tasks for bootstrapping an environment and the root Application exactly. Keep credentials out of
every file, the environment README included.

## Validate locally; activate only when requested

1. Build both dependency trees and validate both charts. Run `helm lint --strict` and
   `helm template` for `.` and `./extras`, using the configured OCI registry access. Confirm
   the root chart emits only its child Applications and that the extras chart emits one
   Application per named manifest directory. Confirm default branch, namespace, app names,
   project, repo URL, server name, and sync/retry settings against the README and user inputs.
2. Show the resolved branch, changed-file list, generated README/root-app name, and validation
   results. Stop here for a request limited to `argocd env add` or `update`; those commands
   authorize local edits and checks only.
3. If explicitly requested, commit and push the validated scaffold. Do not push credentials
   or generated dependency artifacts that the repository ignores.
4. If explicitly requested, create the root Application only after confirming its target
   branch is already available to Argo CD. A request to create does not itself authorize
   pushing a missing branch. Create from the rendered manifest with `argocd app create -f
   <root-application.yaml> --server <ARGOCD_URL>`. Do not use `--upsert`. The manifest sets
   automated sync (`enabled`, prune, and self-heal), so this step may deploy workloads.
5. Read the created Application with `argocd app get <name> --server <ARGOCD_URL>` and report
   its sync/health status and branch/commit. If creation or sync fails, stop; do not repeatedly
   retry or change credentials/configuration without user direction.

The `argocd app create` command targets the same Argo CD URL and cluster identity checked
during preflight. Never silently substitute another cluster or a Kubernetes context.
