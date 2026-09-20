# Security Addendum — GitHub Actions

Read [`core/references/security-core.md`](../../../core/references/security-core.md) first. This
covers only what is specific to GitHub's execution and trust model.

---

## 1. The threat model that makes GitHub different

On GitLab a pipeline runs on your code with your credentials. On GitHub **anyone in the world
can open a pull request**, and several triggers will run something in response. Almost every
serious Actions incident is a variation on: *attacker-controlled input reached a context holding
the repository's secrets.*

Work outward from that question rather than down a checklist: **for each workflow, who can cause
it to run, and what does it hold while running?**

| Trigger | Attacker can trigger? | Secrets available? | Token |
|---|---|---|---|
| `pull_request` (fork) | Yes | **No** | read-only |
| `pull_request_target` | Yes | **Yes** | read/write |
| `issue_comment`, `issues` | Yes | **Yes** | read/write |
| `workflow_run` | Indirectly | **Yes** | read/write |
| `push`, `schedule`, `workflow_dispatch` | No (needs write access) | Yes | per `permissions:` |

The bold rows are where to spend attention. `pull_request` is the safe default precisely because
it withholds secrets.

## 2. Indirect untrusted input

The engine matches known `github.event.*` contexts in `run:` blocks. It cannot follow a value
that arrives another way. Trace by hand:

- **Through a step output** — `id: meta` capturing a PR title, later `${{ steps.meta.outputs.title }}`.
- **Through `env:` into a script file** — safe in the `run:` block, interpolated unsafely inside `script.sh`.
- **Through an action input** — `with: title: ${{ github.event.issue.title }}` where the action `eval`s it.
- **Through a branch name** — `github.head_ref` can contain shell metacharacters and is attacker-chosen.
- **Through a downloaded artifact** — from a fork PR run, unpacked in a privileged `workflow_run` job.

## 3. `workflow_run` — the bypass people forget

A common "fix" for `pull_request_target` is to build on `pull_request` (no secrets) then use
`workflow_run` to publish. `workflow_run` executes **in the base repo with full secrets**, so:

- Does it download an artifact produced by the untrusted run, then *execute* anything from it?
- Does it trust an artifact-supplied PR number or branch name when posting a comment or status?
- Does it check out code using a ref taken from the triggering run?

If yes to any, the privilege separation is defeated.

## 4. `permissions:` — beyond "is the block present"

The engine checks presence and flags `write-all`. It cannot judge *appropriateness*:

- Does a job that only builds and tests hold `contents: write`?
- Is `packages: write` at workflow level when only the publish job needs it?
- Does a workflow triggered by an untrusted event hold any write scope at all?
- Is `id-token: write` present on a workflow that never uses OIDC? Harmless alone, but it
  signals copied config nobody reviewed.

**The rule:** workflow level is `contents: read`; every elevation lives on the single job that
needs it, and each should be explainable in one sentence.

An absent `permissions:` block inherits the repository default, which on repos created before
Feb 2023 is read/write on every scope.

## 5. Runners and environments

- **Self-hosted runners on a public repository — P0.** A fork PR can execute on your
  infrastructure, and runners are not sandboxed between jobs by default.
- **Environment protection is the real production gate**, not the workflow file. A `deploy.yml`
  without a protected Environment (required reviewers, restricted branches) has no gate at all.
- **Environment secrets vs repo secrets.** Production credentials should be Environment-scoped
  so a workflow not targeting that Environment cannot read them.

## 6. Supply chain

- **Unpinned actions** — the engine flags these. Your judgement is *whose* action it is: an
  unpinned action from a single-maintainer repo with no releases is a different risk from
  `actions/*`.
- **Transitive actions.** A composite action you pinned may itself call an unpinned one. The
  pin protects one level only.
- **`npm install` / `pip install` on fork-controlled lockfiles** in a privileged context — a
  lifecycle script is arbitrary code execution.
- **OIDC over stored keys.** `id-token: write` plus a federated role removes the stored
  credential entirely. A static `AWS_SECRET_ACCESS_KEY` in repo secrets is a standing liability.
