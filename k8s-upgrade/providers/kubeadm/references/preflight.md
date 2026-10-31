# Preflight (kubeadm)

The kubeadm checks, on top of the [core preflight](../../../core/references/preflight.md).
`check` runs steps 1 and 2 and the `--check` part of step 3, all read-only; `prepare` runs the
`--apply` part of step 3, which changes packages after a confirmation.

Below, `$C` is the cluster folder and `$RUN` the run folder (or `$C/_pending`).

## 1. SSH and sudo

Every node in `control-plane.md` and `workers.md` must
appear in `kubectl get nodes`, and every node in the cluster must appear in one of the two
files. Stop on any mismatch.

For every node:

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo -- 'id -u; hostname'
```

The output must be `0` and the node's hostname. Stop on any node that fails.

## 2. Node checks

For every node, save the output:

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo --script providers/kubeadm/scripts/node_preflight.sh > "$RUN/preflight/<node>.txt"
```

Stop on any of these in the output:

| Key | Fails when |
| --- | --- |
| `swap` | anything other than `off` (the user's `swapon --show` must print nothing) |
| `pkg_manager` | not `apt`, `dnf` or `yum` |
| `k8s_repo` | `missing` (packages must come from `pkgs.k8s.io`) |
| `cert_min_days` | below 30 on a control plane (renew first; `kubeadm upgrade apply` also renews) |
| `disk_free_root_mb` / `disk_free_var_mb` | below 2048 |
| `kubelet_active` | not `active` |
| `runtime` | not `containerd` or `cri-o`. The skill supports only these two. |
| `runtime_active` / `crictl_ok` | not `active` / not `yes` |
| `kubelet_cgroup_driver` / `runtime_cgroup_driver` | different from each other (both should be `systemd`) |
| `crio_repo` | `missing` on a CRI-O node (CRI-O must come from its `cri-o:/stable:/vX.Y` repository), or a minor different from `k8s_repo` |
| `reboot_required` | `yes` is a warning, not a failure: plan the reboot inside that node's drain window |

All nodes should run the same runtime. A mix of containerd and CRI-O is allowed but record
it in `plan.md`, because each node then follows its own runtime's steps in the upgrade stage.

Show the user the versions (`kubeadm`, `kubelet`, `kubectl`, `runtime`, `runtime_version`,
`runc`/`crun`), the held packages, and the certificate expiry table from every control plane.

## 3. Operating-system packages

Show what would change, per node:

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo --script providers/kubeadm/scripts/pkg_update.sh --check
```

`pkg_update.sh` never touches `kubeadm`, `kubelet`, `kubectl`, `kubernetes-cni` or
`cri-tools`. It also keeps back the container runtime (`containerd`, `containerd.io`, `runc`,
`cri-o`) unless given `--runtime`, because restarting the runtime on a node that still runs
workloads is a disruption. Ask the user before applying, then per node:

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo --script providers/kubeadm/scripts/pkg_update.sh --apply
```

Runtime updates and reboots happen later, inside that node's drain window (the upgrade stage),
with `--apply --runtime`.
