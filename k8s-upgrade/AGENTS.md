# Kubernetes Upgrade

This is the agent-discovery entry point. Read [`SKILL.md`](./SKILL.md): the skill runs as six
stages (`init`, `lint`, `check`, `prepare`, `upgrade`, `validate`), one per request, gated by
`core/scripts/workspace.py`. The core steps of each stage are in
[`core/references/stages.md`](./core/references/stages.md); the provider's steps are in
`providers/<provider>/README.md` (today: [`kubeadm`](./providers/kubeadm/README.md)). Read only
the files of the stage you are running. Scripts run from any shell.
