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
3. Read `README.md` from the GitOps repository's default branch. It must contain an explicit
   cluster name and Argo CD cluster name, for example:

   ```yaml
   # Midgard (cluster name)
   ArgoCD server name: in-cluster
   ```

   The first value is the cluster name used in generated Application names; the second must
   match the Argo CD cluster `NAME` shown by `argocd cluster list --server <ARGOCD_URL>`.
   Stop local preparation if that README or either value is missing. Check the cluster entry
   before activation. Do not
   infer these values from the URL, branch, or a different README.
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

When creating a new local branch, base it on the repository's default branch. In the example README block, treat
the text in `# Midgard (cluster name)` as the cluster label. Normalize a human label such as
`Midgard` to a DNS-safe value such as `midgard` only after showing the mapping to the user;
use the README's Argo CD cluster name exactly. If the user does not confirm a required
normalization, stop.

## Derived configuration

Use the cluster name and Argo CD cluster name from the default-branch README, plus the user-provided
project, environment, Git URL, and optional branch. The root and extras chart names are the
same project name. Use `developer` as the consumer-chart Argo CD Project, `cluster-admin` for
the bootstrap root Application, and `argo-cd` for the namespace containing Application CRs.

The generated chart branch defaults to `<project>/<environment>` (or `<project>` when there
is no environment). Set `branch` in both values files only when the user supplied a different
revision; otherwise leave it empty so the library derives `<Chart.Name>/<environment>` or
`<Chart.Name>`. The root Application always targets the actual branch created.

The root Application name is:

```text
<cluster>-<project>-<environment>-root
<cluster>-<project>-root                 # when environment is empty
```

In an Argo CD `Application`, the README's Argo CD cluster **name** maps to
`spec.destination.name`, not `spec.destination.server` (which expects a server URL).

## Consumer repository scaffold

Copy and fill the starter files under `assets/bootstrap/`. Keep this layout:

```text
.
├── .gitignore
├── .helmignore
├── Chart.yaml
├── README.md
├── values.yaml
├── templates/apps.yaml
├── values/.gitkeep
└── extras/
    ├── .helmignore
    ├── Chart.yaml
    ├── values.yaml
    ├── templates/apps.yaml
    └── manifests/.gitkeep
```

Add future workload manifests only below a named directory such as
`extras/manifests/clamav/`. The `extras/` chart has the same `Chart.yaml` name as the root
chart, depends on the same library release, and carries its own `.Values.extras` defaults
and per-directory `.Values.apps` overrides. Never put workload YAML directly in the root
chart or at `extras/manifests/` itself. Keep `renderExtrasManifests: false`: direct rendering
from the root chart is rejected; each named directory is reconciled by a generated child
Application.

The generated README records the environment, Argo CD URL, project, repository, branch,
cluster/server names, and derived root Application name. It must not contain credentials.

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

## Root Application contract

Use `assets/bootstrap/root-application.yaml` as the basis. Set the repository URL and actual
branch, project `cluster-admin`, source path `.`, destination cluster **name** from the
repository README, and namespace `argo-cd`. Keep the requested sync options and retry policy:

```yaml
syncOptions:
  - Validate=true
  - CreateNamespace=false
  - PrunePropagationPolicy=foreground
  - PruneLast=true
  - RespectIgnoreDifferences=true
  - ApplyOutOfSyncOnly=true
retry:
  limit: 2
  backoff:
    duration: 5s
    factor: 2
    maxDuration: 3m0s
```

The `argocd app create` command must target the same Argo CD URL and cluster identity checked
during preflight. Do not silently substitute another cluster or a Kubernetes context.
