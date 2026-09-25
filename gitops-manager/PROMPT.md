# GitOps Manager: Standard Engineer Prompts

This guide provides battle-tested, copy-pasteable prompts for engineers using the `gitops-manager` skill. Use these prompts to automate Argo CD GitOps environment bootstrap, multi-environment catalog updates, and cluster application syncs while strictly adhering to `argocd-gitops-tpl-library` standards.

---

## Table of Commands & Workflows

| Command / Workflow | Purpose | When to Use |
| :--- | :--- | :--- |
| **`argocd env add`** | Bootstrap new Argo CD GitOps environment | Creating a new project/environment branch and root Application. |
| **`argocd env update`** | Library bump & schema migration | Upgrading `argocd-gitops-tpl-library` version or updating sync configs. |
| **`argocd env audit`** | Read-only preflight & health check | Verifying cluster connection, GitOps branch health, and app sync state. |

---

## Core Engineering Directives (Embedded in All Prompts)

1. **Mandatory Input Preflight & Stop Conditions**:
   - Collect both **GitOps repository URL** and **Argo CD URL** before performing any operations. If either is missing, pause and prompt for them.
   - Collect the target project/application name and environment (`dev`, `qa`, `test`, `uat`, `pre-prod`, `prod`, or empty).
   - Abort if CLI authentication is missing (`argocd`, `git`, `helm`, `glab`/`gh`). Never prompt users to paste tokens or credentials into chat.
2. **Cluster Metadata Discovery**:
   - Inspect `master/README.md` in the GitOps repository. It must declare the explicit cluster name and Argo CD cluster name (e.g., `# Midgard (cluster name)` and `ArgoCD server name: in-cluster`).
   - Stop if `master/README.md` or cluster metadata is missing. Never guess cluster destination names.
3. **Strict Library Consumption (`argocd-gitops-tpl-library`)**:
   - Use starter files in `assets/bootstrap/` (`Chart.yaml`, `values.yaml`, `root-application.yaml`, `extras/`).
   - Pin the highest published non-prerelease SemVer release from `oci://registry-1.docker.io/grootantech/argocd-gitops-tpl-library`. Verify using `helm show chart`.
4. **Zero-Drift & Non-Destructive Operations**:
   - Check if target branch or root Application already exists. Never overwrite an existing branch or use `argocd app create --upsert` unless explicitly directed to perform an update.
   - Do not expose credentials or tokens in generated manifests or command outputs.

---

## 1. `argocd env add` (Environment Bootstrap)

Use when creating a new GitOps environment branch and deploying its Argo CD root Application.

### Quick Copy-Paste Prompt (Terse)

```text
Run `argocd env add` for project `<project>` in environment `<env>`. GitOps repo: `<gitops-repo-url>`, Argo CD URL: `<argocd-url>`. Check CLI authentication and read cluster metadata from `master/README.md`. Scaffolding from assets/bootstrap/ with argocd-gitops-tpl-library pinned to the latest stable OCI release. Validate with helm lint and helm template, push the branch `<project>/<env>`, create the root Application `<cluster>-<project>-<env>-root`, and verify Argo CD sync health.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a GitOps & Platform Engineer using the `gitops-manager` skill.
Bootstrap a new Argo CD environment for:
- **Project**: `<project>`
- **Environment**: `<env>` (e.g. `dev`, `qa`, `staging`, `prod`, or empty for single-env)
- **GitOps Repository URL**: `<gitops-repo-url>`
- **Argo CD URL**: `<argocd-url>`

Execute according to the strict preflight and bootstrap protocol:

### Phase 1: Preflight & Validation
1. Verify required CLIs are installed and authenticated: `argocd`, `git`, `helm`, and `glab`/`gh`.
   - Run `argocd version --server <argocd-url>` and `argocd account get-user-info --server <argocd-url>`.
   - Verify read access to `<gitops-repo-url>` using `git ls-remote`.
2. Inspect `README.md` on branch `master` of the GitOps repository:
   - Extract the cluster name and Argo CD cluster name (e.g. `# Midgard (cluster name)` and `ArgoCD server name: in-cluster`).
   - Verify the cluster exists in Argo CD using `argocd cluster list --server <argocd-url>`.
   - If `master/README.md` or cluster metadata is missing, STOP and report the requirement.
3. Check for collision:
   - Target branch: `<project>/<env>` (or `<project>` if environment is empty).
   - Root application name: `<cluster>-<project>-<env>-root`.
   - Ensure neither the branch nor the root Application already exists.
4. Pin Library Dependency:
   - Query the highest published stable release of `oci://registry-1.docker.io/grootantech/argocd-gitops-tpl-library` using `helm show chart`.
   - Pin that exact verified version.

### Phase 2: Branch & Manifest Scaffolding
1. Create a new branch `<project>/<env>` branched off `origin/master`.
2. Copy and populate starter files from `assets/bootstrap/`:
   - `Chart.yaml`: name `__PROJECT_NAME__` and pinned `argocd-gitops-tpl-library` dependency.
   - `values.yaml` and `values/`: project configuration, target revision, and environment values.
   - `root-application.yaml`: target namespace `argo-cd`, project `cluster-admin`, destination cluster name from metadata.
   - `extras/`: supplementary charts if required.
3. Do not include passwords, plaintext secrets, or tokens.

### Phase 3: Verification & Activation
1. Run Helm validation:
   - `helm dependency build .`
   - `helm lint .`
   - `helm template test-root .`
2. Push branch:
   - Commit files and push branch `<project>/<env>` to `<gitops-repo-url>`.
3. Create Root Application:
   - Apply `root-application.yaml` or run `argocd app create -f root-application.yaml --server <argocd-url>`.
4. Verification:
   - Check application status: `argocd app get <cluster>-<project>-<env>-root --server <argocd-url>`.
   - Confirm health status is `Healthy` or progressing normally without out-of-sync schema errors.
```

---

## 2. `argocd env update` (Environment Update & Sync)

Use when upgrading `argocd-gitops-tpl-library` version in consumer GitOps branches, applying breaking changes from `MIGRATION.md`, or updating environment sync configurations.

### Quick Copy-Paste Prompt (Terse)

```text
Run `argocd env update` on branch `<project>/<env>` in GitOps repo `<gitops-repo-url>`. Inspect MIGRATION.md of argocd-gitops-tpl-library for breaking changes. Bump library dependency version in Chart.yaml, update values structure to match the new schema, run helm lint and helm template to confirm zero regressions, commit changes, and trigger argocd app sync for root Application `<cluster>-<project>-<env>-root`.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a GitOps & Platform Engineer using the `gitops-manager` skill.
Update and modernize the existing GitOps branch: `<project>/<env>` in `<gitops-repo-url>`.

Follow these surgical update steps:
1. **Target Identification & Checkout**:
   - Check out the existing environment branch: `<project>/<env>`.
   - Read current dependency version from `Chart.yaml`.
2. **Library Resolution & Migration Review**:
   - Identify target release version for `argocd-gitops-tpl-library`.
   - Review `MIGRATION.md` for `argocd-gitops-tpl-library` between current and target versions.
   - Note any deprecated fields, renamed values keys, or required sync policy changes.
3. **Surgical Update**:
   - Update `version` under `dependencies` in `Chart.yaml`.
   - Update `values.yaml` and subcharts to comply with migration guidelines.
   - Preserve all existing environment application definitions, custom parameters, and repository URLs.
4. **Validation & Sync**:
   - Run `helm dependency update .`
   - Run `helm lint .` and `helm template .` to ensure error-free rendering.
   - Push updates to the branch.
   - Run `argocd app sync <cluster>-<project>-<env>-root --server <argocd-url>` and verify synchronization.
```

---

## 3. `argocd env audit` (Read-Only Health Check)

Use when auditing an existing GitOps environment branch, root Application status, and cluster synchronization health.

### Quick Copy-Paste Prompt (Terse)

```text
Run `argocd env audit` for `<project>/<env>` on Argo CD `<argocd-url>`. Check root application sync status, health status, git branch alignment, and verify that no plaintext secrets or unregistered repositories exist. Output findings with P1/P2/P3 severity without changing any remote state.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a GitOps Reliability Auditor using the `gitops-manager` skill.
Audit the health and compliance of environment `<project>/<env>` in `<gitops-repo-url>` on Argo CD `<argocd-url>`.

Perform the following non-destructive checks:
1. **Application Health & Drift**:
   - Check root Application status: `argocd app get <cluster>-<project>-<env>-root --server <argocd-url>`.
   - Check sync status (Synced vs. OutOfSync) and health status (Healthy, Progressing, Degraded).
   - Identify unmanaged resource drift or persistent sync errors.
2. **Configuration Compliance**:
   - Inspect `Chart.yaml`: ensure `argocd-gitops-tpl-library` is pinned to a valid, supported SemVer release.
   - Run `helm lint .` on the environment branch.
   - Ensure `.gitignore` and `.helmignore` prevent leaking local artifacts or secrets.
3. **Security Check**:
   - Verify that repository access uses registered Argo CD repository credentials and contains zero hardcoded tokens.
4. **Report**:
   - Summarize findings grouped by severity (P1 blockers, P2 drift/warnings, P3 recommendations).
```
