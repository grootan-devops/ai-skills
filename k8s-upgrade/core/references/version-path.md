# Version path (check)

A run moves the cluster by exactly one hop. This part of `check` decides which hop and gets
the user to confirm it. The rules here hold for every provider; the provider's version-path
steps add how to read its versions and what it offers.

## Current version

Read all of these and write them to `plan.md`:

```bash
kubectl version -o json            # serverVersion.gitVersion
kubectl get nodes -o custom-columns=NAME:.metadata.name,KUBELET:.status.nodeInfo.kubeletVersion
```

Stop if:

- the control plane is mid-upgrade or its components differ (the provider's version-path
  steps say how to read them): finish that first, as its own run;
- any kubelet is a newer minor than the API server;
- any kubelet is an older minor than the API server. Upgrade those nodes to the control
  plane's version first, as their own run; a hop starts with every node on one minor.

## Allowed hops

Let the cluster run `A.B.P`. The hop is one of:

| Hop | When |
| --- | --- |
| `A.B.P` → latest `A.B.x` | A newer patch of the current minor exists |
| `A.B.x` → latest `A.(B+1).y` | The cluster is on the latest patch of its minor, or the user chooses to move to the next minor directly |

Never skip a minor, never target a pre-release (`-alpha`, `-beta`, `-rc`), and never target a
version the provider does not offer. A request for a version further away becomes a
list of hops; do the first one in this run.

Some providers only offer the next minor, or pick the patch themselves; their version-path
steps say which hops exist. Recommend the patch hop first when one is available, since it brings the fixes the next minor
was tested against. Show the user both options when both are allowed.

## Compatibility of what runs on the cluster

Before confirming a minor hop, for every component in the critical list (prepare builds it;
for this check, run `python3 core/scripts/critical.py "$C/_pending/pre"` now):

1. Find its installed version (image tag or chart version).
2. Look up the component's own documented Kubernetes support for the target minor (its
   release notes or support matrix: CNI, CSI/storage, ingress or gateway, policy engines,
   operators, Argo CD or Flux).
3. Mark it `supported`, `unsupported` or `unknown`.

Also read the target minor's changelog, "Urgent Upgrade Notes" section
(`https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-<MAJOR.MINOR>.md`),
and list every item that applies to this cluster.

Stop on any `unsupported` component. Show `unknown` ones to the user and record whether they
accept them.

## Confirmation

Show the user: current version per node, the proposed hop, the alternative hop if any, the
provider's own findings (for kubeadm, the runtime per node and its target version), the component table, and the urgent notes
that apply. Record the confirmed hop in `plan.md`, then
name the run folder `<from>-to-<to>-<YYYYMMDD-HHMM>`.
