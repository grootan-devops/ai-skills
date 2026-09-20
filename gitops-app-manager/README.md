# GitOps Application & Environment Manager AI Skill (`gitops-app-manager`)

An industrial-grade platform engineering skill that automates the onboarding, scaffolding, lifecycle management, and CI/CD delivery integration for applications running under **ArgoCD** (Helm & raw Manifest "Extras" via `argocd-gitops-tpl-library`) and **Komodo** (Docker Compose stacks via `devops/ci-templates` (`deploy/gitops/`)).

---

## 1. Quick Start & Command Suite

All GitOps operations are driven through the enterprise **`gitops`** command suite:

```bash
# 1. Bootstrap a brand-new environment (ArgoCD or Komodo)
gitops bootstrap env <product> <env> <gitops_repo> --type [argocd|komodo]

# 2. Onboard a Helm microservice into an existing GitOps environment branch
gitops onboard helm <gitops_repo> <branch> <service_name> [chart_repo]

# 3. Onboard a raw Kubernetes manifest service ("Extras")
gitops onboard manifest <gitops_repo> <branch> <service_name> <image>

# 4. Onboard a Docker Compose service into Komodo GitOps
gitops onboard komodo <gitops_repo> <branch> <service_name> <image>

# 5. Scaffold the ArgoCD Root App-of-Apps bootstrap Application CR
gitops bootstrap root <gitops_repo> <branch> [server]

# 6. Read-only compliance audit for GitOps repository branch
gitops audit <gitops_repo> [branch]

# 7. Diff application schema (image, resources, hpa, routes, env) across files or branches
gitops diff values <source1> <source2> [--repo-path <path>]
```

---

## 2. Core Architecture & Naming Conventions

### 2.1. Environment Branches

GitOps branches are strictly structured as:

```text
{product-name}/{environment}
```

*Examples*: `myapp/dev`, `myapp/qa`, `myapp/prod`, `acme/vpn`.

The skill includes built-in alias normalization and similarity searching:

- `dev` $\leftarrow$ `dev`, `development`, `develop`
- `qa` $\leftarrow$ `qa`, `test`, `testing`
- `prod` $\leftarrow$ `prod`, `production`

If a branch does not exist in remote and cannot be resolved, the skill halts immediately and instructs the user to contact DevOps.

### 2.2. ArgoCD Application Naming

| Type | Application Name Formula | CI/CD Sync Target (`argocd_apps`) | Example |
|---|---|---|---|
| **Root App** | `{chartBase}-{env}-root` | `acme-cloud-myapp-dev-root` | `acme-cloud-myapp-dev-root` |
| **Helm Microservice** | `{chartBase}-{service}-{env}` | `{root_app} {service_app}` (2 apps!) | `acme-cloud-myapp-dev-root acme-cloud-myapp-chat-frontend-dev` |
| **Manifest Extras** | `{chartBase}-extras-{env}-{service}` | `{service_app}` (1 app only!) | `acme-cloud-myapp-extras-dev-clamav` |
| **Komodo Stack** | `{product}-{env}` | N/A (redeployed via stack API) | `myapp-dev` |

---

## 3. End-to-End Walkthrough: Adding a New Environment

When bootstrapping a new environment (`gitops bootstrap env`), the skill runs through strict pre-flight validation gates before provisioning.

### 3.1. Inputs & Derivation Matrix

| Parameter | Type | Source | Mandatory? | Derivation & Pre-Flight Validation Logic | Confirmation Required? |
|---|---|---|:---:|---|:---:|
| **`product_name`** | String | **User** | **YES** | Core product name (e.g. `myapp`). | **Yes** |
| **`environment`** | String | **User** | **YES** | Target environment (e.g. `dev`, `develop`, `qa`, `prod`). | **Yes** |
| **`canonical_env`**| String | **Skill** | *Auto* | Normalized: `development`/`develop` $\rightarrow$ `dev`, `production` $\rightarrow$ `prod`. | Auto-resolved |
| **`gitops_repo_url`** | String | **User** | **YES** | GitOps repository URL (e.g. `https://gitlab.contoso.com/devops/gitops/acme-cloud.git`). | **Yes** |
| **`gitops_branch`** | String | **Skill** | *Auto* | Formed as `{product_name}/{canonical_env}` (e.g. `myapp/dev`). | **Yes** |
| **`platform_type`** | Enum | **User** | **YES** | `argocd` (Kubernetes) or `komodo` (Docker on VMs). | **Yes** |
| **`platform_endpoint`**| String | **User** | **YES** | **Pre-Flight Verified**: Probe reachability over network + verify signature (ArgoCD API or Komodo Core UI/API). | **MANDATORY** |
| **`repo_access`** | CLI Auth | **Skill** | *Auto* | **Pre-Flight Verified**: `gh`/`glab` presence + active auth + read/access verification to `gitops_repo_url`. | **MANDATORY** |
| **`cli_auth`** | CLI Auth | **Skill** | *Auto* | **Pre-Flight Verified**: `argocd` or `km` CLI must be authenticated against `platform_endpoint` and authorized for `gitops_repo_url`. | **MANDATORY** |
| **`root_app_name`** | String | **Skill** | *Auto* | **Formula**: `{product_name}-{canonical_env}-root` (e.g. `myapp-dev-root`). | Displayed in table |
| **`komodo_stack_name`**| String | **Skill** | *Auto* | **Formula**: `{product_name}-{canonical_env}` (e.g. `myapp-dev`). | Displayed in table |
| **`vm_connection`** | Host/Creds| **User** | **YES (Komodo)** | VM IP, SSH user, key/password (must have `sudo`/`root` access). Tested via SSH before proceeding. | **Yes** |
| **`onboarding_key`** | Secret | **User** | **YES (Komodo)** | Periphery onboarding key (generated from Komodo Core UI or `km` CLI). | **Yes** |
| **`active_kube_context`**| Context | **System** | **YES (ArgoCD)** | Active cluster context from `kubectl config current-context`. | **MANDATORY** |

### 3.2. Pre-Flight Verification Gates

1. **Platform Endpoint Reachability & Signature Probe**:
   - Probes `platform_endpoint` over HTTP/HTTPS with timeout detection.
   - Verifies whether the endpoint returns valid ArgoCD or Komodo Core signatures.
   - **ABORTS** on timeout, DNS failure, connection refused, or signature mismatch.
2. **GitOps Repository Access (`gh` / `glab`)**:
   - Verifies `gh` (GitHub) or `glab` (GitLab) is installed and authenticated.
   - Tests read/access permissions to `gitops_repo_url`.
   - **ABORTS** on 401 Unauthorized, 403 Forbidden, or 404 Not Found.
3. **Platform CLI Authentication & Repo Authorization**:
   - **For ArgoCD**: Checks if `argocd` CLI is authenticated against `platform_endpoint` and has permissions to access the GitOps repo.
   - **For Komodo**: Checks if `km` CLI is configured in `~/.config/komodo/komodo.cli.toml` matching `platform_endpoint`.
4. **Remote Branch Check**:
   - Queries remote branches via `gh`/`glab`.
   - If the branch exists, prompts user if they want to onboard services instead.

### 3.3. ArgoCD New Environment Workflow

1. **Clone & Branch from `origin/main`**: Clones repo and checks out `{product}/{env}`.
2. **Scaffold Directory Layout**: Scaffolds `Chart.yaml` (referencing `argocd-gitops-tpl-library`), `values.yaml` (apps: {}), `values/.gitkeep`, and `extras/manifests/.gitkeep`.
3. **User Review Gate**: Displays initialized structure and file contents for user sign-off.
4. **Kubernetes Context Safety Gate**: Inspects `kubectl config current-context` and prompts user to confirm the active cluster before applying.
5. **Apply Root App-of-Apps**: Renders and applies `{product-prefix}-{env}-root` Application CR.

### 3.4. Komodo New Environment Workflow

1. **Target VM SSH Connection Verification**: Collects VM IP, user, and credentials; tests SSH and `sudo` access.
2. **Automated Docker Engine Installation**: Runs `curl -fsSL https://get.docker.com | sudo sh` and adds user to `docker` group. Verifies daemon.
3. **Komodo Periphery Agent Deployment**:
   - Prompts user to generate an Onboarding Key via Komodo Core UI (Settings $\rightarrow$ Onboarding) or `km` CLI.
   - Renders `/opt/komodo-agent/docker-compose.yml` and launches the agent (`docker compose up -d`).
   - Checks agent logs and verifies server status is **OK** in Komodo Core.
4. **GitOps Compose Setup & Smoke Test**:
   - Scaffolds a basic `docker-compose.yml` with an Nginx service on port 80.
   - Syncs stack via `km` CLI and verifies port 80 (`curl -I http://<vm-ip>:80`).

---

## 4. Flow Diagram: New Environment Addition

```mermaid
flowchart TD
    StartEnv([User Requests New Environment]) --> Inputs[Collect: product_name, env, gitops_repo_url, platform_endpoint]
    Inputs --> Norm[Normalize Environment: develop -> dev, prod -> prod]

    %% Gate 1: Endpoint Probe
    Norm --> ProbeEndpoint[Gate 1: Probe platform_endpoint<br/>gitops-helper --validate-endpoint]
    ProbeEndpoint --> EndpointCheck{Reachable & Valid Signature?}
    EndpointCheck -- Unreachable / Timeout / Invalid --> AbortEndpoint[ABORT: Show Endpoint Error & VPN Warning]
    
    %% Gate 2: Repo Access Check
    EndpointCheck -- Valid & Reachable --> CheckRepoAccess[Gate 2: Verify Repo Access<br/>gitops-helper --validate-repo]
    CheckRepoAccess --> RepoAccessCheck{gh/glab Authenticated & Access OK?}
    RepoAccessCheck -- 401 / 403 / 404 / CLI Missing --> AbortRepo[ABORT: Show Exact Auth/Permission Error]

    %% Gate 3: Platform CLI Auth Check
    RepoAccessCheck -- Access Verified --> CheckPlatformCLI{Target Platform?}
    CheckPlatformCLI -- ArgoCD --> VerifyArgoCLI[Gate 3: Verify argocd CLI Context & Repo Access]
    CheckPlatformCLI -- Komodo --> VerifyKmCLI[Gate 3: Verify km CLI Host in ~/.config/komodo]
    
    VerifyArgoCLI --> ArgoCLICheck{argocd CLI Authenticated?}
    ArgoCLICheck -- No / Wrong Context --> AbortArgoCLI[ABORT: Prompt to run argocd login]
    
    VerifyKmCLI --> KmCLICheck{km CLI Configured?}
    KmCLICheck -- No / Wrong Host --> AbortKmCLI[ABORT: Prompt to configure km CLI]

    %% ArgoCD Scaffolding Path
    ArgoCLICheck -- Authenticated --> GitClone[Clone Repo & Checkout -b product/env from origin/main]
    GitClone --> ScaffoldLayout[Render Branch Layout:<br/>1. Chart.yaml<br/>2. values.yaml<br/>3. values/.gitkeep<br/>4. extras/manifests/.gitkeep]
    ScaffoldLayout --> ReviewLayout{User Reviews Directory Layout?}
    ReviewLayout -- Approved --> CheckKubeCtx[Execute gitops-helper --check-context]
    CheckKubeCtx --> KubeCtxGate{User Confirms Active Context matches Environment?}
    KubeCtxGate -- Confirmed --> ApplyRootApp[Apply root-app-template.yaml<br/>kubectl apply -f {product}-{env}-root]
    ApplyRootApp --> ArgoEnvLive([ArgoCD Environment Live & Watching Branch])

    %% Komodo Scaffolding Path
    KmCLICheck -- Configured --> VMConnect[Verify Target VM Connection via SSH & Sudo Access]
    VMConnect --> InstallDocker[Run: curl -fsSL get.docker.com | sudo sh<br/>sudo usermod -aG docker user]
    InstallDocker --> OnboardingKeyPrompt[Provide UI/CLI Instructions for Onboarding Key<br/>User Pastes Onboarding Key]
    OnboardingKeyPrompt --> DeployAgent[Render & Deploy /opt/komodo-agent/docker-compose.yml<br/>docker compose up -d]
    DeployAgent --> VerifyAgent[Check Agent Container Logs & Confirm OK in Komodo CLI]
    VerifyAgent --> ScaffoldSmoke[Scaffold GitOps repo: smoke nginx on port 80]
    ScaffoldSmoke --> SyncSmoke[Sync via km CLI & Test: curl http://vm-ip:80]
    SyncSmoke --> KomodoEnvLive([Komodo Environment Live])
```

---

## 5. Flow Diagram: Service Onboarding to Existing Environment

```mermaid
flowchart TD
    StartSvc([User Requests Service Onboarding]) --> SvcInputs[Collect: gitops_repo, branch, service_name, type]
    SvcInputs --> SvcType{Service Type?}
    
    %% ArgoCD Helm Microservice
    SvcType -- Helm Chart --> IntrospectRepo[Read Chart.yaml line 2 -> chartBase<br/>Scan values/ -> Discover Base Domain]
    IntrospectRepo --> ComputeHelm[Auto-Compute:<br/>• Route: svc.env.domain<br/>• Image: repo/dev (if dev)<br/>• 2 Sync Apps: root + child]
    ComputeHelm --> ConfirmTable[Present Confirmation Table to User]
    ConfirmTable --> UserConfirm{User Confirms?}
    UserConfirm -- Yes --> ScaffoldHelm[1. values.yaml: Add .apps.svc with enabled: false<br/>2. values/svc.yaml: 128Mi memory, /dev suffix, route host]
    ScaffoldHelm --> InjectHelmCI[Generate .argocd.gitlab-ci.yml Delivery Snippet]
    InjectHelmCI --> CommitStaged[Commit with skip-ci & Send Diff Link to User]
    CommitStaged --> ReviewGate{User Gives 'Go Call'?}
    ReviewGate -- Yes --> ActivateHelm[Remove enabled: false & Sync ArgoCD Apps]
    ActivateHelm --> DoneHelm([Helm Service Live])

    %% ArgoCD Manifest Extras
    SvcType -- Manifest Extras --> DeriveExtras[Derive Single App: chartBase-extras-env-svc<br/>Manifest: extras/manifests/svc/deployment.yaml]
    DeriveExtras --> RenderExtras[Render assets/extras-deployment-template.yaml<br/>Complete 12-Resource Replica of helm-tpl-library]
    RenderExtras --> InjectExtrasCI[Generate .argocd.gitlab-ci.yml with gitops_manifest_file]
    InjectExtrasCI --> CommitExtras[Commit Manifest with skip-ci]
    CommitExtras --> SyncExtras[Sync ArgoCD Extras App]
    SyncExtras --> DoneExtras([Extras Service Live])

    %% Komodo Compose
    SvcType -- Komodo Compose --> DeriveKomodo[Derive Stack: product-env<br/>YQ Path: .services.svc.image]
    DeriveKomodo --> AppendCompose[Append Service to docker-compose.yml<br/>128M limit, healthchecks, networks]
    AppendCompose --> InjectKomodoCI[Generate .komodo.gitlab-ci.yml Delivery Snippet]
    InjectKomodoCI --> RedeployKomodo[Trigger Komodo Stack Redeploy via Webhook/CLI]
    RedeployKomodo --> DoneKomodo([Komodo Service Live])
```

---

## 6. Strict Human-in-the-Loop Protocol (The 4 Gatekeepers)

```text
┌─────────────────────────┐
│ Gatekeeper 1            │ ──> Branch existence & similarity search
│ Branch Identity         │     (HALT if branch missing -> reach out to DevOps)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 2            │ ──> Repo introspection & domain discovery
│ Config Confirmation     │     (Auto-compute route, app names -> User Confirms)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 3            │ ──> Scaffold with safety law: `enabled: false`,
│ Staged Review           │     128Mi memory, /dev suffix -> Send commit/diff link
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 4            │ ──> User "go call" approval -> Remove `enabled: false`,
│ Activation & Sync       │     commit, execute CLI sync or generate Web UI links
└─────────────────────────┘
```

---

## 7. Application Schema Diff Engine (`gitops diff values`)

Filters out Kubernetes metadata boilerplate and diffs strictly application-level operational schema (`image`, `resources`, `autoscaling`/`hpa`, `replicaCount`, `routes`, `ingress`, `service`, `env`).

```bash
# Compare local chart values vs GitOps branch values:
python3 skills/gitops-app-manager/scripts/gitops-helper.py --diff-values \
  values.yaml \
  origin/myapp/dev:values/chat-frontend.yaml \
  --repo-path /path/to/gitops-repo

# Compare across two git branches directly:
python3 skills/gitops-app-manager/scripts/gitops-helper.py --diff-values \
  origin/main:values.yaml \
  origin/myapp/dev:values/chat-frontend.yaml \
  --repo-path /path/to/gitops-repo
```

---

## 8. Automated Engine CLI Reference (`scripts/gitops-helper.py`)

```bash
# Run self-tests
python3 scripts/gitops-helper.py --test

# Check installed platform CLIs
python3 scripts/gitops-helper.py --check-clis

# Validate platform endpoint reachability & signature
python3 scripts/gitops-helper.py --validate-endpoint https://argocd.contoso.com argocd

# Validate repository access permissions via gh/glab
python3 skills/gitops-app-manager/scripts/gitops-helper.py --validate-repo https://gitlab.contoso.com/devops/gitops/acme-cloud.git

# Verify active Kubernetes context
python3 scripts/gitops-helper.py --check-context --json

# Render branch scaffold files (Chart.yaml & values.yaml)
python3 scripts/gitops-helper.py --render-branch-scaffold myapp dev

# Render Komodo Periphery agent compose
python3 scripts/gitops-helper.py --render-komodo-agent "wss://komodo.contoso.com:8120" "key_xxx" "node1"

# Render Komodo smoke-test Nginx compose
python3 scripts/gitops-helper.py --render-komodo-smoke myapp dev
```
