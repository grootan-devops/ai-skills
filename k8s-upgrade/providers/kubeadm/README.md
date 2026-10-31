# Provider: kubeadm

Self-managed clusters built with kubeadm. The skill reaches every node over SSH from the
operator's machine and upgrades it with the node's package manager.

## Scope

| Supported | Stops the run |
| --- | --- |
| Debian/Ubuntu (apt), RHEL family (dnf, yum) | Any other package manager or immutable OS |
| Packages from `pkgs.k8s.io` | Legacy repositories, distro-built packages, binary installs |
| containerd or CRI-O, through `crictl` | Docker with cri-dockerd, any other runtime |
| systemd-managed kubelet | Other init systems |
| Stacked etcd (snapshot taken by the skill) | — External etcd is allowed; you snapshot it yourself |

| Runtime | Handling |
| --- | --- |
| containerd | Versioned on its own; the version path checks its documented Kubernetes support, and `pkg_update.sh --apply --runtime` upgrades it in a node's drain window when needed |
| CRI-O | One CRI-O minor per Kubernetes minor; `k8s_pkg.sh repo` moves its repository with the Kubernetes one, `k8s_pkg.sh install cri-o` upgrades it in the drain window before the kubelet, and the gate fails if the two minors differ |

## Inputs

`init` creates `control-plane.md` and `workers.md`, one SSH credential table each
([credentials](./references/credentials.md)). The first control-plane row runs
`kubeadm upgrade apply`.

## check

Read [preflight](./references/preflight.md) (steps 1, 2 and the `--check` part of 3) and
[version path](./references/version-path.md).

1. Every node in the credential files is in the cluster, and every cluster node is in the
   files; SSH and sudo work on each.
2. `node_preflight.sh` on every node into `_pending/preflight/`: swap, distro, repository,
   runtime and cgroup drivers, certificates, disk, reboot.
3. `pkg_update.sh --check` per node: pending OS updates and runtime updates.
4. Current versions (API server, kubelets, static pod images), what pkgs.k8s.io offers, and the
   runtime target (CRI-O minor, containerd support), for the core version path.

## prepare

Read [preflight](./references/preflight.md) (the `--apply` part of step 3) and
[backup](./references/backup.md).

- The snapshot confirmation covers a VM or storage snapshot of every control-plane node (and of
  workers, if the user wants them).
- Before the baseline: show the pending OS updates from `check`, ask, then
  `pkg_update.sh --apply` per node. Never the runtime here.
- Backups: every control plane (etcd snapshot, `/etc/kubernetes`, kubelet and runtime
  configuration, repositories), then every worker (kubelet and runtime configuration,
  repositories), with `backup.sh`. Offer the off-node copy of the etcd snapshots.

## upgrade

Read [control plane](./references/control-plane.md) and [workers](./references/workers.md); on
a failure, [recovery](./references/recovery.md).

1. Control planes in the order of `control-plane.md`: the first with `kubeadm upgrade apply`,
   the others with `kubeadm upgrade node`; manifest check against the backup; then drain,
   runtime, kubelet, gate, uncordon.
2. Workers in the order of `workers.md`: gate with `--wait`, drain, `kubeadm upgrade node`,
   runtime, kubelet, gate, uncordon.

To resume after a failure, skip each node whose kubelet, static pod images (control planes) and
CRI-O minor (CRI-O nodes) already match the target, after a gate.

## Scripts

| Script | Runs | Purpose |
| --- | --- | --- |
| `provider.py` | local | Hooks for `workspace.py`: input files, credential files, lint |
| `nodes.py` | local | Parse and check the credential files; list nodes |
| `remote.sh` | local | Run a command or a node-side script on a node by name, with sudo |
| `node_preflight.sh` | node | Distro, swap, runtime, cgroup drivers, holds, certificates, disk, versions |
| `pkg_update.sh` | node | OS package update with Kubernetes packages and CRI-O held, runtime optional |
| `k8s_pkg.sh` | node | Kubernetes and CRI-O repositories per minor, version lists, pinned installs |
| `backup.sh` | node | etcd snapshot and configuration backup |
| `manifest_diff.sh` | control plane | Manifests and kubelet configuration against the backup |
