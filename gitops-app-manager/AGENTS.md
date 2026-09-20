# GitOps Application & Environment Manager

> **Entry point for agents that read `AGENTS.md` (Codex, Gemini CLI, and similar).**
> Claude Code reads `SKILL.md` via its YAML frontmatter. Both describe the same skill —
> **[`SKILL.md`](./SKILL.md) is the single source; read it now and follow it.**
> This file exists only so the skill is discoverable under either convention, and
> deliberately duplicates nothing beyond the map below.

## What this skill does

Onboards and maintains applications in GitOps repositories across **ArgoCD** (Helm apps and raw
manifest "Extras") and **Komodo** (Docker Compose stacks), and provisions the environments they
live in. It scaffolds the GitOps entries, injects the matching delivery block into the service's
`.gitlab-ci.yml`, and syncs only after a human review gate.

## Layout

```
SKILL.md                                  the runbook — start here
references/  argocd-gitops-contract        GitOps repo structure contract
             ci-templates-delivery-contract  injected CI blocks + delivery job wiring
             komodo-compose-standard       compose stack standard
             naming-conventions            app, branch and stack naming
scripts/     gitops-helper.py              introspection, rendering, CLI/context checks
assets/      root-app, extras-deployment, gitops-branch-chart, gitops-branch-values,
             komodo-agent-compose, komodo-smoke-compose  (templates)
```

## Commands

Seven workflows, described in full in `SKILL.md` §1, each gated by the human-in-the-loop
protocol in §2 — scaffold disabled, send the review link, activate only on an explicit go-call:

```
gitops onboard helm      <gitops_repo> <branch> <service> [chart_repo]
gitops onboard manifest  <gitops_repo> <branch> <service> <image>
gitops onboard komodo    <gitops_repo> <branch> <service> <image>
gitops bootstrap env     <product> <env> <gitops_repo> --type [argocd|komodo]
gitops bootstrap root    <gitops_repo> <branch> [server]
gitops audit             <gitops_repo> [branch]              # read-only
gitops diff values       <source1> <source2> [--repo-path <path>]
```

The helper behind them runs standalone; `--help` lists every flag:

```bash
python3 scripts/gitops-helper.py --introspect <repo>  --json    # repo introspection
python3 scripts/gitops-helper.py --check-clis                   # argocd / komodo / kubectl
python3 scripts/gitops-helper.py --check-context                # Kubernetes context safety
python3 scripts/gitops-helper.py --diff-values <src1> <src2>    # application schema diff
```

**Never sync or apply without the gate.** `gitops audit` never writes; every other command stops
at a review link and waits. A Kubernetes context check precedes anything that touches a cluster.

Requires Python 3.9+, and `git`, `argocd`, `kubectl` or the Komodo CLI on `PATH` depending on the
command. No agent-specific tooling: everything is plain Markdown and plain Python, so the skill
behaves identically under Claude, Codex, and Gemini.
