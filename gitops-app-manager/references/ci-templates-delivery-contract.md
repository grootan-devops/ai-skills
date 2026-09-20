# CI Templates GitOps Delivery Contract

This document provides the operational reference for injecting GitOps deployment jobs into `.gitlab-ci.yml` via the shared enterprise template library (`devops/ci-templates`).

---

## 1. ArgoCD GitOps Template: `deploy/gitops/.argocd.gitlab-ci.yml`

### 1.1. Helm-Based Deployment Schema
Used for services that package a Helm chart and deploy via GitOps chart version updates.

```yaml
include:
  - project: 'devops/ci-templates'
    ref: 2.0.0/dev
    file: deploy/gitops/.argocd.gitlab-ci.yml
    inputs:
      environment: dev                                      # (required, e.g. dev, qa, prod)
      gitops_repo_url: https://gitlab.contoso.com/gitops.git    # (required)
      gitops_branch: myapp/dev                             # (required, e.g. {product}/{env})
      gitops_chart_values_file: values.yaml                 # (required for Helm)
      gitops_chart_app_yq_path: .apps.chat.frontend         # (mandatory when chart_values_file is set)
      argocd_apps: acme-cloud-myapp-dev-root acme-cloud-myapp-chat-frontend-dev # (required: 2 apps!)
      environment_url: https://chat.dev.contoso.com             # (optional, auto-computed if routes exist)
      # Server & token default to CI/CD variables (ARGOCD_SERVER, ARGOCD_TOKEN)
```

#### Validation Guards (Hard Failures):
- `environment`, `gitops_repo_url`, `gitops_branch`, and `argocd_apps` are mandatory.
- `gitops_chart_values_file` strictly requires `gitops_chart_app_yq_path`.
- Cannot provide both `gitops_chart_values_file` and `gitops_manifest_file`.

---

### 1.2. Manifest-Based Deployment Schema ("Extras")
Used for services deploying raw Kubernetes YAML manifests.

```yaml
include:
  - project: 'devops/ci-templates'
    ref: 2.0.0/dev
    file: deploy/gitops/.argocd.gitlab-ci.yml
    inputs:
      environment: dev                                      # (required)
      gitops_repo_url: https://gitlab.contoso.com/gitops.git    # (required)
      gitops_branch: myapp/dev                             # (required)
      gitops_manifest_file: extras/manifests/clamav/deployment.yaml # (required for Manifest)
      gitops_new_image: registry.contoso.com/tools/clamav:1.0.0 # (mandatory when manifest_file is set)
      argocd_apps: acme-cloud-myapp-extras-dev-clamav   # (required: 1 app only!)
      environment_url: https://clamav.dev.contoso.com           # (optional)
```

#### Validation Guards (Hard Failures):
- `gitops_manifest_file` strictly requires `gitops_new_image`.
- Container image in `deployment.yaml` is updated directly via `yq`.
- Syncs only the single extras application.

---

## 2. Komodo GitOps Template: `deploy/gitops/.komodo.gitlab-ci.yml`

Used for docker-compose based services managed by Komodo.

```yaml
include:
  - project: 'devops/ci-templates'
    ref: 2.0.0/dev
    file: deploy/gitops/.komodo.gitlab-ci.yml
    inputs:
      environment: dev                                      # (required)
      gitops_repo_url: https://gitlab.contoso.com/compose.git   # (required)
      gitops_branch: acme/vpn                               # (required)
      gitops_compose_file: docker-compose.yml               # (defaults to docker-compose.yml)
      gitops_service_image_yq_path: .services.vpn.image    # (required: yq path to image tag)
      komodo_stack_name: myapp-dev                         # (required: {product}-{env})
      # Credentials default to CI/CD variables (KOMODO_SERVER, KOMODO_API_KEY, KOMODO_API_SECRET)
```

#### Validation Guards (Hard Failures):
- `environment`, `gitops_repo_url`, `gitops_branch`, `gitops_compose_file`, `gitops_service_image_yq_path`, and `komodo_stack_name` are mandatory.
- CI clones the GitOps repo, updates the target image using `yq`, pushes to branch, and executes redeployment against the Komodo stack API.

---

## 3. Delivery Job Wiring — nothing deploys on an artifact that was never published

The injected templates already wire this; the rule is here so an audit can tell a correct
pipeline from one that merely looks correct.

A deployment is the most expensive thing a pipeline does and the only one users see. It must
never run once its input is known to be bad, and it must never wait on work it does not consume.
The ArgoCD deploy job therefore declares:

```yaml
stage: deploy
needs:
  - job: Common:Init
    artifacts: true
    optional: true
  - job: Chart:Push
    artifacts: false
    optional: true
  - job: Image:Push
    artifacts: false
    optional: true
  - job: Deploy:ArgoCD:Validate:Chart:$[[ inputs.environment ]]
```

Three things to check, in this order:

1. **`optional: true` is about pipeline composition, not failure tolerance.** It means *"this job
   may not exist in this pipeline"* — a deploy-only run has no `Image:Push` to wait for. It does
   **not** mean the failure is ignored: when the job is present and fails, the deploy does not
   run. Reading it as "don't care" is the single most common misreading of this block.
2. **Validation runs in `check`, long before anything is cloned or pushed.** The
   `Deploy:*:Validate:*` jobs sit in the `check` stage with their own `needs:` on
   `Workflow:Validate:Variables`. A missing `argocd_apps` or a `gitops_chart_values_file` without
   `gitops_chart_app_yq_path` must fail in seconds, not after a GitOps clone.
3. **`artifacts: false` where nothing is consumed.** The deploy reads no artifact from
   `Image:Push` or `Chart:Push` — only their success. Declaring `artifacts: true` there downloads
   payloads nobody opens.

The same principle governs the human gates in `SKILL.md` §2: scaffold with `enabled: false`,
send the review link, and sync only on an explicit go-call. A sync issued before review is the
human-scale version of a job that ran after its input was already known bad.
