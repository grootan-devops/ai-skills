# Platform Builder: Standard Engineer Prompts

This guide provides battle-tested, copy-pasteable prompts for engineers using the `platform-builder` skill. Use these prompts to ensure AI coding agents follow company platform standards, harvest all existing repository context, preserve intentional customizations during updates, and avoid common omissions.

---

## Table of Commands

| Command | Purpose | When to Use |
| :--- | :--- | :--- |
| **`platform onboard`** | Full-stack CI/CD, Dockerfile, & Helm scaffolding | Repositories with no CI/Helm or legacy/unstandardized setups. |
| **`platform update`** | Surgical migration & version bump | Upgrading library versions, applying `MIGRATION.md`, keeping custom configs. |
| **`platform ship`** | Environment deployment wiring | Wiring target environments (`dev`, `qa`, `staging`, `prod`) to GitOps/pipelines. |
| **`platform audit`** | Read-only compliance & security check | Checking parity, security, and standards compliance without editing code. |

---

## Core Engineering Directives (Embedded in All Prompts)

1. **Existing Asset Discovery & Harvesting**:
   - Always inspect and harvest existing configuration from:
     - `docker-compose.yml` / `compose.yaml` (services, exposed ports, environment variables, mounts, dependencies).
     - `.env`, `.env.*`, `.env.example`, `.env.production` (runtime configuration, secrets vs. configmap split).
     - Existing `Dockerfile` (runtime commands, multi-stage artifacts, entrypoints, user/port contracts).
     - Existing Helm charts / Kubernetes manifests (`Chart.yaml`, `values.yaml`, templates).
     - Existing CI configuration (`.gitlab-ci.yml` or `.github/workflows/*.yml`).
   - Translate all harvested context into our standard libraries (`gitlab-ci-library`, `github-ci-library`, `helm-tpl-library`).
2. **Surgical Diff-and-Confirm for Updates (`platform update`)**:
   - **Never reset to ground zero**. Do not overwrite files with fresh starter templates.
   - **Preserve deliberate customizations**: keep custom CI cache keys (e.g. `PROJECT_CACHE_KEY: "access"`), tailored `configmapEnvs`, `secretEnvs`, custom replicas, resource requests/limits, health probe paths, volume mounts, and custom CI jobs/rules.
   - Only apply breaking changes from `MIGRATION.md`, bump library reference tags, synchronize schema, and fix audit defects.
3. **Optional Resource Omission**:
   - Stateless workloads must **omit** `persistence:`, `cronjobs:`, `jobs:`, and `metrics:` by default from `values.yaml` unless explicitly required or detected in existing configurations.
4. **Dynamic Library Resolution**:
   - Always resolve library versions dynamically using `python3 core/scripts/libraries.py [repo] --platform <platform>`. Never hardcode static tags from README examples.

---

## 1. `platform onboard`

Use when onboarding a repository to enterprise CI/CD, packaging Dockerfiles, and Helm charts.

### Quick Copy-Paste Prompt (Terse)

```text
Run `platform onboard .`. First inspect any existing docker-compose.yml, .env*, Dockerfile, or legacy charts to harvest ports, env vars, mounts, and dependencies. Scaffold GitLab CI or GitHub Actions, packaging Dockerfile, and Helm chart strictly consuming gitlab-ci-library/github-ci-library and helm-tpl-library. Omit cronjobs, jobs, persistence, and metrics by default unless detected. Resolve library versions via core/scripts/libraries.py and validate with python3 core/scripts/audit.py . --strict.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Platform Engineer using the `platform-builder` skill.
Execute `platform onboard` for the target repository: `<repo-path>` (default: current directory `.`).

Follow these strict phases:

### Phase 1: Context & Existing Asset Harvesting
1. Detect the CI platform using `python3 core/scripts/platform.py <repo-path>`.
2. Inspect and parse all existing runtime and deployment artifacts in the repository:
   - `docker-compose.yml` / `compose.yaml`: extract exposed ports, environment variables, volume mount targets, container commands, and backing services (DB, Redis, MQ).
   - `.env`, `.env.*`, `.env.example`: extract all configuration keys. Classify non-sensitive settings into `configmapEnvs` and sensitive keys/credentials into `secretEnvs`.
   - Existing `Dockerfile`: inspect entrypoint, working directory, exposed port, and runtime binary. Convert to a packaging-only Dockerfile adhering to non-root UID 10001, single responsibility, and rootless best practices.
   - Legacy Helm charts or Kubernetes YAML: harvest existing ingress hosts, resource limits, probe endpoints, and replicas.
3. Dynamically resolve the shared library refs using:
   `python3 core/scripts/libraries.py <repo-path> --platform <platform>`
   Read the resolved library READMEs and linked guides. Never guess library pins or copy example tags from documentation.

### Phase 2: Confirmation & Scaffolding Plan
Present a clear pre-execution summary to the user before writing any files:
- Detected platform and confidence signals.
- Harvested configuration summary (ports, environment variables, dependencies, configmap vs secret classification).
- Exact library versions/refs to be pinned.
- Ask whether to enable smoke-tests (`ci_image_test.sh`) and chart unit tests (`tests/*_test.yaml`).
- Confirm that optional resources (`persistence`, `cronjobs`, `jobs`, `metrics`) remain omitted unless detected in harvested assets.

### Phase 3: File Generation
Scaffold standard artifacts strictly adhering to enterprise references:
1. **CI Pipeline** (`.gitlab-ci.yml` or `.github/workflows/`):
   - Include only the workflows and modules the repository actually executes (adaptive selection).
   - Pin the exact resolved library ref.
   - Follow least-privilege token permissions.
2. **Dockerfile** (`Dockerfile`, `.dockerignore`):
   - Packaging-only, non-root user `appuser` (UID/GID 10001).
   - No embedded secrets, compilers, or test dependencies in the final stage.
3. **Helm Chart** (`chart/`):
   - `Chart.yaml`: depend on `tpl-library` (`oci://registry-1.docker.io/grootantech/helm-tpl-library`) at the resolved ref.
   - `values.yaml`: populate `partOf`, `component`, container port, resources, health probes, `configmapEnvs`, and `secretEnvs` using harvested data.
   - `templates/manifest.yaml`: clean single-entrypoint deployment calling `{{- include "tpl.deployment" . }}`. Do not include optional snippets (`tpl.pvc`, `tpl.job`, `tpl.cronjob`, `tpl.servicemonitor`) unless explicitly required.
   - `values.schema.json` and `.helmignore`.

### Phase 4: Validation
Run strict verification:
`python3 core/scripts/audit.py <repo-path> --strict`
Ensure all P1 and P2 findings are resolved before completing.
```

---

## 2. `platform update`

Use when upgrading library versions, synchronizing schema changes, or migrating existing pipelines and Helm charts to current enterprise standards.

### Quick Copy-Paste Prompt (Terse)

```text
Run `platform update .`. Execute a SURGICAL update—do NOT reset or regenerate files from ground zero. Preserve all existing custom configurations: custom PROJECT_CACHE_KEY, custom configmapEnvs/secretEnvs, custom replicas, resources, probes, and user-added CI jobs. Resolve target library versions via core/scripts/libraries.py, read MIGRATION.md for gitlab-ci-library/github-ci-library and helm-tpl-library to apply only breaking changes, synchronize values.schema.json, and validate with python3 core/scripts/audit.py . --strict.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Platform Engineer using the `platform-builder` skill.
Execute `platform update` for the target repository: `<repo-path>` (default: current directory `.`).

Strictly adhere to the **Diff-and-Confirm Law** and surgical migration protocol. This is NOT an onboard operation.

### Core Non-Negotiable Directives:
1. **Zero Ground-Zero Re-Scaffolding**:
   - Never overwrite existing `.gitlab-ci.yml`, `.github/workflows/*.yml`, `chart/values.yaml`, or `Dockerfile` with blank starter templates.
   - Edits must be in-place, targeted, and minimal.
2. **Preserve User Customizations**:
   - **CI Variables**: If `.gitlab-ci.yml` has a custom `PROJECT_CACHE_KEY` (e.g., `"access"` or `"chat"`), DO NOT reset it to a default like `"java"` or `"node"`. Keep the user's key.
   - **Application Values**: Preserve all existing `configmapEnvs`, `secretEnvs`, custom resources, replicas, probe paths, volume mounts, ingress configs, and annotations in `values.yaml`.
   - **Pipeline Structure**: Preserve user-added CI stages, custom scripts, and tailored `rules:` or `if:` conditions.
   - **Optional Resources**: If `persistence:`, `jobs:`, `cronjobs:`, or `metrics:` are omitted in `values.yaml`, keep them omitted. If intentionally configured, keep their configuration intact.

### Step-by-Step Update Execution:
1. **Resolve Target Libraries**:
   - Run `python3 core/scripts/libraries.py <repo-path> --platform <platform>` to resolve the target library refs.
2. **Review MIGRATION.md**:
   - Inspect `MIGRATION.md` in the target libraries (`gitlab-ci-library`, `github-ci-library`, `helm-tpl-library`) between the current version and target version.
   - Identify mandatory breaking changes, deprecated fields, and required schema/include changes.
3. **Execute In-Place Updates**:
   - Bump library reference pins:
     - GitLab CI: update `include: ref:`
     - GitHub Actions: update `uses: ...@<ref>`
     - Helm: update `Chart.yaml` dependency version for `tpl-library`
   - Apply specific `MIGRATION.md` transformations.
   - Synchronize `values.schema.json` to the target version without removing custom application property definitions.
   - Update `.helmignore` or `.dockerignore` if standard rules have evolved.
4. **Diff & Verification**:
   - Review the git diff. Ensure every changed line is justified by a library bump or migration requirement.
   - Run compliance validation:
     `python3 core/scripts/audit.py <repo-path> --strict`
   - Present a concise diff and summary of applied changes to the user.
```

---

## 3. `platform ship`

Use when configuring environment-specific deployment wiring, GitOps release branches, or target deployment stages.

### Quick Copy-Paste Prompt (Terse)

```text
Run `platform ship . <env>`. Configure deployment wiring for target environment `<env>` (e.g. dev, qa, staging, prod). Update CI pipeline deployment stages and environment-specific Helm values overrides (e.g. values-<env>.yaml) following least-privilege security and GitOps conventions. Validate with python3 core/scripts/audit.py . --strict.
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Platform Engineer using the `platform-builder` skill.
Execute `platform ship` for the target repository: `<repo-path>` targeting environment: `<env>` (e.g. `dev`, `qa`, `staging`, `prod`).

Follow these execution steps:
1. **Analyze Current Configuration**:
   - Inspect existing CI pipeline (`.gitlab-ci.yml` or `.github/workflows/`) and Helm chart (`chart/`).
   - Identify existing environment patterns, branch protection triggers, and target deployment clusters.
2. **Configure Environment Overrides**:
   - If using environment-specific values files, create or update `chart/values-<env>.yaml` with environment-appropriate scaling, ingress hosts, and environment indicators.
   - Maintain strict separation: non-sensitive environment configs belong in `values-<env>.yaml` or configmaps; sensitive credentials must come from external secret stores or masked CI variables.
3. **Wire Deployment Pipeline**:
   - For GitLab CI: add or activate the matching deployment job for `<env>` (e.g., `WORKFLOW: deploy-<env>`, manual gates for `prod`, automatic triggers for `dev`).
   - For GitHub Actions: configure environment targets, deployment protection rules, and OIDC roles.
   - Ensure image tag propagation uses immutable tags (`CI_COMMIT_SHORT_SHA` / `github.sha`).
4. **Validate**:
   - Run `python3 core/scripts/audit.py <repo-path> --strict` to verify no security or parity regressions.
```

---

## 4. `platform audit`

Use when auditing a repository's CI pipeline, Dockerfile, and Helm chart for compliance, security baselines, and parity against enterprise standards without changing any code.

### Quick Copy-Paste Prompt (Terse)

```text
Run `platform audit .`. Perform a strict, read-only compliance and security audit of the CI pipeline, Dockerfile, and Helm chart. Check values parity, template includes, non-root container standards, and library pin compliance. Run python3 core/scripts/audit.py . --strict and output findings classified by P1 (blocking), P2 (advisory), and P3 (informational).
```

### Comprehensive Engineer Prompt (Full Guardrails)

```markdown
You are acting as a Platform Security & Reliability Auditor using the `platform-builder` skill.
Execute `platform audit` for the target repository: `<repo-path>` (default: current directory `.`).

Execute a strict, read-only audit adhering to the following rules:

### 1. Execution
1. Dynamically resolve the reference libraries the repository targets:
   `python3 core/scripts/libraries.py <repo-path>`
2. Execute the audit engine:
   `python3 core/scripts/audit.py <repo-path> --strict`
3. Cross-check against core standards:
   - **Security**: Rootless Dockerfile (`USER 10001`), no hardcoded tokens/secrets, least-privilege CI scopes.
   - **Helm Parity**: `values.yaml` parity against `templates/manifest.yaml` (ensure no orphan active blocks, correct `tpl.deployment` inclusion, no redundant `---` or colliding separators).
   - **Optional Resource Restraint**: Verify stateless workloads do not carry orphan `persistence:`, `jobs:`, `cronjobs:`, or `metrics:` sections.
   - **Ignore Files**: Verify `.helmignore`, `.dockerignore`, and `.gitignore` contracts.

### 2. Output Format
Present findings cleanly grouped by priority:
- **P1 (Critical / Blocker)**: Violations of security standards, broken includes, invalid YAML rendering, missing mandatory fields.
- **P2 (Advisory / Best Practice)**: Outdated library versions, unoptimized layers, recommended caching improvements.
- **P3 (Informational)**: Cleanup opportunities, documentation alignment.

Do NOT modify any files during this audit.
```

---

## 5. Platform-Specific Aliases

If your repository exclusively uses GitLab or GitHub, you can use platform-specific alias commands:

### GitLab
```bash
# Onboard
gitlab-platform onboard [repo]
# Update
gitlab-platform update [repo]
# Ship
gitlab-platform ship [repo] <env>
# Audit
gitlab-platform audit [repo]
```

### GitHub
```bash
# Onboard
github-platform onboard [repo]
# Update
github-platform update [repo]
# Ship
github-platform ship [repo] <env>
# Audit
github-platform audit [repo]
```
