---
name: gitops-app-manager
description: >-
  Industrial-grade GitOps application onboarding and environment management platform for ArgoCD
  (Helm & Extras manifests via argocd-gitops-tpl-library) and Komodo (Docker Compose), with automated
  root app-of-apps bootstrapping, values scaffolding, safety review gates, and GitLab CI/CD delivery injection.
---

# GitOps Application & Environment Manager Skill

An industrial-grade platform engineering skill that automates the onboarding, scaffolding, lifecycle management, and CI/CD delivery integration for applications running under **ArgoCD** (Helm & raw Manifest "Extras" via `argocd-gitops-tpl-library`) and **Komodo** (Docker Compose stacks via `devops/ci-templates` (`deploy/gitops/`)).

---

## 1. Unified Command Suite & Operational Workflows

All GitOps operations are driven through the enterprise **`gitops`** command suite:

| # | Command | Mandatory Arguments | Unified Execution Pipeline |
|:---:|---|---|---|
| **1** | `gitops onboard helm` (or `gitops add helm`) | `<gitops_repo>` `<branch>` `<service_name>` `[chart_repo]` | **Helm App Onboarding**: Branch similarity validation $\rightarrow$ repo introspection $\rightarrow$ registers `.apps.<service>` with `enabled: false` $\rightarrow$ scaffolds `values/<service>.yaml` (128Mi memory, `/dev` image suffix, auto-computed route) $\rightarrow$ derives ArgoCD root and child apps $\rightarrow$ generates `.gitlab-ci.yml` delivery block $\rightarrow$ user review gate $\rightarrow$ enables and syncs. |
| **2** | `gitops onboard manifest` (or `gitops add manifest`) | `<gitops_repo>` `<branch>` `<service_name>` `<image>` | **Extras Manifest Onboarding**: Branch similarity validation $\rightarrow$ scaffolds `extras/manifests/<service>/deployment.yaml` (128Mi memory, non-root) $\rightarrow$ computes single ArgoCD extras app `{chartBase}-extras-{env}-{service}` $\rightarrow$ generates `.gitlab-ci.yml` delivery block $\rightarrow$ user review gate $\rightarrow$ syncs app. |
| **3** | `gitops onboard komodo` (or `gitops add komodo`) | `<gitops_repo>` `<branch>` `<service_name>` `<image>` | **Komodo Compose Onboarding**: Branch similarity validation $\rightarrow$ auto-computes stack `{product}-{env}` $\rightarrow$ appends service to `docker-compose.yml` $\rightarrow$ generates `.gitlab-ci.yml` delivery block with YQ path $\rightarrow$ user review gate $\rightarrow$ triggers Komodo stack redeploy. |
| **4** | `gitops bootstrap env` (or `gitops init env`) | `<product>` `<env>` `<gitops_repo>` `--type [argocd\|komodo]` | **Environment Bootstrap**: Full enterprise environment provisioning.<br>• **ArgoCD**: CLI check $\rightarrow$ remote branch check $\rightarrow$ clones from main $\rightarrow$ scaffolds GitOps directory layout (`Chart.yaml`, `values.yaml`, `.gitkeep`) $\rightarrow$ user review $\rightarrow$ Kubernetes context safety gate $\rightarrow$ applies root app.<br>• **Komodo**: CLI check $\rightarrow$ remote branch check $\rightarrow$ SSH VM verification $\rightarrow$ Docker install $\rightarrow$ Periphery agent container under `/opt/komodo-agent/docker-compose.yml` $\rightarrow$ onboarding key input $\rightarrow$ agent logs check $\rightarrow$ smoke nginx compose on port 80 $\rightarrow$ CLI sync & port 80 test. |
| **5** | `gitops bootstrap root` (or `gitops init root`) | `<gitops_repo>` `<branch>` `[server]` | **Root App-of-Apps Bootstrap**: Scaffolds and applies the ArgoCD root Application CR (`{product-prefix}-{env}-root`) after Kubernetes context safety verification. |
| **6** | `gitops audit` *(read-only)* | `<gitops_repo>` `[branch]` | **Compliance Audit**: Validates `Chart.yaml` naming, `values.yaml` apps structure, mandatory 128Mi memory limits, `/dev` suffixes, and route domains. |
| **7** | `gitops diff values` (or `gitops diff schema`) | `<source1>` `<source2>` `[--repo-path <path>]` | **Application Schema Diff**: Filters out Kubernetes boilerplate and diffs strictly application-level operational schema (`image`, `resources`, `autoscaling`/`hpa`, `replicaCount`, `routes`, `ingress`, `service`, `env`). Supports local files, git revisions (`branch:path`), or cross-branch comparisons. |

---

## 2. Mandatory Human-in-the-Loop Protocol (Strict Execution Gates)

> [!CRITICAL]
> **STRICT EXECUTION BARRIERS**:
> The agent MUST NOT scaffold, modify files, or execute syncs in a single silent turn.
> Every onboarding workflow MUST pass through the following 4 sequential gatekeepers:

```
┌─────────────────────────┐
│ Gatekeeper 1            │ ──> Target branch verification & similarity search
│ Branch & Repo Identity  │     (HALT if branch not found -> contact DevOps)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 2            │ ──> Repo introspection, domain discovery,
│ Introspection & Config  │     auto-computation of routes & app names (User Confirms)
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 3            │ ──> Scaffold with safety law: `enabled: false`,
│ Staged Commit & Review  │     128Mi memory, /dev suffix -> Send commit/diff link
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│ Gatekeeper 4            │ ──> User "go call" approval -> Remove `enabled: false`,
│ Activation & Sync       │     commit, execute CLI sync or generate Web UI links
└─────────────────────────┘
```

---

### 2.1. Gatekeeper 1: GitOps Repository & Branch Existence

1. **Inquire / Verify Arguments**:
   - `gitops_repo_url`: Target GitOps repository (cannot be guessed; user must confirm).
   - `branch`: Target environment branch, formatted as `{product-name}/{environment}` (e.g. `myapp/dev`, `myapp/qa`, `myapp/prod`).
2. **Branch Normalization & Similarity Search**:
   If the user specifies colloquial environment names (`development`, `develop`, `test`, `testing`, `stage`, `production`):
   - Normalize via alias dictionary (`development` $\rightarrow$ `dev`, `develop` $\rightarrow$ `dev`, `production` $\rightarrow$ `prod`).
   - Run similarity match against available remote branches:
     ```bash
     python3 skills/gitops-app-manager/scripts/gitops-helper.py --match-branch "<branch>" --available-branches <branch_list> --json
     ```
   - If a similar candidate is detected (e.g. user asks for `myapp/development` and repository contains `myapp/dev`):
     Ask the user: *"We detected existing branch `myapp/dev` in repository `<repo>`. Should we use `myapp/dev`?"*
3. **Hard Stop (Branch Missing)**:
   If the branch does not exist and no matching candidates exist:
   > [!CAUTION]
   > **HALT IMMEDIATELY**. Do not attempt to initialize or push a new branch.
   > Respond to user:
   > *"The target branch `<product>/<env>` does not exist in `<gitops_repo_url>`. An environment has not yet been initialized for this project. Please reach out to the DevOps team to initialize the environment."*

---

### 2.2. Gatekeeper 2: Introspection & Auto-Computation Confirmation

1. **Introspect Repository**:
   - Read `Chart.yaml` line 2 to extract the canonical `{chartBase}` (e.g., `acme-cloud-myapp`).
   - Scan all files in `values/*.yaml` to discover the active cluster domain (e.g., `contoso.com` in `values/docs.yaml`).
2. **Auto-Compute Configuration Parameters**:
   - **Environment URL**:
     If the microservice chart exposes routes, auto-compute:
     ```
     https://{hostname-based-on-chart}.{environment}.{domain}
     ```
     *(Example: `https://chat-frontend.dev.contoso.com`)*. If no routes are exposed, leave empty.
   - **ArgoCD Application Names**:
     - **For Helm (2 apps to sync)**:
       - Root App: `{chartBase}-{environment}-root` (e.g. `acme-cloud-myapp-dev-root`)
       - Child App: `{chartBase}-{service_name}-{environment}` (e.g. `acme-cloud-myapp-chat-frontend-dev`)
       - String argument: `acme-cloud-myapp-dev-root acme-cloud-myapp-chat-frontend-dev`
     - **For Manifest "Extras" (1 app only)**:
       - Extras App: `{chartBase}-extras-{environment}-{service_name}` (e.g. `acme-cloud-myapp-extras-dev-clamav`)
       - String argument: `acme-cloud-myapp-extras-dev-clamav`
     - **For Komodo**:
       - Stack Name: `{product-name}-{environment}` (e.g. `myapp-dev`)
3. **STOP AND PRESENT CONFIRMATION TABLE**:
   Present the auto-computed values to the user and request explicit confirmation before writing any changes:

   | Setting | Auto-Computed Value | User Confirmation Required |
   |---|---|---|
   | **GitOps Repo** | `<gitops_repo_url>` | Confirmed |
   | **GitOps Branch** | `<product>/<env>` | Verified in remote |
   | **Service Identity** | `<service_name>` | Target microservice |
   | **ArgoCD Apps to Sync** | `<root_app> <service_app>` | Derived from `Chart.yaml` line 2 |
   | **Public Route URL** | `https://<svc>.<env>.<domain>` | Auto-computed from cluster domain |
   | **YQ App Path** | `.apps.<service>` | Entry in `values.yaml` |
   | **Memory Baseline** | Requests: `128Mi`, Limits: `128Mi` | Mandatory default |
   | **Dev Image Suffix** | `/dev` (if environment is `dev`) | Enforced |

---

### 2.3. Gatekeeper 3: Staged Scaffolding & Disabled Review Gate

1. **Scaffold with Mandatory `enabled: false` Law**:
   - In root `values.yaml`, insert the service registration:
     ```yaml
     apps:
       chat:
         frontend:
           enabled: false  # MANDATORY: Initially disabled!
           chart:
             repoURL: oci://registry.contoso.com/devops/charts
             name: chat-frontend
             version: 0.1.0
     ```
   - In `values/<service>.yaml` (or `values/<group>/<service>.yaml`), scaffold:
     ```yaml
     image:
       repository: registry.contoso.com/myapp/chat-frontend/dev  # /dev suffix for dev

     resources:
       requests:
         cpu: 50m
         memory: 128Mi
       limits:
         cpu: 200m
         memory: 128Mi

     routes:
       enabled: true
       host: chat-frontend.dev.contoso.com
     ```
2. **Generate CI/CD Delivery Injection Snippet**:
   Provide the exact YAML block to be placed in the application repository's `.gitlab-ci.yml`.
3. **Commit & Send Review Link**:
   Commit the staged changes to the GitOps repository branch (or working branch) with message:
   `chore(gitops): register <service> (disabled) for <env> [skip ci]`
   Send the commit link or branch diff link to the user:
   > *"I have staged the service in `<branch>` with `enabled: false`. Please review the configuration here: `<commit_or_branch_url>`."*
4. **STOP AND WAIT**: Do NOT enable or sync until the user replies with approval ("go call").

---

### 2.4. Gatekeeper 4: Activation & Synchronization

Only AFTER the user gives approval ("go call", "approved", "looks good"):
1. **Remove `enabled: false`**:
   Update `values.yaml` to remove `enabled: false` (or set `enabled: true`).
2. **Commit & Push**:
   Commit with message: `chore(gitops): activate <service> in <env>` and push to GitOps branch.
3. **Execute Sync**:
   - **If ArgoCD CLI is available**:
     ```bash
     argocd app get "<root_app>" --hard-refresh --grpc-web
     argocd app sync "<root_app>" --grpc-web --async
     argocd app get "<service_app>" --hard-refresh --grpc-web
     argocd app sync "<service_app>" --grpc-web --async
     ```
   - **If ArgoCD CLI is NOT available**:
     Inform the user and output the exact Web UI sync links:
     - Root App: `https://<argocd_server>/applications/argo-cd/<root_app>?view=tree&resource=&orphaned=false`
     - Service App: `https://<argocd_server>/applications/argo-cd/<service_app>?view=tree&resource=&orphaned=false`

---

## 3. Step-by-Step Command Procedures

### 3.1. `gitops onboard helm`
```bash
# 1. Run similarity check & repo introspection
python3 skills/gitops-app-manager/scripts/gitops-helper.py --introspect /path/to/gitops/clone --json

# 2. Present configuration table to user and wait for approval.
# 3. Apply values.yaml update (enabled: false) and values/<service>.yaml.
# 4. Inject CI include block into microservice .gitlab-ci.yml.
# 5. Send commit link for review.
# 6. Upon 'go call', remove enabled: false, commit, and sync.
```

### 3.2. `gitops onboard manifest` ("Extras")
```bash
# 1. Create directory extras/manifests/<service_name>/
# 2. Scaffold production-grade manifest stack replicating helm-tpl-library:
#    - ServiceAccount (automountServiceAccountToken: false)
#    - ConfigMap & Secret (${service_name}-env injected via envFrom)
#    - Deployment:
#        * 10001:10001 Pod & Container SecurityContext (readOnlyRootFilesystem: true, drop ALL capabilities)
#        * /tmp volumeMount with emptyDir: {}
#        * Resources (128Mi requests/limits)
#        * LivenessProbe, ReadinessProbe, StartupProbe
#        * Downward API (POD_NAME, POD_NAMESPACE, POD_IP)
#        * TopologySpreadConstraints (kubernetes.io/hostname)
#    - Service (ClusterIP port 80 -> targetPort 8080)
#    - Ingress (Nginx with TLS termination & ssl-redirect)
#    - HTTPRoute (Gateway API with parentRefs to default-gateway)
#    - ServiceMonitor & PodMonitor (Prometheus operator metrics scraping on /metrics)
#    - HorizontalPodAutoscaler (Autoscaling v2 CPU/Memory metrics & scale behavior)
#    - PodDisruptionBudget (minAvailable: 1)
#    - NetworkPolicy (Ingress & Egress zero-trust isolation)
# 3. Compute single app name: {chartBase}-extras-{env}-{service_name}
# 4. Inject CI include block with gitops_manifest_file & gitops_new_image.
# 5. Send commit link for review.
# 6. Upon 'go call', sync single extras application.
```

### 3.3. `gitops onboard komodo`
```bash
# 1. Auto-compute stack: {product}-{env}
# 2. Append service to docker-compose.yml with restart and logging.
# 3. Inject CI include block with gitops_service_image_yq_path and komodo_stack_name.
# 4. Send review link to user.
# 5. Upon 'go call', commit and trigger Komodo redeployment.
```

### 3.4. `gitops bootstrap env` (Environment Provisioning Workflow)

Used when bootstrapping a brand-new environment after a developer reports an environment is missing or requested.

#### 3.4.1. Step 1: Inquire & Validate User Inputs
Prompt for the mandatory inputs:
- `product_name`: Core product identity (e.g. `myapp`).
- `environment`: Target environment (e.g. `dev`, `develop`, `qa`, `prod`).
- `gitops_repo_url`: GitOps repository URL (e.g. `https://gitlab.contoso.com/devops/gitops/acme-cloud.git`).
- `platform_endpoint`: ArgoCD Server URL (`https://argocd.contoso.com`) or Komodo Core URL (`https://komodo.contoso.com:8120`).

#### 3.4.2. Step 2: Strict Pre-Flight Validations (Endpoint & Repo Access)

1. **Platform Endpoint Reachability & Signature Validation**:
   - Run: `python3 skills/gitops-app-manager/scripts/gitops-helper.py --validate-endpoint "<platform_endpoint>" [argocd|komodo]`
   - **Reachability Check**: Probes network connectivity (detects timeouts, VPN requirements, connection refused, or DNS failures).
   - **Signature Verification**: Validates whether the target endpoint is genuinely an ArgoCD server or Komodo Core instance (inspects API endpoints, headers, and UI signatures).
   - If unreachable or if signatures do NOT match $\rightarrow$ **ABORT** immediately:
     > *"Endpoint `<url>` is unreachable or not a valid `<platform>` server. Please verify endpoint URL and VPN connectivity."*

2. **GitOps Repository Access & CLI Authentication (`gh` / `glab`)**:
   - Run: `python3 skills/gitops-app-manager/scripts/gitops-helper.py --validate-repo "<gitops_repo_url>"`
   - Confirms `gh` (GitHub) or `glab` (GitLab) is installed and actively authenticated (`auth status`).
   - Verifies the user's active credentials have real access permissions to that specific GitOps repository:
     - **401 Unauthorized** $\rightarrow$ **ABORT**: *"Authentication failed. Run `glab auth login` or `gh auth login`."*
     - **403 Forbidden** $\rightarrow$ **ABORT**: *"Access forbidden. Your account lacks permissions for `<gitops_repo_url>`."*
     - **404 Not Found** $\rightarrow$ **ABORT**: *"Repository endpoint not found. Please verify repository URL."*

3. **Remote Branch Check & Similarity Search**:
   - Run: `python3 skills/gitops-app-manager/scripts/gitops-helper.py --check-remote-branch "<gitops_repo_url>" "<product>/<env>"`
   - If branch already exists on remote $\rightarrow$ Prompt user: *"Branch `<product>/<env>` already exists. Do you wish to reconfigure or onboard services into this environment instead?"*

4. **Platform CLI Authentication & Repo Management Access**:
   - **For ArgoCD**:
     - Check: `python3 skills/gitops-app-manager/scripts/gitops-helper.py --check-argocd "<platform_endpoint>" --repo-url "<gitops_repo_url>"`
     - Verifies `argocd` CLI is present and authenticated against `<platform_endpoint>`.
     - Confirms ArgoCD has permissions to manage applications and access `<gitops_repo_url>`.
     - If unauthenticated or pointing to wrong context $\rightarrow$ **ABORT** and alert user to run `argocd login <endpoint>`.
   - **For Komodo**:
     - Check: `python3 skills/gitops-app-manager/scripts/gitops-helper.py --check-komodo "<platform_endpoint>" --repo-url "<gitops_repo_url>"`
     - Verifies `km` CLI is present and configured in `~/.config/komodo/komodo.cli.toml` with host matching `<platform_endpoint>`.
     - Confirms CLI has permissions to manage stacks and sync the GitOps repo.
     - If unauthenticated $\rightarrow$ **ABORT** and provide `km config` setup instructions.

---

#### 3.4.3. ArgoCD New Environment Initialization Flow
1. **Clone & Branch from `master`/`main`**:
   - Clone the GitOps repository locally.
   - Checkout a clean branch from `master` or `main`:
     ```bash
     git checkout -b {product}/{env} origin/main
     ```
2. **Scaffold Canonical Directory Structure (`argocd-gitops-tpl-library`)**:
   - Create the standard enterprise GitOps layout:
     ```
     {gitops-repo}/ (branch: {product}/{env})
     ├── Chart.yaml             # Initial root chart referencing argocd-gitops-tpl-library
     ├── values.yaml            # Environment base config with empty apps: {}
     ├── values/
     │   └── .gitkeep
     └── extras/
         └── manifests/
             └── .gitkeep
     ```
   - Render `Chart.yaml` and `values.yaml` from canonical assets:
     ```bash
     python3 skills/gitops-app-manager/scripts/gitops-helper.py --render-branch-scaffold {product} {env}
     ```
3. **User Review Gate for Directory Layout**:
   - Present the initialized GitOps structure and files to the user for explicit confirmation:
     > *"I have initialized the GitOps repository structure on branch `{product}/{env}` matching `argocd-gitops-tpl-library` standards. Please review the layout above."*
4. **Mandatory Kubernetes Context Safety Gate**:
   - Inspect active context via `python3 skills/gitops-app-manager/scripts/gitops-helper.py --check-context`.
   - Prompt user to confirm `kubeconfig` and the active context.
5. **Apply Root App-of-Apps**:
   - Render and apply `{product-prefix}-{env}-root` application CR to ArgoCD.
   - Verify ArgoCD recognizes the new root application.

---

#### 3.4.4. Komodo New Environment Initialization Flow (VM Docker Host)
Komodo manages containerized workloads across dedicated VMs running Docker.

1. **Target VM Connection Verification**:
   - Inquire target VM credentials: SSH Key or Password, VM IP Address, SSH Username (`root` or user with `sudo` privileges).
   - Test SSH connectivity before making changes:
     ```bash
     ssh -o BatchMode=yes -o ConnectTimeout=5 <user>@<vm-ip> "echo VM_CONNECTED"
     ```
   - If connection fails or credentials lack sudo privileges $\rightarrow$ **HALT** and notify user.
2. **Automated Docker Installation**:
   - Execute remote Docker installation:
     ```bash
     curl -fsSL https://get.docker.com | sudo sh
     sudo usermod -aG docker <username>
     ```
   - Verify Docker daemon health:
     ```bash
     docker ps && sudo docker ps
     ```
3. **Komodo Periphery Agent Deployment**:
   - Agent is installed as a container with host networking:
     Path: `/opt/komodo-agent/docker-compose.yml`
   - **Onboarding Key Instructions**:
     Instruct user to obtain or create an Onboarding Key:
     - **Via Komodo Core UI**: Navigate to **Settings → Onboarding** (or **Servers → Add Server**), click **Create Onboarding Key**, and copy the token.
     - **Via `km` CLI**: Run `km server new ...` to register the host.
     - Prompt user: *"Please paste your Komodo Core URL and Onboarding Key."*
   - Render agent compose:
     ```bash
     python3 skills/gitops-app-manager/scripts/gitops-helper.py \
       --render-komodo-agent "<core_url>" "<onboarding_key>" "<server_name>"
     ```
   - Deploy agent:
     ```bash
     sudo mkdir -p /opt/komodo-agent/config
     sudo docker compose -f /opt/komodo-agent/docker-compose.yml up -d
     ```
   - Verify agent logs (`docker compose -f /opt/komodo-agent/docker-compose.yml logs -f --tail 20`).
   - Confirm server status is **OK** in Komodo via `km` CLI or UI. Troubleshoot if connection is not established.
4. **GitOps Repository Setup & Smoke Test**:
   - In the GitOps repo on branch `{product}/{env}`:
   - Prepare a smoke-test `docker-compose.yml` running an `nginx` service on port 80:
     ```bash
     python3 skills/gitops-app-manager/scripts/gitops-helper.py --render-komodo-smoke {product} {env}
     ```
   - Commit and push to GitOps branch.
   - Sync stack using Komodo CLI (`km x commit ...`) or dashboard.
   - Test container access: `curl -I http://<vm-ip>:80` and prompt user to verify port 80.

---

### 3.5. `gitops bootstrap root` (Root App-of-Apps Only)

> [!CRITICAL]
> **MANDATORY KUBERNETES CONTEXT CONFIRMATION GATE**:
> BEFORE applying `root-app-template.yaml` (via `kubectl apply` or ArgoCD CLI), the agent MUST verify and confirm the active Kubernetes cluster context:
>
> 1. **Inspect Context**:
>    Run `python3 skills/gitops-app-manager/scripts/gitops-helper.py --check-context --json`
>    (or `kubectl config current-context` and `kubectl config get-contexts -o name`).
> 2. **Active Context Detected**:
>    The agent MUST STOP and explicitly ask the user:
>    > *"Detected active Kubernetes context: `[CURRENT_CONTEXT]`.
>    > Available contexts: `[AVAILABLE_CONTEXTS]`.
>    > Do you confirm applying the root application `[ROOT_APP_NAME]` to this cluster context?"*
> 3. **No Active Context (or kubectl unconfigured)**:
>    If no context is configured or `kubectl` fails:
>    The agent MUST STOP and ask the user:
>    > *"No active Kubernetes context is currently set.
>    > Please set your context using `kubectl config use-context <context>`, or specify your kubeconfig path (`KUBECONFIG=/path/to/kubeconfig`).
>    > Available contexts detected: `[CONTEXT_LIST]`.
>    > Which context should be used?"*
> 4. **STRICT LAW**: NEVER execute `kubectl apply` without explicit user confirmation of the target Kubernetes cluster context!

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {product-prefix}-{env}-root
  namespace: argo-cd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: developer
  source:
    repoURL: https://gitlab.contoso.com/devops/gitops/{repo}.git
    path: .
    targetRevision: {product}/{env}
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-cd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
      enabled: true
    syncOptions:
      - ApplyOutOfSyncOnly=true
      - RespectIgnoreDifferences=true
    retry:
      limit: 2
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m0s
```

---

> **Delivery job wiring.** Nothing deploys on an artifact that was never published, and no job
> waits on work it does not consume. The `needs:` contract for the injected deploy jobs — and why
> `optional: true` there means "may not exist in this pipeline", never "ignore its failure" — is in
> [`references/ci-templates-delivery-contract.md`](./references/ci-templates-delivery-contract.md) §3.

## 4. Template Library Compatibility (`argocd-gitops-tpl-library`)

This skill integrates with `argocd-gitops-tpl-library`.
- **Naming Law**: Root `Chart.yaml` line 2 establishes `{chartBase}` (e.g. `name: acme-cloud-myapp`).
- **Extras Naming Fix**: In `argocd-gitops-tpl-library/templates/argocd/_application.tpl`, `tpl.argocd.application.extras` automatically strips legacy `-extras.*` suffixes from `$.Chart.Name` using `regexReplaceAll "-extras(-.*)?$" $.Chart.Name ""` and derives `{chartBase}-extras-{env}-{dirName}` cleanly.
