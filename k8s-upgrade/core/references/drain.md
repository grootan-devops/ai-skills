# Cordon and drain

How any stage that drains a node does it, whatever the provider. `<node>` is the node being
drained.

```bash
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=10m
```

If the drain fails or times out, list what is left:

```bash
kubectl get pods -A -o wide --field-selector spec.nodeName=<node>,status.phase!=Succeeded,status.phase!=Failed
```

Ignore DaemonSet pods and static (mirror) pods. For each other pod, show the user why it did
not go (the PDB and its `ALLOWED DISRUPTIONS`, a pod stuck `Terminating`, a local volume) and
ask before deleting it:

```bash
kubectl delete pod -n <namespace> <pod> --wait=false
```

Use `--grace-period=0 --force` only for a pod already stuck in `Terminating` for longer than
its grace period, and only after the user confirms that pod.

Repeat until only DaemonSet and static pods remain. Storage systems that keep replicas on the
node need care:

- Longhorn: do not delete an `instance-manager` pod while the gate shows any volume that is
  not `healthy`. With every volume healthy, the node holds no last replica and the drain can
  remove it.
- Other replicated storage (Rook/Ceph, OpenEBS): follow that system's maintenance procedure
  (for Ceph, `noout`) before deleting its pods.
