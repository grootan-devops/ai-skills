# Preflight (check, prepare)

The checks every provider runs. Every check must pass, or the user must accept the specific gap
in writing in `plan.md`. `check` runs steps 2 to 4, all read-only; `prepare` runs step 1. The
provider's preflight adds its own checks (for kubeadm: SSH, node checks, OS packages).

Below, `$C` is the cluster folder and `$RUN` the run folder (or `$C/_pending`).

## 1. Snapshot confirmation

Ask the user whether a VM-level or storage-level snapshot exists for every control-plane node
(and, if they want one, every worker), taken after the last change to the cluster. Record the
answer and the snapshot names or IDs in `plan.md`. If the answer is no, stop and explain that
the provider's backup in prepare does not replace a node snapshot. Continue only if the user
explicitly accepts running without one.

## 2. Cluster-admin

```bash
kubectl config current-context
kubectl auth can-i '*' '*' --all-namespaces
kubectl get nodes -o wide
```

Stop unless `can-i` prints `yes`.

## 3. Deprecated API use

```bash
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis
```

List every deprecated API that has requests and is removed in the target minor (check
names it). Ask the user to migrate those clients first; record any accepted exception.

## 4. Admission webhooks that can lock the cluster

```bash
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json \
  | python3 core/scripts/webhook_scan.py --out "$RUN/preflight/webhooks.json"
```

When the API server restarts it removes its address from the `default/kubernetes`
EndpointSlice and writes it back. A webhook with `failurePolicy: Fail` that intercepts that
write, while its own backend is down, leaves the Service with no endpoints. Every pod then
loses the API, including the webhook's backend, and the cluster cannot recover by itself.

The scan reports, per webhook:

- `RISK`: `failurePolicy: Fail`, matches `endpoints`, `endpointslices` or `services` in
  `default`, or `leases` in `kube-system`, and no match condition exempts the API server's own
  user (`system:apiserver`). Stop. The fix belongs in the webhook's source (for a Kyverno
  policy, a match condition `request.userInfo.username != 'system:apiserver'`; for other
  tools, their exclusion setting). Do not patch a generated webhook configuration to pass
  this check.
- `DOWN`: `failurePolicy: Fail` and its service has no ready endpoint. Stop until the backend
  is healthy.
- `OK`.
