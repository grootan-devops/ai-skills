# Gates

`core/scripts/gate.py` decides whether the cluster is healthy enough to touch the next node. Run
it after the control plane, after each node, and before the next node. It prints one line per
check (`PASS`, `FAIL` or `SKIP`, the check name, details) and exits non-zero if any check
failed.

```text
python3 core/scripts/gate.py [--node NAME] [--expect-version vX.Y.Z] [--pre DIR] [--wait SECONDS] [--out FILE]
```

| Option | Effect |
| --- | --- |
| `--node` | Adds the node checks (4, 4a and 5) for that node |
| `--expect-version` | The node's kubelet must report this version |
| `--pre` | The baseline folder. Without it, the critical-workload check (7) is skipped, so always pass it during a run |
| `--wait` | Repeat all checks every 30 seconds until they pass or the time runs out |
| `--out` | Also write the result to this file |

## Checks

| # | Check | Passes when |
| --- | --- | --- |
| 1 | `api-readyz` | `kubectl get --raw /readyz` returns `ok` |
| 2 | `kubernetes-endpoints` | The `default/kubernetes` EndpointSlice has endpoints and lists the InternalIP of every node labelled `node-role.kubernetes.io/control-plane` (a managed control plane has none, so only the first part applies) |
| 3 | `webhooks` | Every admission webhook with `failurePolicy: Fail` that calls a Service has at least one ready endpoint behind it |
| 4 | `node-ready` | The node is `Ready` and, with `--expect-version`, its kubelet runs that version |
| 4a | `node-runtime` | The node runs containerd, or runs CRI-O whose minor equals its kubelet minor |
| 5 | `node-cni` | Every pod of the CNI DaemonSets on the node is `Ready` |
| 6 | `storage` | Longhorn: every attached volume is `healthy` and every instance manager is `running`. Any CSI: no VolumeAttachment has an attach or detach error, and no volume has attachments on two nodes |
| 7 | `critical` | Every critical workload in `<pre>/critical.json` that was fully ready in the baseline is fully ready now |
| 8 | `nodes-cordoned` | Without `--node`, or for nodes other than `--node`: no node is cordoned (a forgotten cordon from an earlier node) |

A workload that was already failing in the baseline does not fail check 7; `compare.py`
reports it in the validate stage as pre-existing.

## When a gate fails

Stop. Do not drain, uncordon or upgrade anything else. Show the failing lines to the user,
then find the matching section of [recovery.md](./recovery.md). Re-run the gate after the
fix, and continue only when it passes.
