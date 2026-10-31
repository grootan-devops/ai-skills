# Control plane (upgrade)

Upgrade the control planes one at a time, in the order of `control-plane.md`. The first row
runs `kubeadm upgrade apply`; every other control plane runs `kubeadm upgrade node`. `<ver>` is
the confirmed target without a leading `v` (for example `<MAJOR.MINOR.PATCH>`).

Before starting, run the gate with no node (cluster-wide checks only) and stop if it fails:

```bash
python3 core/scripts/gate.py --pre "$RUN/pre" --out "$RUN/gates/00-before-control-plane.txt"
```

## First control plane

### 1. kubeadm

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh repo <MAJOR.MINOR>
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install kubeadm <ver>
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'kubeadm version -o short'
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'kubeadm upgrade plan'
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- "kubeadm upgrade diff v<ver> | grep '^-' || true"
```

`k8s_pkg.sh repo` points the node's Kubernetes repository at the target minor, and on a CRI-O
node its CRI-O repository too (it keeps a copy of each previous file next to it). `install
kubeadm` unholds, installs the exact version and holds it again. CRI-O itself is upgraded
later, in step 5, while the node is drained.

Show the user the `upgrade plan` output and every removed line from `upgrade diff`. Removed
lines other than `image:` (flags, volumes, mounts) need an explanation from the target's
changelog before you continue.

### 2. Confirm, then apply

Ask the user to confirm `kubeadm upgrade apply v<ver>` on `<cp-1>`. Run it detached from the
SSH session, so a dropped connection does not interrupt it, and follow its log:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- "setsid nohup kubeadm upgrade apply v<ver> -y </dev/null >/root/kubeadm-upgrade-apply-<ver>.log 2>&1 &"
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'tail -n 30 /root/kubeadm-upgrade-apply-<ver>.log; pgrep -f "kubeadm upgrade apply" >/dev/null && echo RUNNING || echo FINISHED'
```

While it runs, watch for the webhook lock every minute:

```bash
kubectl get endpointslice -n default -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[*].endpoints[*].addresses}'
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- "crictl ps --name kube-apiserver -q | head -1 | xargs -r crictl logs --tail 50 2>&1 | grep -E 'unable to sync kubernetes service|failed calling webhook' || true"
```

If the EndpointSlice stays empty and the API server logs `failed calling webhook`, follow
"Kubernetes Service has no endpoints" in [recovery.md](./recovery.md). `kubectl` from your
machine may fail during this window; run it on the control plane through `remote.sh` with
`--kubeconfig /etc/kubernetes/admin.conf`.

The apply succeeded only when its log ends with the `SUCCESS!` line. If it failed, kubeadm
rolled the manifests back; read the log, then stop and follow
[recovery.md](./recovery.md). Do not run it again until the cause is fixed.

### 3. Manifest check

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/manifest_diff.sh <backup-dir>
```

It prints the full diff of the static pod manifests and the kubelet configuration against the
backup from prepare, then a section of changed lines that are not `image:` lines. Exit code 2 means
that section is not empty. Stop and show it to the user: a removed volume, mount or flag (for
example a mount a storage system relies on) must be restored in the kubeadm configuration
before continuing.

### 4. Gate

```bash
python3 core/scripts/gate.py --pre "$RUN/pre" --out "$RUN/gates/01-<cp-1>-after-apply.txt"
```

### 5. Container runtime, kubelet and kubectl

Ask the user to confirm draining `<cp-1>`. Then drain it:

```bash
kubectl drain <cp-1> --ignore-daemonsets --delete-emptydir-data --timeout=10m
```

Upgrade the container runtime first, so the new kubelet starts against it:

- **CRI-O node**, on a minor hop (always), or on a patch hop when the user asked for the latest
  CRI-O patch:

  ```bash
  providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install cri-o <MAJOR.MINOR>
  ```

  It installs the latest CRI-O patch of the target minor from the CRI-O repository `repo`
  switched in step 1, holds it, restarts `crio` and waits until `crictl` answers. Static pods
  and DaemonSet pods restart with it.

- **containerd node**, when version-path marked the installed containerd `unsupported`, or the
  preflight found a containerd/runc update:

  ```bash
  providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/pkg_update.sh --apply --runtime
  ```

Then the kubelet:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install kubelet <ver>
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'kubelet --version; systemctl is-active kubelet'
```

`install kubelet` installs `kubelet` and `kubectl` at `<ver>`, holds them, reloads systemd and
restarts the kubelet. If the preflight found a pending reboot for this node, reboot it now,
while the node is drained:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-1> --sudo -- 'systemctl reboot'   # only when reboot_required=yes
```

Wait for the node to be `Ready`, then gate and uncordon. The gate's `node-runtime` check fails
if a CRI-O node's CRI-O minor differs from its kubelet minor:

```bash
python3 core/scripts/gate.py --node <cp-1> --expect-version v<ver> --pre "$RUN/pre" --out "$RUN/gates/02-<cp-1>.txt"
kubectl uncordon <cp-1>
```

If draining a control plane is blocked, handle it as in [workers.md](./workers.md), step 2.

## Other control planes

For each remaining row of `control-plane.md`, after the user confirms that node:

```bash
providers/kubeadm/scripts/remote.sh "$C" <cp-n> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh repo <MAJOR.MINOR>
providers/kubeadm/scripts/remote.sh "$C" <cp-n> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install kubeadm <ver>
providers/kubeadm/scripts/remote.sh "$C" <cp-n> --sudo -- "setsid nohup kubeadm upgrade node </dev/null >/root/kubeadm-upgrade-node-<ver>.log 2>&1 &"
```

Follow the log as above, then run the manifest check, the gate, and step 5 for that node.

## Done

The control planes are done when every control plane runs `<ver>` for every static pod and its
kubelet, `kubectl get --raw='/readyz?verbose'` passes, and the `default/kubernetes`
EndpointSlice lists every control plane's address.
