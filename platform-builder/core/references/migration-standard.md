# Migration Standard & Major Upgrade Guide

This reference outlines the consumption protocol for `MIGRATION.md` when upgrading consumer
repositories across major releases of a shared CI or Helm library.

> **Shared reference.** Platform-agnostic: read by both the `gitlab` and `github` adapters.
> The protocol is identical on both platforms; only the consumer file names differ. Read the
> per-platform names from the invoking skill's SKILL.md.

---

## 1. Automated Migration Protocol

When upgrading a repository:

1. **Resolve the libraries.** `core/scripts/libraries.py` clones or locates each library at
   the requested ref and reports the path to its `README.md` and `MIGRATION.md`. Never guess
   a path, and never read a library you did not resolve this run.

2. **Let the engine work out what is owed.** `core/scripts/audit.py` detects the ref the
   consumer is pinned to — GitLab from `include: ref:`, GitHub from the job-level `uses:
   …@ref` — enumerates every release tag between that and the resolved target, and extracts
   each intermediate `MIGRATION.md` section **in order**. It prints them under `migration:`
   and carries them in `--json` as `migrations[]`.

   > **Multi-Version Law.** Upgrading `1.0.0` → `2.0.0` means applying `1.1.0`, then `1.2.0`,
   > then `2.0.0`. Never only the major. The engine enumerates the chain so a skipped release
   > is visible rather than assumed; applying each step is still yours.

   Two cases the engine reports rather than hides:
   - **Current version unknown** — the consumer is pinned to a branch or a SHA, not a release
     tag. The full chain is returned, because "we cannot tell what you applied" must not read
     as "you are up to date".
   - **No release section** — the version exists as a tag but `MIGRATION.md` has no entry for
     it. That is "nothing to do", and it is stated, not inferred from silence.

3. **Apply each step against the library's own documentation.** The `MIGRATION.md` section is
   prose; the task-specific pages linked from `README.md` and the library's canonical
   `values.yaml` are the source of truth
   for what the result should look like. Change the consumer's `.gitlab-ci.yml`,
   `.github/workflows/*.yml`, `chart/values.yaml` and `chart/templates/manifest.yaml` to
   match — ref bumps, renamed variables, boolean inversions, route migrations, schema updates.

   *Why the agent and not a script:* hardcoding migration tables into Python creates stale,
   high-maintenance code that breaks on every library release. The chain is mechanical, so the
   engine computes it; the edits are judgement, so the agent makes them.

4. **Re-audit.** `python3 core/scripts/audit.py <repo> --strict` — same engine, same run.

---

## 2. Dynamic Source-of-Truth Law: Reference `MIGRATION.md` Directly

> [!IMPORTANT]
> **Never Hardcode Static Migration Rules**:
> Skills, automated scripts, and AI agents **MUST NOT** hardcode static variable renames, boolean inversion tables, or deprecated anchor lists. Any examples found in documentation (such as mock `[1.0.0...2.0.0]` blocks) are purely sample reference standards and **not** actual migrations.
>
> The authoritative, active migration instructions are maintained exclusively in the root **`MIGRATION.md`** of each library:
>
> - **CI Template Library**: `<ci_repo>/MIGRATION.md`
> - **Helm Template Library**: `<helm_repo>/MIGRATION.md`
>
> When executing `platform update` or upgrading a consumer project:
>
> 1. **Fetch & Read**: Read `MIGRATION.md` and the `README.md` index from the paths
>    `core/scripts/libraries.py` resolved for this run. Follow only the affected topic links,
>    relative to their containing page and at that same ref. Never a path you guessed.
> 2. **Sequential Multi-Version Processing**: `core/scripts/audit.py` compares the consumer's
>    pinned ref against the resolved target and returns every intermediate version section,
>    in order, under `migrations[]`. Read them in that order.
> 3. **Dynamic AST / YAML Transformation**: Apply the actual renames, boolean inversions, stage reclassifications, and schema adjustments specified in `MIGRATION.md`.

### 2.1. Diff-and-Confirm Law: Surgical Migration vs Ground-Zero Re-scaffold

> [!CAUTION]
> **`platform update` is never `platform onboard`**:
> An update MUST NOT rewrite files from scratch templates, re-scaffold configurations, or reset custom application values back to baseline defaults.
>
> 1. **Preserve deliberate customizations**:
>    - Never revert custom variables such as `PROJECT_CACHE_KEY: "access"` or `"chat"` back to generic stack defaults (e.g. `"java"` or `"node"`).
>    - Never wipe, reset, or overwrite `configmapEnvs`, `secretEnvs`, custom replicas, resource requests/limits, probes, or volume mounts in `values.yaml`.
>    - Preserve custom CI pipeline jobs, scripts, and overrides.
> 2. **Apply only migration and schema diffs**:
>    - Apply strictly the ref bumps, renamed keys, inverted booleans, and schema changes mandated by `MIGRATION.md`.
>    - Use AI intelligence to differentiate between standard drift and intentional user customizations. If a user customization makes sense and complies with platform contracts, preserve it.
>    - If a user deviation appears to conflict with a required migration step, present a diff and ask the user rather than blindly overwriting.

---

## 3. Helm Template Library Architecture Migration: Unified Routes (`routes`)

When upgrading consumer repositories to the latest `helm-tpl-library` (incorporating Gateway API HTTPRoute and consolidated Ingress):

### 3.1. `chart/templates/manifest.yaml`

- **Action**: Delete `{{- include "tpl.ingress" . }}`.
- **Specification**: `manifest.yaml` must contain ONLY:

  ```yaml
  {{- include "tpl.deployment" . }}
  ```

- **Rationale**: `tpl.deployment` now directly invokes `{{- include "tpl.routes" . }}` at line 96. `tpl.ingress` is deleted from `helm-tpl-library`; attempting to include it causes fatal compilation failure.

### 3.2. `chart/values.yaml`

1. **Global Block**:
   - Remove `global.ingress` and `global.istio`.
   - Add `global.routes`:

     ```yaml
     global:
       routes:
         domain: "acme.local"
         ingressClass: ""
         tlsSecretName: "acme-tls"
         gateway:
           name: "default-gateway"
           namespace: "gateway-system"
           class: "gateway"
     ```

2. **Routes Block**:
   - Replace `virtualService:` with `routes:`:

     ```yaml
     routes:
       default:
         enabled: true
         ingress: true
         httpRoute: true
         host: "{{ $.Values.component }}.{{ $.Values.global.routes.domain }}"
         hosts: []
         ingressClass: ""
         tlsSecretName: ""
         gateway:
           name: ""
           namespace: ""
           class: ""
         parentRefs: []
         annotations: {}
         paths: []
     ```

3. **Environment & CORS Expressions**:
   - Replace `.Values.global.istio.gateway.host` with `.Values.global.routes.domain` across all public URLs and CORS allowlists in `configmapEnvs`.

### 3.3. `chart/values.schema.json`

- Replace `"virtualService"` in `properties` with `"routes": { "type": "object" }`.
- Update `$defs.globalSettings.properties`: replace `ingress` and `istio` with `routes`.

### 3.4. `chart/README.md`

- Re-run `helm-docs -c chart --template-files "README.gotmpl" --sort-values-order file --document-dependency-values` to synchronize documentation.
