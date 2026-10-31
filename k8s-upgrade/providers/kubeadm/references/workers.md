# Workers (upgrade)

Upgrade workers one at a time, in the order of `workers.md`. Never start a node while another
node is cordoned or the last gate failed.

For each worker:

## 1. Gate and confirm

```bash
python3 core/scripts/gate.py --pre "$RUN/pre" --wait 900 --out "$RUN/gates/<nn>-before-<worker>.txt"
```

`--wait` keeps checking for up to that many seconds, for storage that is still rebuilding
after the previous node. If it still fails, stop.

Show the user what runs on the node (`kubectl get pods -A -o wide --field-selector
spec.nodeName=<worker>`) and which PodDisruptionBudgets cover those pods, and ask to confirm
the drain.

## 2. Cordon and drain

Follow [drain](../../../core/references/drain.md) for `<worker>`: cordon, drain, and resolve
anything that blocks it until only DaemonSet and static pods remain.

## 3. Upgrade

```bash
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh repo <MAJOR.MINOR>
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install kubeadm <ver>
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo -- 'kubeadm upgrade node'
# CRI-O node on a minor hop (or a requested CRI-O patch):
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install cri-o <MAJOR.MINOR>
# containerd node with an unsupported containerd or a pending containerd/runc update:
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/pkg_update.sh --apply --runtime
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo --script providers/kubeadm/scripts/k8s_pkg.sh install kubelet <ver>
providers/kubeadm/scripts/remote.sh "$C" <worker> --sudo -- 'kubelet --version; systemctl is-active kubelet'
```

Run only the runtime line that applies to the node (see
[control-plane.md](./control-plane.md), step 5). `kubeadm upgrade node` updates the node's
kubelet configuration to the new release; skipping it leaves the kubelet running with the
previous release's configuration. The runtime goes before the kubelet, so the new kubelet
starts against it. If the preflight found a pending reboot for this node, reboot it now.

## 4. Gate, then uncordon

```bash
python3 core/scripts/gate.py --node <worker> --expect-version v<ver> --pre "$RUN/pre" --out "$RUN/gates/<nn>-<worker>.txt"
kubectl uncordon <worker>
```

The gate requires the node to be `Ready` with the new kubelet and its CNI pod `Ready` before
you uncordon. After uncordoning, the next node's step 1 waits for storage to finish
rebuilding onto this node.

## Done

The workers are done when every node is `Ready`, schedulable, and runs the new kubelet, and
the last gate passed.
