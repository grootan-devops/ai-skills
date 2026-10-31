# Version path (kubeadm)

The kubeadm steps of the version path. The rules (allowed hops, compatibility of what runs on
the cluster, confirmation) are in [core version path](../../../core/references/version-path.md).

## Control-plane images

On every control plane, through `remote.sh`; all must show the same version:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp> --sudo -- "grep -h 'image:' /etc/kubernetes/manifests/*.yaml"
```

## Resolving versions

Latest patch of a minor, from the release service:

```bash
curl -fsSL https://dl.k8s.io/release/stable-<MAJOR.MINOR>.txt
```

What the package repository actually offers, on the first control plane:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh versions <MAJOR.MINOR>
```

`versions` reads the repository for that minor without changing the node's configuration.
The next minor exists when this lists at least one version. Use the highest stable version it
lists, and the matching package revision, as the target.

## Container runtime

The preflight's `runtime` key says which runtime each node uses. Only containerd and CRI-O are
supported.

**CRI-O.** CRI-O releases one minor per Kubernetes minor, and a node's CRI-O minor must match
its kubelet minor. A minor hop therefore upgrades CRI-O on every CRI-O node too, in that
node's drain window. Before confirming the hop, check that CRI-O has packages for the target
minor:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cri-o-node> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh versions <MAJOR.MINOR> cri-o
```

Stop if it lists nothing: CRI-O for that minor is not released yet, so the hop has to wait. A
patch hop leaves CRI-O on its minor; take its latest patch in the drain window only if the
user asks.

CRI-O packages moved hosts: up to 1.32 they are on pkgs.k8s.io
(`addons:/cri-o:/stable:/vX.Y`), from 1.33 on the openSUSE build service
(`download.opensuse.org/repositories/isv:/cri-o:/stable:/vX.Y`). `versions` prints the
repository it found the packages in, and a `NOTE` when that is not the host the node's
repository file points at. In that case `k8s_pkg.sh repo` refuses to switch the file. Before
the upgrade stage, with the user's confirmation, replace each CRI-O node's repository line and signing
key with the new host's (its `Release.key`), following CRI-O's install instructions for the
target minor, then check again with `versions`.

**containerd.** containerd is versioned on its own. Look up the target Kubernetes minor in
containerd's documented Kubernetes support (the "Kubernetes Support" table in its releases
documentation) and mark the installed version `supported`, `unsupported` or `unknown`, like the
components below. If it is `unsupported`, upgrading containerd becomes part of each node's
drain window (`pkg_update.sh --apply --runtime`); confirm the containerd version that
OS packages will install is itself supported.

For both runtimes, the kubelet and the runtime must use the same cgroup driver
(`kubelet_cgroup_driver` and `runtime_cgroup_driver` in the preflight), and the node must be on
cgroup v2 if the target minor's changelog has dropped cgroup v1 support.
