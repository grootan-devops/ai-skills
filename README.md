# AI Agent Skills

[Compatibility](./COMPATIBILITY.md) · [Security](./SECURITY.md) · [Contributing](./CONTRIBUTING.md)

This directory contains reusable, production-hardened **AI Agent Skills**. Skills are modular packages of procedures and validation scripts that transform AI coding assistants into specialized platform engineers; the standards they apply live in the library repositories, read at the release a project pins.

---

## 1. Available Skills Catalog

| Skill Name | Supported Platforms | Description |
| --- | --- | --- |
| [`terraform-module-builder`](./terraform-module-builder/) | AWS reference modules; other providers require schema evidence | Terraform module creation, updates, and audits with provider schema checks, migration review, and repository-native validation. |
| [`terraform-module-consumer`](./terraform-module-consumer/) | Terraform, Terragrunt, provider-agnostic modules | Compose existing versioned modules into environment infrastructure configurations, with state-safe validation and no direct managed resources or apply. |
| [`platform-builder`](./platform-builder/) | GitLab CI/CD, GitHub Actions, Docker, Helm, Kubernetes, Terraform pipelines, Argo CD GitOps (Komodo planned) | Unified CI/CD, container, Helm and GitOps platform engineering, from onboarding a repository to running it in an environment. One engine with per-platform adapters measures a repository against the CI and Helm library releases it pins, and plans upgrades across their migration notes. Per-backend GitOps workflows bootstrap, clone and audit environments; add Helm, private-chart or manifest apps that meet the cluster's workload rules; promote sandbox stacks with their data; and wire each app's CI deploy job, automatic or manual. Supersedes the former `gitops-manager`, `gitlab-platform-builder` and `github-platform-builder`; the last two remain available as aliases inside it. |
| [`k8s-upgrade`](./k8s-upgrade/) | Kubernetes: kubeadm (EKS, AKS, GKE and Scaleway Kapsule planned) | Upgrade a cluster one minor version at a time in six gated stages: workspace and inputs, offline lint, preflight with admission-webhook and version-path checks, baseline and backups, control plane then nodes gated node by node, and validation against the baseline with a smoke test. |
| [`observability-builder`](./observability-builder/) | Any language or framework, OpenTelemetry, OTLP backends (OneUptime, Grafana, Dynatrace, New Relic), Langfuse | End-to-end observability from one `observability.yml`: access and application logs, traces with function spans, ops metrics on `/metrics`, errors, profiling, RUM and session replay, and LLM, agent, RAG and MCP telemetry for Langfuse or OneUptime's AI views, with spend per user and team. Instruments services and frontends, verifies against a local receiver, and audits telemetry for leaked secrets and prompts. |

### From application to environment

`platform-builder` takes a project from its repository to an environment:

1. **`platform onboard <repo>`** writes the project's chart, Dockerfile and CI. Its report ends
   with the next step.
2. **The project's first full CI run** publishes the chart and the image.
3. **`platform env add` / `platform app add <repo>`** reads the project's chart, image and CI
   pin from its own files, and checks both artifacts are published. If they are not, it offers
   to run that pipeline, and only runs it on a yes. It then adds the app with no chart or image
   questions.
4. **The same command wires the app's CI deploy job.** The only question is whether it runs
   automatically or manually, per environment. `platform audit` validates the result.

Maintainer regression checks for the platform checker live in
[`platform-builder`](./platform-builder/README.md#maintainer-regression-checks).
The Terraform migration detector's checks live in
[`terraform-module-builder`](./terraform-module-builder/README.md#local-tools).

---

## 2. Installation

Install a skill with the [Skills CLI](https://skills.sh/):

```bash
npx skills add https://github.com/grootan-devops/ai-skills \
  --skill platform-builder
```

Use `--global` for a user-level installation:

```bash
npx skills add https://github.com/grootan-devops/ai-skills \
  --skill platform-builder \
  --global
```

Swap `--skill` for any supported skill in the catalog above — `terraform-module-builder`,
`terraform-module-consumer`, `platform-builder`, `observability-builder` or
`k8s-upgrade`.
Repeat the command to install more than one.

Each skill is self-contained: the CLI copies that directory and nothing else, so a skill
never reaches outside its own folder for a reference.

Every skill checks for a newer ai-skills release each time it runs. For a git checkout, it
compares the tag, or else the repository's root `VERSION`, and the branch's commits with the
remote. A copied install doesn't record its release, so the check names the latest release and
offers the reinstall command. When an update exists, the agent shows the command
(`npx skills add …`, `git pull`, or a checkout of the new tag) and asks before running it.
Offline, the skill carries on with the installed release.

---

## 3. Manual Integration Instructions by AI Tool

If you prefer to configure your environment manually, clone the repository first — every
instruction below points at that clone, and none of them copies a skill into your project.

```bash
git clone https://github.com/grootan-devops/ai-skills.git ~/.ai-skills
git -C ~/.ai-skills checkout "$(git -C ~/.ai-skills describe --tags --abbrev=0 origin/main)"
```

Pin a tag rather than tracking a branch: a branch moves, and a skill that changes underneath
a project changes what the agent does to it without anything in the project's history saying
so.

Where the project should carry its own skill version, add the repository as a submodule
instead. The revision is then recorded in the project's history, and a fresh checkout gets
the same skill:

```bash
git submodule add https://github.com/grootan-devops/ai-skills.git .ai-skills
git -C .ai-skills checkout "$(git -C .ai-skills describe --tags --abbrev=0 origin/main)"
```

The examples below use `~/.ai-skills` and name `terraform-module-builder`. Substitute the
submodule path if you took that route, and any skill from the catalog above — each lives at
the repository root.

### 3.1. Google Antigravity (AGY)

Antigravity automatically discovers skills placed in `.agents/skills/` or declared in `.agents/skills.json`.

- **Project-Level (Workspace)**:

  ```bash
  mkdir -p .agents/skills
  ln -sfn ~/.ai-skills/terraform-module-builder .agents/skills/terraform-module-builder
  ```

  Or register the path in `.agents/skills.json`:

  ```json
  {
    "entries": [
      { "path": ".ai-skills/terraform-module-builder" }
    ]
  }
  ```

- **Global (All Projects on Machine)**:

  ```bash
  mkdir -p ~/.gemini/config/skills
  ln -sfn ~/.ai-skills/terraform-module-builder ~/.gemini/config/skills/terraform-module-builder
  ```

- **Activation**: Antigravity automatically indexes the skill's `name` and `description`. Trigger it by prompting:
  - `"terraform-module audit path/to/module"`
  - `"terraform-module add <registry-url> <name>"`
  - `"terraform-module update path/to/module"`
  - Or invoke bare: `"terraform-module"` (displays the interactive 3-workflow menu).

---

### 3.2. Cursor AI

Cursor supports project-specific rules (`.cursor/rules/*.mdc`) and agent skills (`.cursor/skills/`).

- **Installation**:

  ```bash
  # 1. Link skills folder
  mkdir -p .cursor/skills
  ln -sfn ~/.ai-skills/terraform-module-builder .cursor/skills/terraform-module-builder

  # 2. Create Cursor Rule (.cursor/rules/terraform-module-builder.mdc)
  mkdir -p .cursor/rules
  ```

  Create `.cursor/rules/terraform-module-builder.mdc`:

  ```yaml
  ---
  description: Industrial-grade Terraform module engineering skill
  globs: **/*.tf, **/*.tfvars, **/*.tftest.hcl, **/*.hcl
  alwaysApply: false
  ---

  # Skill: terraform-module-builder

  When authoring, auditing, testing, or refactoring Terraform modules:
  - Refer directly to `.cursor/skills/terraform-module-builder/SKILL.md` for command workflows.
  - Apply the standards in the terraform-modules repository docs the skill names.
  - Execute audit scripts from `.cursor/skills/terraform-module-builder/scripts/`.
  ```

- **Activation**: Cursor Agent auto-activates this rule whenever you open or edit Terraform files, or when you tag `@terraform-module-builder` in chat.

---

### 3.3. Anthropic Claude (Claude Code & Claude Projects)

Claude Code supports project-level skills and `CLAUDE.md` instruction files.

- **Claude Code Installation**:

  ```bash
  mkdir -p .claude/skills
  ln -sfn ~/.ai-skills/terraform-module-builder .claude/skills/terraform-module-builder
  ```

  Add the skill reference to your project's `CLAUDE.md`:

  ```markdown
  ## Skills
  - **terraform-module-builder**: Located at `.claude/skills/terraform-module-builder/SKILL.md`. Consult this runbook whenever designing, auditing, testing, or documenting Terraform modules.
  ```

- **Claude Projects (Web)**:
  Upload `SKILL.md` (and any `references/` it has) into your Claude Project's **Project Knowledge**, together with the library documentation it reads.

---

### 3.4. Kiro / Kirao IDE

Kiro recognizes skills in `.kiro/skills/` or standard `.agent/skills/`.

- **Installation**:

  ```bash
  mkdir -p .kiro/skills
  ln -sfn ~/.ai-skills/terraform-module-builder .kiro/skills/terraform-module-builder
  ```

- **Activation**: Kiro reads `.kiro/skills/terraform-module-builder/SKILL.md` when executing infrastructure automation tasks or when prompting with `terraform-module audit` or `terraform-module add`.

---

### 3.5. OpenAI / Codex / ChatGPT

- **Custom GPTs**:
  Create a Custom GPT (e.g. "Terraform Module Platform Architect"), paste the contents of `SKILL.md` into the **Instructions**, and upload the library documentation it reads into **Knowledge**.
- **Codex / API Agents**:
  Symlink or place the skill into `.codex/skills/` or pass `SKILL.md` as a system prompt directive.

---

### 3.6. Universal Multi-Agent Frameworks (Windsurf, Roo, Continue, GitHub Copilot)

Most modern agentic tools natively parse `.agent/skills/` or workspace instruction files:

```bash
mkdir -p .agent/skills
ln -sfn ~/.ai-skills/terraform-module-builder .agent/skills/terraform-module-builder
```

For **GitHub Copilot Workspace**, add to `.github/copilot-instructions.md`:

```markdown
For Terraform module tasks, follow the architectural and security standards defined in `.agent/skills/terraform-module-builder/SKILL.md`.
```

---

## 4. Skill Architecture Standard

Every skill in this repository follows the standard layout:

```text
skills/<skill_name>/
├── SKILL.md          # Required: YAML frontmatter + the workflow instructions; first step: the update check
├── scripts/          # Optional: Automated CLI linters, generators, and validators
└── references/       # Optional: step-by-step instructions for one workflow
```

Standards, contracts and starter templates are not copied into a skill. They live in the
library repositories (`gitlab-ci-library`, `github-ci-library`, `helm-tpl-library`,
`argocd-gitops-tpl-library`, `terraform-modules`), and a skill reads them from the library's
README index at the release the project pins — so a library release needs no skill release.
`observability-builder` is the exception: no library holds its standard, so the skill bundles
it in its own `references/` and `assets/`.

What belongs in a skill:

- **Yes:** procedures, generic scripts, and pointers to a library's README index. A skill may
  name a library's stable public interface (job, input, value and include names, the names its
  templates derive), which only a new library major can change.
- **No:** a library version, a statement about what a library release does, a default value, a
  library rule or library upgrade steps. Those belong in the library's docs, or in the data file
  its release ships for the checks (see [COMPATIBILITY.md](./COMPATIBILITY.md)).
  `platform-builder/core/scripts/tests/test_entry_files.py` keeps versions and "current
  library" claims out of the entry files.

Each skill resolves its library the same way: an explicit source in the request wins, then
the release the project pins, then — for a new project — the library's latest release tag.

| Skill | Library | Resolved from, highest first |
| --- | --- | --- |
| `platform-builder` | `gitlab-ci-library`, `github-ci-library`, `helm-tpl-library` | a `--<library>` flag, `$PLATFORM_BUILDER_LIB_<NAME>`, the repository's `.platform-builder.json`, the release the repository pins, then the latest release (`--latest` skips the pin, for an upgrade) |
| `platform-builder` (GitOps) | `argocd-gitops-tpl-library`; for a deploy job, `gitlab-ci-library` or `github-ci-library` | a checkout or repository/ref in the request, the release the environment's root and `extras/Chart.yaml` pin (or the repository's other branches pin), then the latest release of `https://github.com/grootan-devops/argocd-gitops-tpl-library`; a deploy job uses the CI-library release the application project already pins, and stops for a project with none |
| `terraform-module-builder` | `terraform-modules` (edited in a local checkout) | a path or repository/ref in the request, the current repository, `$TERRAFORM_MODULES_REPO`, a sibling checkout, then `https://github.com/grootan-devops/terraform-modules` |
| `terraform-module-consumer` | `terraform-modules` | the request, `$TERRAFORM_MODULES_REPO`, the stack's pinned module source, a sibling checkout, then the latest release of `https://github.com/grootan-devops/terraform-modules` |
| `observability-builder` | none | the standard is bundled in the skill |
| `k8s-upgrade` | none | no library: the stages and checks are the skill's own, and each provider's tools are read from the cluster |

### Progressive Disclosure

To prevent overwhelming the AI assistant's context window:

1. Only the skill's `name` and `description` from the YAML frontmatter are indexed initially.
2. The complete `SKILL.md` is loaded only when a prompt matches the skill's triggers.
3. Library documentation is read on demand, following only the README-index links the task needs.

## License

Copyright 2026 Grootan Technologies Pvt Ltd.

Licensed under the [GNU Affero General Public License v3.0](./LICENSE.md)
(`AGPL-3.0-only`). External contributions are not accepted; see
[CONTRIBUTING.md](./CONTRIBUTING.md) for bug and security reporting.
