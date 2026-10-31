# Baseline (prepare)

The baseline is what the validate stage compares against, and what the gates use to tell a regression
from a problem that existed before the upgrade. Take it after the preflight package updates
and immediately before the backup. All steps are read-only.

## 1. Critical components

```bash
python3 core/scripts/critical.py "$RUN/pre" "$C/critical-extra.txt"
```

It writes `critical.json`: every Deployment, StatefulSet and DaemonSet that the cluster or
the applications depend on, with a category:

| Category | Detected from |
| --- | --- |
| `kube-system` | every workload in `kube-system` |
| `cni` | DaemonSets of Calico, Cilium, Flannel, Canal, Weave, Antrea, kube-router, kube-ovn |
| `storage` | CSI drivers and storage systems: Longhorn, Rook/Ceph, OpenEBS, Portworx, any `*csi*` workload |
| `database` | workloads whose images are PostgreSQL, MySQL, MariaDB, MongoDB, Redis/Valkey, ClickHouse, Cassandra, Elasticsearch/OpenSearch, etcd, Kafka, NATS, MinIO, RabbitMQ |
| `operator` | workloads named `*operator*` or `*controller-manager*` outside `kube-system` |
| `policy-security` | Kyverno, Gatekeeper, cert-manager, Vault, External Secrets, Falco, Trivy, service meshes |
| `ingress` | ingress controllers and gateways: ingress-nginx, Traefik, APISIX, Envoy Gateway, HAProxy, Contour, Kong |
| `gitops` | Argo CD, Flux |
| `extra` | each `namespace/kind/name` line in `critical-extra.txt` |

Show the list to the user grouped by category. Ask whether anything else the applications
depend on is missing, and add it to `critical-extra.txt`, which later runs reuse.

## 2. Cluster state

```bash
core/scripts/snapshot.sh "$RUN/pre"
```

Saves nodes, pods, PVCs, PVs, VolumeAttachments, workloads, webhooks, storage classes and,
when present, Longhorn volumes, as JSON, plus `summary.txt` with the counts (nodes, pods by
phase and reason, PVCs by phase, PVs, VolumeAttachments). Show `summary.txt` to the user.

## 3. Databases

```bash
python3 core/scripts/db_stats.py "$RUN/pre"
```

For every running database pod it finds, it runs the engine's client inside the pod and
records each database and its size in `db_stats.tsv`
(`namespace pod container engine database size unit status`; the unit is bytes, or keys for
Redis and Valkey). The credentials come from the
container's own environment, which Kubernetes fills from the database's Secrets, and the
command expands them inside the pod. No credential is read out of the cluster or printed.

A row whose status is not `ok` (`no-client`, `auth-failed`, `no-credentials`, `error`) means
that database is not measured. For each one:

1. Find the Secret holding its credentials: the container's `env`/`envFrom`, the operator's
   documented Secret (for example a `*-superuser`, `*-app` or `*-root` Secret), or the chart's
   values.
2. Tell the user which Secret you will read, read the value into a shell variable without
   printing it, and run the same query as `db_stats.py` with the value passed on standard
   input or as an environment variable of `kubectl exec ... -- env VAR=... client`. Append
   the result to `db_stats.tsv`.
3. If it still cannot be measured, record it in `plan.md` and ask the user to accept the gap.

## 4. PVC usage

```bash
python3 core/scripts/pvc_usage.py "$RUN/pre"
```

Writes `pvc_usage.tsv` (`namespace pvc pod node used_bytes capacity_bytes source`). It reads
each node's kubelet volume statistics, and for any mounted PVC those do not cover it runs `df`
inside a pod that mounts it. A PVC with no running pod is listed with `source=unmounted`.

## 5. Record

Add to `plan.md`: the counts from `summary.txt`, the number of critical components per
category, the number of databases measured and not measured, and the number of PVCs measured.
