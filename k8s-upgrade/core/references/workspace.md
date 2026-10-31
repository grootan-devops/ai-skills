# Workspace (init, lint)

Every run keeps its inputs, evidence and decisions in one folder per cluster. Create the
layout when it is missing; when it exists, check it and reuse it.

## Layout

The default root is `./k8s-upgrade-runs/` in the directory the agent runs from. The user may
name another root.

```text
k8s-upgrade-runs/
├── .gitignore                      # from core/assets/gitignore.example
└── <cluster>/
    ├── cluster.md                  # provider and kubectl context of this cluster
    ├── <provider input files>      # kubeadm: control-plane.md, workers.md (mode 600)
    ├── critical-extra.txt          # optional: namespace/kind/name lines the user marks critical
    ├── kubeconfig                  # only that context, written by each stage, mode 600
    ├── state.json                  # which stages passed, and the current run folder
    └── <from>-to-<to>-<YYYYMMDD-HHMM>/
        ├── plan.md                 # versions, hop, confirmations, decisions, in order
        ├── log.md                  # every command run against the cluster or a node, with its time
        ├── report.md               # validate verdict
        ├── preflight/              # the provider's check output, webhook scan
        ├── pre/                    # baseline before the upgrade
        ├── post/                   # the same files after the upgrade
        └── gates/                  # gate.py output, one file per run of it
```

`<cluster>` is a short name the user chooses (for example the kubeconfig context name). Name
the run folder once the version hop is confirmed in check; until then, write to
`<cluster>/_pending/` and rename it.

## init

Ask the user which provider runs the cluster, then:

```bash
python3 core/scripts/workspace.py providers          # the providers this skill supports
python3 core/scripts/workspace.py init <root> <cluster> --provider <name> [--context <kubectl-context>]
```

It creates what is missing and overwrites nothing:

- `<root>/.gitignore` from `core/assets/gitignore.example`;
- `cluster.md` with `provider:` and `context:` (the context given, else the current one);
- the provider's input files from `providers/<name>/assets/`, credential files mode 600;
- `critical-extra.txt` with a comment header.

A provider without a folder under `providers/` is refused, with the list of supported ones.
Ask the user to fill in the input files themselves. Do not ask them to paste credentials into
the conversation.

## lint

`python3 core/scripts/workspace.py lint <root>/<cluster>` checks, without touching the cluster
or the nodes:

- the provider's own checks of its input files (for kubeadm, see
  [credentials](../../providers/kubeadm/references/credentials.md));
- git ignores the provider's credential files and `kubeconfig`, when the root is inside a
  repository;
- `kubectl` and `python3` are installed;
- the context in `cluster.md` exists in the kubeconfig;
- each `critical-extra.txt` line is `namespace/kind/name`.
