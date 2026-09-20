# Security Review Core — Judgement, Not Pattern Matching

**Applies to every platform.** Read this first, then the addendum for the detected platform:
- `platforms/gitlab/references/security-addendum.md`
- `platforms/github/references/security-addendum.md`

Load during onboard (before writing a chart or Dockerfile) and during audit (after the rule
engine runs).

`core/scripts/audit.py` enforces what is *structural* — non-root UID, `.dockerignore` shape,
unpinned images, script style, job wiring. Those are exact and reproducible, so they live in
code and are tagged `[engine]`.

> [!NOTE]
> Base images are checked on both platforms, but for different things. **Pinning** — an
> untagged `FROM` or `:latest` — is platform-independent and always checked, with the remedy
> worded per platform (dependency proxy on GitLab, digest or mirror on GitHub). **Proxy
> routing** is GitLab-only, because GitHub has no Dependency Proxy to route through. A
> GitLab line that already reported as unproxied does not also report as unpinned.


**Everything below requires reading a value and reasoning about what it means.** A regex over
key names cannot do it, and adding more regexes makes the engine noisier without making it
smarter. Report these as `[judged]`.

---

## 1. Secrets — classify by *value*, not by key name

The engine greps for secret-ish key names. That misses the common cases and flags harmless ones.

Ask of every value, wherever it appears (`values.yaml`, `.env`, CI variables, compose files):

- **Does a URL embed a credential?** `postgres://user:pw@host/db`, `amqp://u:p@rabbit`,
  `https://token@github.com/...`, `mongodb+srv://...`. The *key* is `DATABASE_URL` — innocuous;
  the *value* is a live credential. **P0** when committed.
- **Is it high-entropy?** Long random-looking strings are keys even when the field is called
  `id`, `ref`, or `token_name`. Conversely `API_KEY: ""` or `API_KEY: <from-vault>` is fine.
- **Is a private key or certificate inlined?** `-----BEGIN ... PRIVATE KEY-----` anywhere.
- **Is it a real value that merely looks like a placeholder?** `changeme`, `admin`, `test123`
  shipped to production are credentials, not placeholders.
- **Is a genuinely non-secret value sitting in the secret store?** Over-classification has a
  real cost: it forces a Secret where a ConfigMap would do and obscures which values matter.
- **Is the secret *derived* before being printed?** Both platforms mask known secret values in
  logs, but not transformations of them: base64-encoding, `jq`-extracting, or slicing a secret
  defeats masking entirely.

Correct placement: literal non-sensitive → shared config → secret store. Chart specifics are in
[`helm-chart-standard.md`](./helm-chart-standard.md).

## 2. Chart posture — what the manifest actually grants

None of these are name-matchable; each needs a judgement about blast radius.

- **`hostPath` mounts.** Any is suspect; `/`, `/var/run/docker.sock`, `/etc`, or `/proc` is
  **P0** — it is a container escape. Ask what the workload genuinely needs.
- **`privileged: true`, `hostNetwork`, `hostPID`, `hostIPC`.** Each is a P0 unless the workload
  is demonstrably infrastructure (a CNI agent, a node exporter) and the reason is written down.
- **Added capabilities.** `NET_ADMIN`, `SYS_ADMIN`, `SYS_PTRACE` — justify or drop. `SYS_ADMIN`
  is effectively root.
- **`allowPrivilegeEscalation`** unset or true, and a missing `readOnlyRootFilesystem`, on a
  workload that never writes to its own filesystem.
- **ServiceAccount scope.** A role granting `*` verbs on `*` resources, or a cluster-wide
  binding where a namespaced Role suffices.
- **Missing resource limits.** Not confidentiality, but one pod can starve a node — P1 on
  shared clusters.
- **Ingress exposing an internal service.** A worker or internal API with a public host is
  usually a mistake.

## 3. Dockerfile posture — beyond the non-root check

- **Secrets in build args or layers.** `ARG NPM_TOKEN` / `ARG PIP_INDEX_URL` with credentials
  persist in image history even when a later layer deletes the file. **P0.** Use BuildKit
  secret mounts (`--mount=type=secret`) when a build genuinely needs one.
- **`COPY . .` with a weak `.dockerignore`.** The inverted allowlist exists to keep `.git`,
  `.env`, and local certs out of the image. Verify by reading what the allowlist actually
  admits, not that the file exists.
- **Unpinned base image.** A floating tag is unreproducible and silently changes the attack
  surface between builds. Pin an explicit tag — never `:latest`, never untagged — with a
  `# renovate:` annotation above it so the pin is maintained rather than forgotten.
- **Build tooling left in the final layer** — compilers and `curl` in a runtime image widen
  what an attacker can do after an RCE. A runtime stage built `FROM` a `*_BUILD_IMAGE` ships
  all of it by construction. **P1.**
- **A shim that is PID 1 but cannot act like it.** `[judged]` — an `ENTRYPOINT` script that
  forks or backgrounds work, rather than `exec`ing the service as its last action, stays
  PID 1 and will neither forward signals nor reap zombies. The container then ignores
  `SIGTERM` and is killed on the grace-period timeout at every rollout. Read the script's
  last effective line: a bare `exec <service>` is correct; anything that leaves children
  behind should `exec /usr/bin/dumb-init -- <service>`. Scripts that end in `main "$@"`, a
  `trap`/`wait` pair, or a supervisor loop need reading rather than pattern-matching, which
  is why this is judged and not enforced.

## 4. Pipeline posture (platform-independent half)

- **Credential lifetime and scope.** Prefer a short-lived, job-scoped token over a long-lived
  one. Where a static credential is unavoidable, confirm it is read-only unless the job pushes.
- **Masked and protected.** Anything secret must be both. An unmasked token appears in logs; an
  unprotected one is readable from an unprotected branch, which is the whole attack.
- **Secret echoed**, including indirectly via `set -x` or a constructed URL.
- **Third-party components from unpinned refs** in a job that holds credentials.
- **Failure modes that pass silently.** `curl` without `--fail`, `|| true` around an auth step.
  A job that reports success without doing its work is a security problem, not just a bug.
- **Cross-project publishing.** A job pushing to a registry outside its own project needs an
  explicitly granted credential. If it "just works", something is over-permissioned.

Platform-specific mechanics — GitLab's `CI_JOB_TOKEN` scoping and protected variables, GitHub's
`permissions:` block, `pull_request_target`, and OIDC — are in the addenda.

---

## Reporting

```
[P0] [judged]  Embedded credential   chart/values.yaml:42
     -> DATABASE_URL value contains an inline password (postgres://svc:hunter2@...).
        Move it to the secret store and source from the cluster secret manager.
```

A judged finding is a considered opinion and may be wrong — say what you saw and why it
concerns you, so the user can overrule you. A deterministic finding is a fact and should not be
hedged. Never present a judged finding as if the engine produced it.
