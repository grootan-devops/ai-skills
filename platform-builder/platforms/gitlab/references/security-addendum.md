# Security Addendum — GitLab CI

Read [`core/references/security-core.md`](../../../core/references/security-core.md) first. This
covers only what is specific to GitLab's execution and trust model.

---

## 1. The threat model that makes GitLab different

GitLab's default posture is the inverse of GitHub's. A pipeline normally runs on your code with
your credentials, and outside contributors cannot trigger it without repository access — so
there is no `pull_request_target` equivalent to get wrong.

The pressure moves elsewhere: **token scope, variable exposure, and cross-project access**.

| Context | Who can trigger | Secrets available |
|---|---|---|
| Push / MR from a branch in the project | members with write access | all, subject to protection |
| MR from a **fork** | any user | **no protected variables**; `CI_JOB_TOKEN` is fork-scoped |
| Scheduled pipeline | schedule owner | runs as that user — check who owns it |
| Downstream/multi-project trigger | upstream project | inherits what the trigger passes |

**Fork MRs are the closest analogue to GitHub's fork threat**, and GitLab handles it by
withholding protected variables. The failure mode is therefore the opposite one: someone marks a
variable *unprotected* to "make the fork pipeline work", and now every unprotected branch can
read it.

## 2. `CI_JOB_TOKEN` scope

- Scoped to the current project and expires with the job — prefer it over any long-lived token.
- It **cannot** reach another project's registry or API unless that project adds yours to its
  **Settings → CI/CD → Token Access** allowlist. If a cross-project push "just works", check
  whether someone widened that allowlist or substituted a personal token.
- A PAT or deploy token used where `CI_JOB_TOKEN` would do is a standing liability. When one is
  genuinely needed, confirm the scope: `read_registry` only, unless the job actually pushes.

## 3. Variables

- **Masked *and* protected.** Masked keeps it out of logs; protected keeps it off unprotected
  branches. A secret with only one of the two is exposed by the other path.
- Masking has format constraints — a value that fails them is silently *not* masked. Verify
  rather than assume.
- **Group-level variables propagate to every project in the group.** Ask whether a credential
  set at the group really needs that reach.
- `CI_DEBUG_TRACE` in any form is a secret disclosure; it prints the full environment.

## 4. Rules and gating

- A job with **no `rules:` runs in every workflow**, including ones that should not reach it —
  a publish job triggered by a `lint` dispatch is a real outcome, not a hypothetical.
- `when: manual` is a convenience, not a security control: anyone who can run the pipeline can
  press the button. Use protected environments and protected branches for an actual gate.
- `rules:` entries without `when:` default to `on_success`. A cleanup or teardown job that
  inherits that default never runs after the failure it exists to clean up.

## 5. Runners

- **Shared runners on a public project** execute fork MR code. Confirm no protected variable is
  reachable from that pipeline.
- A runner with `privileged = true` (needed for dind) can escape to the host. Confirm it is
  isolated and not shared with unrelated projects.
- Tag-pinned jobs landing on a long-lived self-hosted runner inherit whatever the last job left
  on disk — cache and workspace poisoning between projects is possible.

## 6. Supply chain

- **`include:` from another project pins a `ref:`.** A branch or tag ref is mutable, so the
  included template can change under you; a commit SHA cannot. Weigh this against the update
  friction a SHA pin creates.
- A repository that includes **itself** must use `local:` — a remote pinned `ref:` means the
  pipeline validates a frozen old copy of the templates rather than the change under review.
- Base images and tools pulled from public registries should route through the **Dependency
  Proxy** (`${CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}/<image>`) or an internal mirror.
