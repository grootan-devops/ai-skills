# Deployment rules

> Every workload deployed through the workflows must meet these rules.
> They mirror the cluster's Kyverno policies (`extras/manifests/kyverno/` on the
> platform branch) plus the probe rule. When a rule truly cannot be met (an operator
> generates the object, or the workload is a host agent), record it in the branch
> README's **Kyverno policy exceptions** table instead of forcing it.

## 1. The rules

### lockdown-default-namespace

- Nothing is created in the `default` namespace.

### audit-workload-best-practices

Applies to Pods, Deployments, StatefulSets, DaemonSets, ReplicaSets, Jobs and CronJobs.

- Every container, init containers included, has **positive and equal** memory requests and limits.
- Memory requests and limits are a **power of two in Mi**: 4Mi, 8Mi, 16Mi, … 1024Mi and up.
- Every image has an explicit tag that is not `latest`, and `imagePullPolicy: IfNotPresent`.
- Deployments, StatefulSets and DaemonSets set `revisionHistoryLimit: 0`.
- Every `emptyDir` has a positive `sizeLimit`.
- Token mounting is off (`automountServiceAccountToken: false`), or the pod's own ServiceAccount sets `automountServiceAccountToken` explicitly. The `default` ServiceAccount is ignored.

### audit-strict-security-context

Applies to the same kinds.

- **Pod level:** positive `fsGroup`, `runAsUser` and `runAsGroup`, plus `runAsNonRoot: true`, `seccompProfile.type: RuntimeDefault` and `fsGroupChangePolicy: OnRootMismatch`.
- **Every container, init containers included:** `allowPrivilegeEscalation: false`, `privileged: false`, `capabilities.drop: [ALL]`. Set `readOnlyRootFilesystem: false` explicitly; read-only root filesystems are not required for now.
- **No overrides:** a container can't switch back to root, use another seccomp profile, or **add capabilities**.
- **Host access:** no `hostNetwork`, `hostPID` or `hostIPC`, no `hostPath` volumes, no `hostPort`.
- **Sysctls:** only the PSS-baseline safe ones are allowed:
  - `kernel.shm_rmid_forced`
  - `net.ipv4.ip_local_port_range`, `net.ipv4.ip_unprivileged_port_start`, `net.ipv4.ip_local_reserved_ports`
  - `net.ipv4.tcp_syncookies`, `net.ipv4.ping_group_range`
  - `net.ipv4.tcp_keepalive_time`, `net.ipv4.tcp_keepalive_intvl`, `net.ipv4.tcp_keepalive_probes`, `net.ipv4.tcp_fin_timeout`

Mandatory baseline values:

```yaml
podSecurityContext:
  fsGroup: 10001
  fsGroupChangePolicy: OnRootMismatch
  runAsUser: 10001
  runAsGroup: 10001
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
containerSecurityContext:
  privileged: false
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: false
  capabilities:
    drop:
      - ALL
```

- **Never run as UID 0.** Prefer `10001`.
- **Keep the image's own UID** when the image ships files owned by that UID (e.g. `1000` for node images, `999` for valkey, `65532` for distroless). Changing it breaks writes.

### audit-pdb-coverage

- Every Pod is covered by a PDB. Helm hook pods are skipped.

### audit-pdb-unhealthy-pod-eviction-policy

- Every PDB sets `unhealthyPodEvictionPolicy` (use `AlwaysAllow`).

### audit-batch-jobs-best-practices

- **Jobs** set `ttlSecondsAfterFinished: 60`, `activeDeadlineSeconds`, `backoffLimit`, `parallelism` and `completions`.
- **CronJobs** keep 1 successful and 1 failed job, and set `concurrencyPolicy`, `timeZone`, `startingDeadlineSeconds` and `schedule`. A CronJob's Job template meets the Job rules.

Standard shape (keep each job's own schedule; change other values only with a reason):

```yaml
spec:
  schedule: "0 0 * * *"
  timeZone: "Europe/Berlin"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 300
  failedJobsHistoryLimit: 1
  successfulJobsHistoryLimit: 1
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 60
      activeDeadlineSeconds: 3600
      backoffLimit: 3
      parallelism: 1
      completions: 1
```

### audit-pod-security-admission-labels

- Every namespace except `kube-system`, `kube-public`, `kube-node-lease` and `kyverno` has `pod-security.kubernetes.io/enforce` set to `baseline` or `restricted`. A cluster admin applies this (see each backend's namespace steps, for Argo CD [discovery.md](../../gitops/argocd/references/discovery.md) §7).

### audit-standard-labels

- Deployments, StatefulSets, DaemonSets, CronJobs, Services, Ingresses and PDBs carry non-empty `app.kubernetes.io/name` and `app.kubernetes.io/instance` labels.
- Owned objects (anything an operator generates) and `default/kubernetes` are skipped.
- **Name and instance values:** use the app or directory name, e.g. `komodo/komodo`.
- **Helm-chart companions:** use the chart's own labels, e.g. `kube-prometheus-stack/prometheus-operator`.

### Probes

- Every long-running container has **all three** probes (`startupProbe`, `readinessProbe`, `livenessProbe`), each with a complete schema.
- Use a real health endpoint (`httpGet` on `/health`, `/healthz`, `/ready`, …) when the app has one. Otherwise default to `tcpSocket` on the main port.
- **Third-party charts are the exception:** probes come from the chart, and the workflow does not add or tune them.

```yaml
startup:   { tcpSocket: { port: 8080 }, initialDelaySeconds: 10, periodSeconds: 10, timeoutSeconds: 10, successThreshold: 1, failureThreshold: 3 }
readiness: { httpGet: { path: /health, port: 8080 }, initialDelaySeconds: 10, periodSeconds: 10, timeoutSeconds: 10, successThreshold: 1, failureThreshold: 3 }
liveness:  { httpGet: { path: /health, port: 8080 }, initialDelaySeconds: 30, periodSeconds: 30, timeoutSeconds: 10, successThreshold: 1, failureThreshold: 3 }
```

## 2. How to meet the rules by deployment type

### Public / third-party Helm chart (plugin `kustomized-helm`)

1. **Start from vanilla values.** Run `helm show values <chart> --version <v>` and save the output as the app's values file.
2. **Make the minimum edits,** and note each one so an upgrade can re-apply them:
   - security contexts (pod and container), UIDs per the image
   - resources: memory only, requests equal to limits, a power of two
   - persistence: storage class from siblings, **1Gi** to start
   - hostname, TLS, ingress or route class
   - PDB, if the chart supports one: `maxUnavailable: 1` plus `unhealthyPodEvictionPolicy: AlwaysAllow` where the chart passes it through
   - `revisionHistoryLimit: 0`, `automountServiceAccountToken`, job TTL and deadlines, where the chart exposes them
3. **Use a kustomize patch for anything the chart cannot set.** Typical patches:
   - `revisionHistoryLimit: 0` on Deployments, StatefulSets and DaemonSets
   - `automountServiceAccountToken: false` on pods that never call the API
   - a container `securityContext` the chart doesn't template, often on init containers and hook Jobs
   - `sizeLimit` on chart emptyDirs
   - `unhealthyPodEvictionPolicy` on chart PDBs: a JSON6902 `add` with a kind-only target
   - Job `ttlSecondsAfterFinished`, `parallelism`, `completions` and `backoffLimit`
4. **Put objects the chart cannot create at all in `extras/manifests/<app>/`.** Typical cases:
   - a PDB (when the chart has no PDB template, or only creates one for replicas > 1)
   - an HTTPRoute
   - a Certificate
5. **Never edit chart probes.**

**Upgrading a chart:**

1. Get the new vanilla values.
2. Diff them against the committed file.
3. Keep the new upstream defaults and re-apply only the recorded edits.
4. Re-render and re-audit.

### Private chart (`helm-tpl-library`)

- **No plugin.** The library already templates security contexts, probes, PDB, `revisionHistoryLimit`, routes and jobs.
- **Minimal values only,** following sibling apps on the same branch:
  - `component` / `subComponent`
  - image repository and tag (usually `tag: ""`, so the chart version drives it)
  - `replicas`
  - resources
  - `database.*` (host, name, user)
  - `oauth` / `keycloak` client
  - `routes` (domain, gateway, TLS secret)
  - persistence
  - only the env vars the app needs

  Never copy a sibling's whole file.
- **Cross-check** the rendered output against every rule except resource sizes. If the chart or the app repo is not compliant, **stop and raise it**: list the failing rules and ask whether to fix it in the chart repo or in this GitOps branch (same branch, or a new branch plus a PR).

### Raw manifests (`extras/manifests/<app>/`)

- **One file per kind.** Every object sets `metadata.namespace` and the standard labels.
- **Write them compliant from the start:** pod and container security contexts, resources, probes, `revisionHistoryLimit: 0`, `automountServiceAccountToken: false` unless the pod calls the API, and emptyDir `sizeLimit`.
- **Always add** `pdb.yaml` (`maxUnavailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`, selector equal to the pod labels) and the route (`httproute.yaml` or `ingress.yaml`).
- **Never ship a pod that needs root at runtime** (`apt-get`, `apk add`, `chown`):
  - Copy tools from their own image in an init container into a shared `emptyDir`. For example `amazon/aws-cli` → `cp -aL /usr/local/aws-cli/v2/current /tools/aws-cli`, or `alpine/k8s` → `kubectl`/`yq`.
  - Or pick an image that already contains them (`mongo` has `mongodump`, `nicolaka/netshoot` has `ssh`/`openssl`/`curl`).
- **Tools that need a passwd entry** (`ssh`): an init container writes `/etc/passwd` and `/etc/group` with the UID into an `emptyDir`, and they are mounted with `subPath`.
- **Added capabilities are not a fix.** Kubernetes puts them only in the bounding set, so a non-root process cannot use them. If something needs `NET_BIND_SERVICE` or `NET_RAW`, use the safe sysctls (`net.ipv4.ip_unprivileged_port_start`, `net.ipv4.ping_group_range`) or a port above 1024.

## 3. Exceptions

Add a README row (`Application | Workload type | Issue | Reason`) only when:

- an **operator or CRD** generates the object and exposes no field for the fix (e.g. operator StatefulSets without `revisionHistoryLimit`, operator PDBs without `unhealthyPodEvictionPolicy`)
- a **host-level agent** needs privilege (CSI or storage managers, log collectors reading root-owned host logs)
- **existing data** can't move off a root UID without a planned migration (e.g. a database PVC written as root). In that case, plan the migration with the user.

## 4. Verification checklist (run before asking to commit)

1. **Render:** the root and extras charts with `helm template`, and every new or changed app exactly as Argo CD renders it (plugin: `helm template` then `kustomize build`).
2. **Security audit:** for every Pod, Deployment, StatefulSet, DaemonSet, Job and CronJob, check:
   - pod-level keys present and non-zero
   - every container (init included) has the container keys
   - no `capabilities.add`
   - no host fields
   - no unsafe sysctls
3. **Workload audit:**
   - memory requests equal limits and are a power of two
   - the image tag is pinned, with `IfNotPresent`
   - `revisionHistoryLimit: 0`
   - every emptyDir has a `sizeLimit`
   - automount is set
4. **PDBs:**
   - every pod is matched by a PDB selector, with no duplicates between the chart and extras
   - every PDB sets `unhealthyPodEvictionPolicy`
5. **Jobs and CronJobs** meet the batch schema. Helm `test` hooks are skipped.
6. **Labels** are on every object kind listed above.
7. **Non-root smoke test** for any image that used to run as root, or whose UID changed:

   ```bash
   docker run --rm --user <uid>:<gid> --cap-drop ALL \
     --security-opt no-new-privileges --network none \
     <image> <cmd>
   ```

   It must get past setup and fail only on the network, never on permissions.
8. **Live check after sync** (read-only): `kubectl get policyreports -n <ns>`. Filter the results to this app's objects and confirm nothing new fails outside the README exceptions.
