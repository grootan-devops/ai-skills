# Verify (validate)

Prove that the cluster is in the state the baseline recorded, and that new workloads still
work end to end.

## 1. Post baseline

Wait until the last gate passes with `--wait`, then take the same measurements as prepare:

```bash
python3 core/scripts/gate.py --pre "$RUN/pre" --wait 900 --out "$RUN/gates/99-final.txt"
python3 core/scripts/critical.py "$RUN/post" "$C/critical-extra.txt"
core/scripts/snapshot.sh "$RUN/post"
python3 core/scripts/db_stats.py "$RUN/post"
python3 core/scripts/pvc_usage.py "$RUN/post"
```

Measure any database the baseline measured by hand the same way again.

## 2. Compare

```bash
python3 core/scripts/compare.py "$RUN/pre" "$RUN/post" --target v<ver> --out "$RUN/compare.md"
```

It exits 1 when there is a regression. What it checks:

| Area | Regression | Warning |
| --- | --- | --- |
| Nodes | A node missing, not `Ready`, cordoned, or not on the target kubelet | |
| Critical workloads | Fully ready before, not now | Not ready before and still not ready (pre-existing) |
| Workload pods | A workload has fewer ready pods than before; a pod is `Pending`, `ContainerCreating`, `Init:*`, `ImagePullBackOff`, `Evicted`, `Unknown` or `Error` and its workload had no pod in that state before; `CrashLoopBackOff` in a workload that was not crash-looping before | `CrashLoopBackOff` that was already there |
| PVCs and PVs | A PVC missing or no longer `Bound`; fewer PVs | |
| Databases | A database missing; a database pod measured before and not measurable now | A database smaller by more than 10% |
| PVC usage | | A PVC missing from the measurement, or using more than 10% and 100 MiB less than before |

Pods are matched by their workload (Deployment, StatefulSet, DaemonSet, Job owner or the bare
pod name), because pod names change when a node is drained. Completed Job pods are ignored.

For every warning, show the user the numbers. A shrinking database or PVC can be normal
(compaction, retention) or a sign of data loss; ask the user to confirm which.

## 3. Smoke test

Ask before running it; it creates and deletes a namespace.

```bash
core/scripts/smoke_test.sh [--host <hostname>] [--ingress-class <class> | --gateway <namespace>/<name>] [--image <registry/nginx-unprivileged:tag>]
```

It creates a throwaway namespace, then a PVC on the default StorageClass, a Deployment that
writes to it, a Service, and an Ingress or HTTPRoute (detected from the cluster unless
named). It checks that the pod schedules, the volume attaches and is writable, the Service
answers from another pod by its DNS name, and the route is accepted. With `--host` and a
reachable address, it also requests the route from your machine. It then deletes the
namespace and waits until it is gone. Use `--keep` only to debug a failure, and delete the
namespace afterwards.

## 4. Report

Write `report.md` in the run folder:

- versions before and after, per node;
- snapshot confirmation, backup folder per node and etcd snapshot locations;
- every confirmation and accepted exception from `plan.md`;
- gate results in order;
- the comparison (regressions, warnings and how the user resolved each);
- the smoke test result;
- the verdict: `PASS` only with no regression, every warning resolved, and a passing smoke
  test. Otherwise `FAIL`, with what is left to do.

If more hops are needed to reach the version the user asked for, say which is next. It is a
new run.
