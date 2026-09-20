# ArgoCD GitOps Repository Architecture Contract

This document defines the structural contract for Kubernetes GitOps repositories utilizing `argocd-gitops-tpl-library`.

---

## 1. Directory Structure

```
gitops-repo/ (branch: {product}/{environment})
├── Chart.yaml              # Root chart declaration (name: {product-prefix})
├── values.yaml             # Master applications registry (apps: { ... })
├── values/                 # Microservice values overrides
│   ├── chat/frontend.yaml
│   ├── docs.yaml
│   └── notification.yaml
├── templates/
│   └── applications.yaml   # {{- include "tpl.argocd.applications" . -}}
└── extras/                 # Raw manifest applications stack
    ├── Chart.yaml          # Standardized chart declaration
    ├── values.yaml         # Extras configuration
    ├── templates/
    │   └── manifest.yaml   # {{- include "tpl.argocd.application.extras" . -}}
    └── manifests/
        └── clamav/
            ├── deployment.yaml   # Mandatory deployment file
            ├── service.yaml      # Optional
            └── ingress.yaml      # Optional
```

---

## 2. Helm Service Registry Contract

### 2.1. Registration in Root `values.yaml`
When a new service is onboarded, it is registered under `.apps`.

> [!CRITICAL]
> **MANDATORY SAFETY LAW: INITIALLY DISABLED**:
> The service MUST be committed initially with `enabled: false`.
> This guarantees that merging or committing the configuration does NOT trigger unintended deployment before human review.

```yaml
apps:
  chat:
    frontend:
      enabled: false  # MANDATORY: Initially disabled for review!
      chart:
        repoURL: oci://registry.contoso.com/devops/charts
        name: chat-frontend
        version: 0.1.0
```

### 2.2. Service Values Overrides (`values/<service>.yaml`)
Create `values/<service>.yaml` (or `values/<group>/<service>.yaml`) with the following enterprise baselines:
1. **Mandatory Memory Limits**:
   Default to `128Mi` for requests and `128Mi` for limits.
   ```yaml
   resources:
     requests:
       memory: 128Mi
     limits:
       memory: 128Mi
   ```
2. **Dev Image Repository Suffix**:
   In `dev` environments, the repository path MUST include the `/dev` suffix:
   ```yaml
   image:
     repository: registry.contoso.com/myapp/chat-frontend/dev
   ```
3. **Ingress & Route Discovery**:
   Inspect existing `values/*.yaml` files in the repository (e.g., `values/docs.yaml`) to discover the active base domain (e.g. `contoso.com`). Auto-compute:
   ```yaml
   routes:
     enabled: true
     host: chat-frontend.dev.contoso.com
   ```

---

## 3. Raw Manifest Stack Contract ("Extras")

For workloads that do not package Helm charts:
1. Create a subdirectory under `extras/manifests/<service-name>/`.
2. The primary deployment manifest **MUST ALWAYS BE NAMED** `deployment.yaml` (or `deployment.yml`).
3. **Production-Grade Standard (Exact Replica of `helm-tpl-library`)**:
   The manifest stack (either multi-document in `deployment.yaml` or split into files) must replicate the full `helm-tpl-library` production standard:
   - **ServiceAccount**: Dedicated ServiceAccount with `automountServiceAccountToken: false` and standard labels.
   - **ConfigMap & Secret Load**: `${SERVICE_NAME}-env` ConfigMap and Secret injected via `envFrom: [configMapRef, secretRef]`.
   - **Downward API**: Injected `POD_NAME`, `POD_NAMESPACE`, `POD_IP` environment variables.
   - **10001:10001 Security Context**:
     - Pod-level: `runAsUser: 10001`, `runAsGroup: 10001`, `fsGroup: 10001`, `runAsNonRoot: true`, `seccompProfile: { type: RuntimeDefault }`, `fsGroupChangePolicy: OnRootMismatch`.
     - Container-level: `runAsUser: 10001`, `runAsGroup: 10001`, `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `privileged: false`, `capabilities: { drop: ["ALL"] }`.
   - **Volume Mounts**: `/tmp` mounted with `emptyDir: {}` to support read-only root filesystems.
   - **Probes**: Configured `livenessProbe` (`/healthz`), `readinessProbe` (`/ready`), and `startupProbe` (`/healthz`).
   - **Topology & Scheduling**: `topologySpreadConstraints` on `kubernetes.io/hostname`.
   - **Service**: ClusterIP service mapping port 80 to container targetPort 8080.
   - **Ingress**: Ingress with TLS termination, `nginx.ingress.kubernetes.io/ssl-redirect: "true"`, and host routing `${service}.${env}.${domain}`.
4. **ArgoCD Application Naming**:
   ArgoCD discovers each subdirectory and automatically generates a single Application CR named:
   ```
   {chartBase}-extras-{environment}-{serviceName}
   ```

---

## 4. ArgoCD Root App-of-Apps Bootstrap Manifest

To initialize GitOps in a cluster, the root application is applied:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: acme-cloud-myapp-dev-root
  namespace: argo-cd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: developer
  source:
    repoURL: https://gitlab.contoso.com/devops/gitops/acme-cloud.git
    path: .
    targetRevision: myapp/dev
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

## 5. Sync & Health Verification

When triggering or verifying synchronization:
1. **Via CLI** (if `argocd` CLI is installed and authenticated):
   ```bash
   argocd app get "<app>" --hard-refresh --grpc-web
   argocd app sync "<app>" --grpc-web --async
   argocd app wait "<app>" --health --sync --timeout 600 --grpc-web
   ```
2. **Via Web UI**:
   Provide the user with clickable direct links:
   ```
   https://{argocd_server}/applications/argo-cd/{app_name}?view=tree&resource=&orphaned=false
   ```
