# Recovery (kubeadm)

Stop the stage first. Generic problems (the `kubernetes` Service without endpoints, a volume that
does not attach, a drain that does not finish) are in
[core recovery](../../../core/references/recovery.md). Every action here changes the cluster: show the user the evidence and
the exact command, and ask before running it. Record both in `log.md`.

When `kubectl` from your machine does not answer (it may reach the API through something
that runs in the cluster), run it on a control plane:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'kubectl --kubeconfig /etc/kubernetes/admin.conf get nodes'
```

## kubeadm upgrade apply failed

kubeadm restores the previous static pod manifests by itself. Read
`/root/kubeadm-upgrade-apply-<ver>.log` for the failing step. Its own backups are in
`/etc/kubernetes/tmp/kubeadm-backup-manifests-*` and `kubeadm-backup-etcd-*`.

- etcd may already be on the new version after a failed apply; that is supported. Do not
  downgrade etcd.
- `kubeadm upgrade apply` checks cluster health with a short-lived Job before it starts. If
  that Job cannot run (no CNI, no endpoints, a taint), fix that first; do not skip the check.
- Run the apply again only once the cause is fixed. It is safe to repeat.

## Static pod manifests changed beyond images

Copy the missing part back from the backup, not the whole file, so the new image stays:

```bash
diff -u <backup>/manifests/<file> /etc/kubernetes/manifests/<file>
```

Edit `/etc/kubernetes/manifests/<file>` on that node; the kubelet restarts the pod when the
file changes. Then add the same setting to the `kube-system/kubeadm-config` ConfigMap
(`apiServer.extraArgs`, `extraVolumes` and so on) so the next upgrade keeps it.

## The container runtime does not start after its upgrade

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo -- 'systemctl status crio containerd --no-pager -l | tail -30; journalctl -u crio -u containerd --since -15min --no-pager | tail -50'
```

The usual cause is configuration the new release rejects: a removed or renamed option in
`/etc/crio/crio.conf` or `crio.conf.d/`, or in `/etc/containerd/config.toml` (containerd 2
reads only the version 3 config format and migrates older files at start-up; a migration
error shows in the log). Compare with `<backup>/runtime/`, fix the option, and restart the
service. The kubelet's cgroup driver and the runtime's must still match.

To go back instead:

- CRI-O: point the repositories back with `k8s_pkg.sh repo <previous MAJOR.MINOR>` and run
  `k8s_pkg.sh install cri-o <previous MAJOR.MINOR>`. Only do this together with the kubelet,
  because the gate requires the CRI-O minor to equal the kubelet minor.
- containerd: install the previous version with the package manager (`apt-get install
  --allow-downgrades containerd.io=<version>` or `dnf downgrade containerd.io-<version>`) and
  restore `/etc/containerd/` from the backup.

The node stays drained until the runtime and the kubelet both run and the gate passes.

## The kubelet does not start after the upgrade

```bash
providers/kubeadm/scripts/remote.sh "$C" <node> --sudo -- 'journalctl -u kubelet --since -15min --no-pager | tail -50'
```

Compare `/var/lib/kubelet/config.yaml` and `kubeadm-flags.env` with the backup. A removed
or renamed flag in `kubeadm-flags.env` is the usual cause. Restore the file from the backup,
remove the flag the new kubelet rejects, and restart the kubelet. To go back to the previous
kubelet, install the previous version with `k8s_pkg.sh install kubelet <previous>` after
pointing the repository back with `k8s_pkg.sh repo <previous MAJOR.MINOR>`; a kubelet older
than the API server is supported, a newer one is not.

## Restoring etcd

This is the last resort, when the control plane's data is lost or corrupted and the node
snapshot cannot be used. It rolls the whole cluster back to the snapshot's moment. Do it only
with the user's explicit decision, on a cluster whose API is already down, following the
Kubernetes documentation for restoring etcd for the cluster's etcd topology. The snapshot is
`<backup>/etcd-snapshot.db` and the matching configuration is in the same backup folder.
