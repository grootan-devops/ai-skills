# Backup (prepare)

Every node gets a backup folder named after the version it runs now:

```text
/etc/kubernetes-backup-<current-version>-<YYYYMMDD>/
```

If that folder exists already (a second run on the same day), `backup.sh` appends `-<HHMM>`.
It prints the folder path on its last line; record it per node in `plan.md`, because the upgrade and
validate stages and `recovery.md` use it.

## Control planes

On every control plane, one at a time:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp> --sudo --script providers/kubeadm/scripts/backup.sh --role control-plane --version <current-version>
```

It saves:

| Path in the backup | From |
| --- | --- |
| `manifests/`, `pki/`, `*.conf` and everything else | `/etc/kubernetes/` (the same layout, so `manifests/` sits at the top of the backup) |
| `config.yaml`, `kubeadm-flags.env` | `/var/lib/kubelet/` |
| `kubelet-pki/` | `/var/lib/kubelet/pki/` |
| `kubelet.service.d/` | the kubelet systemd drop-ins |
| `repo/` | the Kubernetes apt list or yum/dnf repo file, and the CRI-O one on a CRI-O node |
| `runtime/` | `/etc/containerd/` or `/etc/crio/`, and `/etc/crictl.yaml` |
| `etcd-snapshot.db` | `etcdctl snapshot save`, run in the local etcd member (stacked etcd only) |
| `kubeadm-config.yaml` | the `kube-system/kubeadm-config` ConfigMap |
| `SHA256SUMS` | checksums of everything above |

It verifies the etcd snapshot with `etcdutl snapshot status` (or `etcdctl` where `etcdutl` is
missing) and fails if the snapshot is empty or does not verify. With external etcd, it saves
no snapshot and says so: take one from the etcd cluster yourself, and record where it is.

## Workers

On every worker:

```bash
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/backup.sh --role worker --version <current-version>
```

Workers get the kubelet files: `/var/lib/kubelet/config.yaml`, `kubeadm-flags.env`,
`/etc/kubernetes/kubelet.conf`, the kubelet drop-ins, plus the repository files and the
container runtime configuration (`runtime/`).

## Off-node copy

Ask the user whether to copy each control plane's `etcd-snapshot.db` off the node. If they
agree, stream it into a location they name (never into the git-tracked part of a
repository), then compare checksums:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp> --sudo -- cat <backup>/etcd-snapshot.db > <local-dir>/<cp>-etcd-snapshot.db
providers/kubeadm/scripts/remote.sh "$C" <cp> --sudo -- sha256sum <backup>/etcd-snapshot.db
shasum -a 256 <local-dir>/<cp>-etcd-snapshot.db
```

A snapshot that only exists on the node it backs up is lost with that node.
