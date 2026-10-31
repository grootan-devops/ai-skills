# Providers

The stages, the baseline, the gates, validation and the smoke test are the same for every
cluster; they live in `core/`. How a cluster's control plane and nodes are reached, inspected,
backed up and upgraded differs per provider; that lives in `providers/<name>/`. A cluster's
provider is the `provider:` line of its `cluster.md`, set by `init`.

## Supported and planned

| Provider | Status | Control plane | Nodes |
| --- | --- | --- | --- |
| `kubeadm` | Supported | Static pods on control-plane nodes, upgraded with `kubeadm` over SSH | kubelet packages over SSH, drained one at a time |
| `eks` | Planned | Managed by AWS | Managed node groups, self-managed groups, Karpenter, Fargate |
| `aks` | Planned | Managed by Azure | Node pools |
| `gke` | Planned | Managed by Google Cloud | Node pools, Autopilot |
| `scaleway` | Planned | Kapsule / Kosmos, managed by Scaleway | Pools |

A planned provider is refused by `workspace.py init` until its folder exists.

## What a provider supplies

```text
providers/<name>/
├── README.md              # the provider's steps per stage (check, prepare, upgrade) and its scope
├── references/            # step files the README links (preflight, version path, backup, upgrade, recovery)
├── scripts/
│   ├── provider.py        # hooks for core/scripts/workspace.py (below)
│   └── ...                # the provider's own tools
└── assets/                # examples of its input files
```

`providers/<name>/scripts/provider.py` defines, for `workspace.py`:

| Name | Type | Used by |
| --- | --- | --- |
| `INIT_FILES` | `{file name: example in assets/}` | `init` copies each missing file into the cluster folder |
| `SECRET_FILES` | `[file name]` | `init` sets them to mode 600; `lint` requires git to ignore them |
| `lint(cluster_dir)` | returns `(notes, problems)` | `lint` adds them to its own offline checks |

The provider's `README.md` covers each stage that has provider steps:

| Stage | The provider must |
| --- | --- |
| `check` | Prove access (SSH, cloud credentials and permissions); read the current version of the control plane and every node or pool; say which versions it offers and which hops are possible; run its own preflight (node health, quotas, add-on versions it manages) |
| `prepare` | Say what the snapshot confirmation covers for this provider; take its backups (etcd and configuration for kubeadm; for managed providers, the cluster and node-pool configuration and anything the provider does not keep); apply OS updates if it manages the OS |
| `upgrade` | Upgrade the control plane, then nodes one at a time (or pool by pool with a surge of one), draining with [drain](./drain.md) where the provider does not drain itself, and running the gate after each step |
| recovery | Its own failures, beside the generic [recovery](./recovery.md) |

Core pieces a provider reuses as they are: [stages](./stages.md), [workspace](./workspace.md),
[preflight](./preflight.md), [version path](./version-path.md), [baseline](./baseline.md),
[drain](./drain.md), [gates](./gates.md), [verify](./verify.md), [recovery](./recovery.md) and
every script in `core/scripts/`.

## Adding a provider

1. Create `providers/<name>/` with the layout above and a `provider.py` with the three hooks.
2. Write its `README.md` with the steps for `check`, `prepare` and `upgrade`, and its scope
   (what it supports and what stops the run).
3. Check that the core gate holds on that provider. A managed control plane shows no
   control-plane nodes, so `kubernetes-endpoints` checks only that the Service has endpoints.
   CNI pods must match the CNI pattern in `core/scripts/gate.py`; extend it if needed.
4. Add the provider to the table above and to the provider list in `SKILL.md` and `README.md`.
