# k8s-upgrade

An agent skill that upgrades a Kubernetes cluster one minor version at a time and proves
nothing regressed. Entry point: [SKILL.md](./SKILL.md).

## Commands

Run one stage per request, in order. A stage refuses to start until the stages before it
passed.

| Command | Does | Asks for, when missing |
| --- | --- | --- |
| `k8s upgrade init` | Creates the cluster workspace in `k8s-upgrade-runs/`. | cluster name, provider, kubectl context |
| `k8s upgrade lint` | Checks the workspace offline. | which cluster, when there are several |
| `k8s upgrade check` | Cluster-admin, webhooks, deprecated APIs, provider preflight, the next version hop. | confirmation of the hop |
| `k8s upgrade prepare` | Baselines and backups. | snapshot confirmation |
| `k8s upgrade upgrade` | Control plane, then nodes, one confirmed step at a time; then `validate`. | confirmation of each step |
| `k8s upgrade validate` | Compares with the baseline and runs the smoke test. | confirmation before the smoke test |
| `k8s upgrade status` | Shows where each stage stands. | which cluster |

Commands use the `k8s-upgrade-runs/` workspace in the current directory, and otherwise ask
for its path.

## What it guards against

- Skipping a minor version, or moving nodes ahead of the control plane.
- Upgrading without a restorable backup.
- An admission webhook with `failurePolicy: Fail` that blocks the API server from
  re-registering in the `kubernetes` Service while its own backend is down, which cuts every
  pod off from the API.
- Draining the next node while replicated storage is still rebuilding or a volume is stuck
  attaching.
- Uncordoning a node whose CNI pod is not Ready.
- Declaring success while databases, PVC data, operators or critical pods differ from before.
- Running everything in one go: each stage is its own request, and a stage refuses to start
  until the stages before it passed.

## Providers

| Provider | Status |
| --- | --- |
| [kubeadm](./providers/kubeadm/README.md) | Supported: self-managed, over SSH; apt or dnf/yum nodes; containerd or CRI-O |
| EKS, AKS, GKE, Scaleway Kapsule | Planned. The layout and the provider contract are ready for them: [providers](./core/references/providers.md) |

## Layout

```text
k8s-upgrade/
├── SKILL.md  AGENTS.md  README.md
├── core/                         # the same for every provider
│   ├── references/               # stages, providers, workspace, preflight, version path,
│   │                             # baseline, drain, gates, verify, recovery
│   ├── scripts/                  # workspace.py and the cluster tools
│   └── assets/                   # workspace .gitignore, smoke-test manifests
└── providers/
    └── kubeadm/
        ├── README.md             # kubeadm steps per stage, scope, scripts
        ├── references/           # credentials, preflight, version path, backup,
        │                         # control plane, workers, recovery
        ├── scripts/              # provider.py hooks, SSH and node-side tools
        └── assets/               # control-plane.md and workers.md examples
```

## Stages

| Stage | What it does |
| --- | --- |
| `init` | Creates the workspace: `cluster.md` (provider, kubectl context), the provider's input files, `critical-extra.txt` |
| `lint` | Checks those inputs offline |
| `check` | Checks access and the cluster, finds the next version hop; changes nothing |
| `prepare` | The provider's preparation, baseline of pods, PVCs, databases and PVC usage, backups |
| `upgrade` | Control plane, then nodes, step by step with gates, then `validate` |
| `validate` | Compares with the baseline, runs the smoke test, writes the report |

`python3 core/scripts/workspace.py status <cluster-dir>` shows where a cluster is. Re-running a
stage clears the stages after it.

## Requirements

On the operator's machine: `kubectl` with a cluster-admin context, `python3` (standard library
only) and `bash`, plus what the provider needs (kubeadm: `ssh`, `curl`, and `sshpass` for nodes
with password authentication).

## Workspace

The skill keeps everything for one cluster in a workspace folder, by default
`./k8s-upgrade-runs/<cluster>/` in the directory the agent runs from. See
[workspace](./core/references/workspace.md). Credentials stay in that folder, mode 600,
ignored by git.

## Installation

Link or copy this folder where your assistant discovers skills, for example:

| Assistant | Location |
| --- | --- |
| Claude Code | `.claude/skills/k8s-upgrade` (project) or `~/.claude/skills/k8s-upgrade` |
| Codex and other `AGENTS.md` readers | reference `k8s-upgrade/AGENTS.md` from the project's `AGENTS.md` |
| Google Antigravity | `.agents/skills/k8s-upgrade` or `~/.gemini/config/skills/k8s-upgrade` |
| Cursor | `.cursor/skills/k8s-upgrade` |

## Core scripts

| Script | Purpose |
| --- | --- |
| `workspace.py` | `providers`, `init`, `lint`, stage gating (`require`, `record`, `status`), the per-cluster kubeconfig |
| `critical.py` | Detect critical workloads |
| `snapshot.sh` | Cluster state as JSON |
| `db_stats.py` | Database list and sizes per database pod |
| `pvc_usage.py` | Bytes used per PVC |
| `webhook_scan.py` | Admission webhooks that can lock the API server out of its Service |
| `gate.py` | Gate checks between nodes |
| `compare.py` | Pre/post comparison |
| `smoke_test.sh` | Throwaway namespace with pod, Service, PVC and route |

Provider scripts are listed in each provider's README.
