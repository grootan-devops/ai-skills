# Recovery

Stop the stage first. Every action here changes the cluster: show the user the evidence and
the exact command, and ask before running it. Record both in `log.md`. Problems specific to a
provider (its control-plane upgrade, node packages, runtime) are in that provider's
`recovery.md`.

When `kubectl` from your machine does not answer (it may reach the API through something that
runs in the cluster), use the provider's direct path to the API; for kubeadm that is `kubectl`
on a control plane with `/etc/kubernetes/admin.conf`.

## Kubernetes Service has no endpoints

Symptoms: pods log `dial tcp 10.96.0.1:443: connect: connection refused` (or your cluster's
first Service IP); CNI, CSI, policy-engine and operator pods crash or fail their init
containers; `kubectl` straight to a control plane still works. Check:

```bash
kubectl get endpointslice -n default -l kubernetes.io/service-name=kubernetes -o yaml
kubectl -n kube-system logs <kube-apiserver-pod> | grep -E 'unable to sync kubernetes service|failed calling webhook'
```

An empty EndpointSlice plus `failed calling webhook "<name>"` means an admission webhook with
`failurePolicy: Fail` blocks the API server from re-registering itself, and the webhook's own
backend cannot start because it has no route to the API either.

1. Find the webhook entry by name:

   ```bash
   kubectl get validatingwebhookconfigurations -o json \
     | python3 -c 'import json,sys; [print(c["metadata"]["name"], i, w["name"]) for c in json.load(sys.stdin)["items"] for i, w in enumerate(c.get("webhooks", []))]'
   ```

2. With the user's confirmation, set only that entry to `Ignore`. The `test` operation makes
   the patch fail if the index points at a different entry:

   ```bash
   kubectl patch validatingwebhookconfiguration <config> --type=json -p='[{"op":"test","path":"/webhooks/<index>/name","value":"<webhook-name>"},{"op":"replace","path":"/webhooks/<index>/failurePolicy","value":"Ignore"}]'
   ```

   For a mutating webhook, use `mutatingwebhookconfiguration`.

3. Within a few seconds the API server should fill the EndpointSlice. Then restart the pods
   that crashed while it was empty (CNI first).
4. The tool that owns the webhook (for example Kyverno) usually rewrites its configuration when
   it is healthy again, which undoes step 2. Make the permanent fix in the source: exempt the
   API server's own requests (`request.userInfo.username != 'system:apiserver'`), commit it to
   the GitOps repository, and let it sync before continuing.

Deleting the policy object does not help while the tool's controller is down: the webhook
entry stays registered until that controller removes it.

## A volume does not attach after a drain

Symptoms: `FailedAttachVolume ... the volume is currently attached to different node`, or
`Multi-Attach error`. Usually the old attachment is still being released; give it up to 10
minutes while the gate waits. Then:

```bash
kubectl get volumeattachments -o custom-columns=PV:.spec.source.persistentVolumeName,NODE:.spec.nodeName,ATTACHED:.status.attached,DELETING:.metadata.deletionTimestamp,ERROR:.status.attachError.message
kubectl get node <old-node> -o jsonpath='{.status.volumesInUse}'
```

- If the old node still lists the volume in `volumesInUse`, a pod there still uses it, or the
  kubelet did not unmount it: find that pod, or restart the kubelet on that node.
- Longhorn: check `kubectl -n longhorn-system get volumeattachments.longhorn.io <pv> -o yaml`
  for the attachment tickets, and the volume's engine and replicas.
- Delete a VolumeAttachment by hand only when its node is gone or powered off, and only after
  the user confirms. Deleting one for a running node can corrupt the volume.

## A drain does not finish

See [drain](./drain.md). Never use `--disable-eviction`, and never force-delete
a StatefulSet pod whose node is still running unless the user confirms that the old pod has
stopped; two copies of a database pod writing the same volume corrupt it.
