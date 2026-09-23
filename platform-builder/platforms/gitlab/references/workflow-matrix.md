# WORKFLOW Execution Matrix, Adaptive Selection & Job Grouping

This reference covers only what the template library does **not** document about itself:

1. How to decide which `WORKFLOW` options a project may declare (§1–§3).
2. Which class of job each workflow is allowed to trigger (§4).
3. Open defects to report during an audit (§5).

For the library's own behaviour — what each workflow runs, the two-tier release model,
publishing and authentication — follow the relevant topic links from `<ci_templates>/README.md` at the resolved ref (§6).
Read migration notes for upgrades or compatibility checks.

The machine-readable map lives in [`workflow-map.json`](../workflow-map.json) and is loaded at
runtime by both scripts. There is no second copy.

---

## 1. Adaptive Declaration Law

> [!IMPORTANT]
> **A project declares only the workflows it can actually execute.**
> Never paste the full enum. An option whose backing module is not included produces a pipeline with **zero jobs** — a confusing dead end for whoever clicks it in the Run-pipeline UI.

Two rules, both enforced by the validator:

- **Option ⇒ Module.** Every declared option must have its providing module in `include:`.
- **Module ⇒ Option.** Every included module's options must be declared (otherwise the module is dead weight that still slows pipeline creation).

Declaring `WORKFLOW` locally is therefore **mandatory** for any project that is not the full app+image+chart shape. Inheriting the canonical variable from `common/.gitlab-ci.yml` is correct *only* when every module is included.

---

## 2. Module → WORKFLOW Options Map

> [!IMPORTANT]
> **The map lives in [`workflow-map.json`](../workflow-map.json), not here.** That file is the
> only copy, and `platforms/gitlab/ci_checks.py` loads it at runtime. Do not transcribe it into
> this document, into a script, or into `SKILL.md` — a second copy is a second thing to forget.

The map carries only what the library's README does **not** say: which module provides which
`WORKFLOW` option, the job that proves an option is active, and when a module applies. Job
lists, stage order and module descriptions are the library's to state, and are re-read from
the relevant pages linked from its `README.md` at the resolved ref.

Read it directly:

```bash
python3 -c "import json; print(json.dumps(json.load(open('platforms/gitlab/workflow-map.json')), indent=2))"
```

`build` and `deploy` map to no single provider. The reason for each is in the JSON's
`shared_options`; read it there rather than from a second copy here.

---

## 3. Project Shape Profiles

Pick the profile that matches, then take its option set verbatim.

### 3.1 Chart-only (Helm library or umbrella chart)

`Chart.yaml` present, no Dockerfile, no application manifest.

```yaml
variables:
  CHART_DIR: '.'          # ONLY when Chart.yaml is at the repo root
  WORKFLOW:
    value: "full-pipeline"
    options:
      - "full-pipeline"
      - "check"
      - "lint"
      - "chart-build-and-push"
      - "secret-scanning"   # requires secret-scanning/.gitlab-ci.yml in include:
```

- `CHART_DIR: '.'` is required whenever `Chart.yaml` sits at the repo root; omit it when the chart lives in `chart/` (the default).
- Omit `build` — there is nothing to compile.
- Omit every `image-*` option and the `image/` modules.
- `chart-scan` is optional; include it only if remote chart scanning by `TARGET_VERSION` is actually used.

### 3.2 Application, no image, no chart (e.g. AWS Amplify deploy)

```yaml
options: ["full-pipeline", "build", "check", "lint", "secret-scanning", "sonarqube"]
```

Omit `deploy` until a GitOps module is wired — Amplify deploys outside this library.

### 3.3 Application + image, no chart

```yaml
options: ["full-pipeline", "build", "check", "lint", "image-build-and-push", "image-scan", "secret-scanning", "sonarqube"]
```

### 3.4 Full service (app + image + chart + GitOps)

Every module included ⇒ the full enum applies ⇒ **omit the local `WORKFLOW` block** and inherit from `common/`.

### 3.5 Terraform module repository

```yaml
options: ["full-pipeline", "check", "lint"]
```

---

## 4. Job Grouping Contract

Each workflow triggers exactly one class of work. A job appearing outside its class is a defect.

| Workflow | Admits | Must NOT admit |
| --- | --- | --- |
| `check` | Existence & drift assertions only: `Tag:Tag Existence`, `Changelog:Check Existence`, `Migration:Check Existence`, `Chart:Check Existence`, `Chart:Check:README`, `Chart:Check:Dependency`, `Image:Check Existence`, `Terraform:Check:README` | Any build, push, scan, or lint job |
| `lint` | Linters only: `*:Lint`, `Chart:Lint`, `Chart:Values:Lint`, `Docker:Lint`, `Changelog:Lint`, `Migration:Lint`, `YAML:Lint`, `Terraform:Validate/Lint` | Drift/existence checks, dependency downloads, builds |
| `build` | stack-scoped dependency download (for example `Node:Dependency:Download`) → `Project:Build` → `Project:Unit:Test` | Image or chart packaging, pushes |
| `image-build-and-push` | `build` class **plus** `Image:Build`, `Image:Push`, `.Image:Test`, `Image:Scan` | Chart jobs |
| `chart-build-and-push` | `Chart:Lint`, `Chart:Check Existence`, `Chart:Check:README`, `Chart:Build`, `Chart:Push`, `Chart:Scan` | `Chart:Values:Lint`, `Chart:Check:Dependency` (they belong to `lint` / `check`), any image or app-build job |
| `image-scan` / `chart-scan` | The matching `*:Scan` job only | Existence checks, builds |
| `sonarqube` | `Sonarqube` | Everything else |
| `secret-scanning` | `Git:Secret:Scan` | Everything else |
| `license-scanning` | `License:Scan` | Everything else |
| `sbom-scanning` | `SBOM:Generate`, `SBOM:Scan` | Everything else |
| `deploy` | `Workflow:Validate:Variables`, `Validate:*`, `Deploy:*` | Any build or scan |

**Disabling an inherited job.** When a module must be included but one of its jobs does not apply, override it in the project `.gitlab-ci.yml` rather than forking the template:

```yaml
Chart:Scan:
  rules:
    - when: never
```

Common cases: `Trivy:Cache:Warm` (no Trivy-backed scan is offered) and `Chart:Scan` (config scanning handled elsewhere).

---

## 5. Known Gating Defects in the Template Library

Flag these during `platform audit`; they break §4 until fixed upstream.

| Location | Defect |
| --- | --- |
| `common/.gitlab-ci.yml` `.trivy-cache-rules` | Has a bare `- exists: main.tf` rule with no `if:` ⇒ `Trivy:Cache:Warm` runs in **every** workflow for any repo containing `main.tf`. |
| `common/.gitlab-ci.yml` `.common-init-rules` | Excludes the scan workflows (`chart-scan`, `image-scan`, `license-scanning`, `sbom-scanning`), yet those scan jobs `needs: Common:Init` with `optional: true` ⇒ they run **without `init.env`**, so `TAG` and the registry suffixes are unset and the scan silently targets the wrong artifact. |
| `terraform/.gitlab-ci.yml` `Terraform:Check:README` | A drift check gated to `lint` instead of `check`, unlike every other `*:Check:README`. |
| `common/.gitlab-ci.yml` | Dead anchors defined but extended by nothing: `.master-fast-track-rules`, `.mr-only-rules`, `.full-and-mr-rules`, `.deploy-workflow-rules`, `.retry-with-script-failure`. The last is a missed resilience win — wiring it into network-bound jobs (stack-specific dependency-download jobs, `*:Push`, `Release:Upload`) is cheap. |
| library-wide | **No job declares `timeout:`.** The ArgoCD/Komodo poll loops and `Terraform:Module:Test` are bounded only by their own variables; an empty timeout variable lets a job run to the project-wide limit. |
| `deploy/gitops/*` | GitOps `git push` jobs have no `resource_group:` and no rebase-retry, so concurrent environment deploys race on the same branch. |

**Recently fixed** (do not re-report): terraform jobs are now gated by `.terraform-workflow-rules` / `.terraform-scan-rules` / `.terraform-publish-rules`; `.terraform-common` no longer carries an invalid `rules:` mapping; the Go/Python lint anchors now use `optional: true`; the Komodo deploy `curl` calls use `--fail-with-body`; `.image-registry-login` and the buildah credential path now fail fast like `.chart-login`.

---

## 6. Library Behaviour Lives in the Library's Own Docs

The **two-tier release model** and the **per-workflow stage/job dispatch tables** used to be
duplicated here. They are not skill knowledge — they are facts about the template library,
and the library documents them itself. Duplicating them guaranteed they would drift.

**Follow the relevant topic links at the resolved source:**

| Question | Authoritative source |
| --- | --- |
| What does each `WORKFLOW` value run? | `<ci_templates>/README.md` → pipeline lifecycle → *Available Manual Workflows* + *Comprehensive Execution Matrix* |
| Which jobs are in which stage? | `<ci_templates>/README.md` → module catalog → relevant module's job table |
| What changed between library versions? | `<ci_templates>/MIGRATION.md` |
| How are charts/images published and authenticated? | `<ci_templates>/README.md` → configuration → chart/image publishing and authentication |

`core/scripts/libraries.py` resolves the library and prints these paths. `platform onboard`,
`platform update`, and `platform audit` all read them before acting — see SKILL.md §0.

What stays in *this* file is only what the library does not state: the adaptive declaration
law (§1), the machine-readable map (§2), the shape profiles (§3), the grouping contract (§4),
and the open defects (§5).
