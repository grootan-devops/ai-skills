---
name: k8s-upgrade
description: >-
  Upgrade a Kubernetes cluster one minor version at a time, in six stages run one per request:
  `k8s upgrade init` (workspace and the provider's input files), `lint` (offline checks of the
  inputs), `check` (access, preflight, admission webhooks, next version), `prepare` (baseline
  of critical components, pods, PVCs, databases and PVC usage, backups), `upgrade` (control
  plane then nodes, gated node by node, then validate) and `validate` (compare with the
  baseline, smoke test with a throwaway namespace, PVC, Service and Ingress or HTTPRoute).
  Providers: kubeadm (self-managed, over SSH, apt or dnf/yum nodes, containerd or CRI-O).
  EKS, AKS, GKE and Scaleway Kapsule are planned and not yet supported; not for k3s, RKE2,
  Talos, or upgrading workloads, charts or Argo CD apps.
---

# Kubernetes Upgrade

This skill upgrades a Kubernetes cluster from its current version to the next allowed
version, and proves the cluster is in the same state afterwards. It works the same way in any
agent that can run a shell.

The stages, baseline, gates, validation and smoke test are the same for every cluster and live
in `core/`. How a cluster is reached, backed up and upgraded depends on its provider and lives
in `providers/<name>/`.

| Folder | What it holds |
| --- | --- |
| `core/references/` | [stages](./core/references/stages.md) (start here), [providers](./core/references/providers.md), and the shared step files: workspace, preflight, version path, baseline, drain, gates, verify, recovery |
| `core/scripts/` | `workspace.py` (init, lint, stage state, kubeconfig) and the cluster tools: `critical.py`, `snapshot.sh`, `db_stats.py`, `pvc_usage.py`, `webhook_scan.py`, `gate.py`, `compare.py`, `smoke_test.sh` |
| `core/assets/` | The workspace `.gitignore` and the smoke-test manifests |
| `providers/<name>/` | `README.md` with the provider's steps per stage, its `references/`, `scripts/` (with `provider.py`) and `assets/` |

## Check for updates first

At the start of every invocation, run `python3 core/scripts/update_check.py` from this skill's folder.

- **An update is available (exit 1):** tell the user the installed and latest release and the
  command it prints, and ask whether to update now. Run the update only on a yes, then reload
  this skill before you go on.
- **The installed release is unknown (exit 2, a copied install):** tell the user the latest
  release and the reinstall command it prints, and ask whether to reinstall now. Run it only
  on a yes, then reload this skill before you go on.
- **They decline, or it could not check (exit 2):** carry on with the installed release and
  name it in your report.

## Providers

| Provider | Status | Read |
| --- | --- | --- |
| `kubeadm` | Supported | [providers/kubeadm/README.md](./providers/kubeadm/README.md) |
| `eks`, `aks`, `gke`, `scaleway` | Planned | [providers](./core/references/providers.md) |

A cluster's provider is set once by `init`, in `cluster.md`. For a planned provider, say it is
not supported yet and stop; do not improvise its steps.

## Stages

Run exactly one stage per request, then stop and tell the user the result and the next stage.
`upgrade` is the only stage that runs another one: it ends by running `validate`.

| Stage | Request | Does | Changes |
| --- | --- | --- | --- |
| `init` | `k8s upgrade init <cluster>` | Creates the workspace: `cluster.md` (provider, kubectl context), the provider's input files, `critical-extra.txt` | Local files only |
| `lint` | `k8s upgrade lint <cluster>` | Checks the inputs offline | Nothing |
| `check` | `k8s upgrade check <cluster>` | cluster-admin, admission webhooks, deprecated APIs, the provider's access and preflight, the next version hop; creates the run folder | Nothing |
| `prepare` | `k8s upgrade prepare <cluster>` | Snapshot confirmation, the provider's preparation (OS updates for kubeadm), baseline of critical components, pods, PVCs, databases and PVC usage, the provider's backups | After confirmation |
| `upgrade` | `k8s upgrade upgrade <cluster>` | Control plane, then nodes, one confirmed step at a time with gates; then `validate` | The cluster |
| `validate` | `k8s upgrade validate <cluster>` | Post baseline compared with the prepared one; smoke test (namespace, PVC, pod, Service, Ingress or HTTPRoute, then deleted); report | Smoke-test namespace only, after confirmation |

`k8s upgrade status <cluster>` shows the provider, which stages passed and what comes next
(`python3 core/scripts/workspace.py status`).

## Running a stage

Every stage after `init` follows the same frame. `$C` is the cluster folder and `$P` the
provider folder named in `cluster.md`.

1. **Gate on the previous stages.** `python3 core/scripts/workspace.py require "$C" <stage>`.
   If it prints `BLOCKED`, tell the user which stage to run and stop.
2. **Pin the cluster.** `export KUBECONFIG="$(python3 core/scripts/workspace.py kubeconfig "$C")"`.
   Every `kubectl` call and script in the stage then talks to the context in `cluster.md`,
   whatever the user's current context is.
3. **Do the stage's steps:** the core steps in [stages](./core/references/stages.md), and the
   provider's steps for that stage in `$P/README.md`.
4. **Record the result.** `python3 core/scripts/workspace.py record "$C" <stage> pass|fail`
   (with `--run <run-folder>` in `check`). Re-running a stage clears every later stage, so a
   fresh `check` needs a fresh `prepare` before `upgrade`.
5. **Stop.** Report what passed, what failed, and the next stage.

## Hard rules

1. One minor version per run. A request that spans several minors runs as consecutive runs,
   each from `check` onwards.
2. One stage per request, in order. Never start a stage whose `require` is `BLOCKED`, and never
   continue into the next stage on your own (except `upgrade` into `validate`).
3. One node at a time (or one node of surge per pool). Never drain a node while another node
   is drained, a gate is failing, or storage is rebuilding.
4. Ask before every change: package updates, control-plane upgrades, each drain, each pod
   deleted to unblock a drain, each node or pool upgrade, the smoke test. A confirmation covers
   that one action only.
5. Run `gate.py` after the control plane, after every node and before the next node. A failed
   gate stops the stage; follow [recovery](./core/references/recovery.md) and the provider's
   recovery, and do not improvise a fix on a half-upgraded cluster.
6. Secrets never leave memory: no password, token, cloud credential or database credential in
   the transcript, the log, the report or a command line visible in `ps`.
7. Record every command run against the cluster, a node or a cloud API in the run's `log.md`,
   and every decision and confirmation in `plan.md`.
8. Do not edit GitOps-managed objects (policies, webhooks, Helm releases) on the cluster to get
   past a check. Report the object, its owner, and the fix in its source repository.
