# AI Agent Skills

Release `1.0.0` · [Compatibility](./COMPATIBILITY.md) · [Security](./SECURITY.md) · [Contributing](./CONTRIBUTING.md)

This directory contains reusable, production-hardened **AI Agent Skills**. Skills are modular packages of procedures, automated validation scripts, reference baselines, and architectural contracts that transform AI coding assistants into specialized platform engineers.

---

## 1. Available Skills Catalog

| Skill Name | Supported Platforms | Description |
|---|---|---|
| [`terraform-module-builder`](./terraform-module-builder/) | AWS, Azure, GCP, Kubernetes | Industrial-grade Terraform module engineering platform: provider schema introspection, capability-aware security, 7-level testing pyramid, zero-destroy state migrations, and canonical documentation generation. |
| [`platform-builder`](./platform-builder/) | GitLab CI/CD, GitHub Actions, Docker, Helm, Kubernetes | Unified CI/CD, container and Helm platform engineering for both platforms: one engine with per-platform adapters. Strict 3-job separation, packaging-only Dockerfiles, Nginx frontend standard, least-privilege `GITHUB_TOKEN` permissions, SHA-pinned actions, breaking-change migrations, and minimal YAML footprints. Supersedes the former `gitlab-platform-builder` and `github-platform-builder`, which remain available as aliases inside it. |
| [`ci-library-builder`](./ci-library-builder/) | GitLab CI templates, GitHub Actions reusable workflows | Develops the shared CI **libraries** themselves: adds, extends, ports and audits jobs across a GitLab CI template library and its GitHub Actions counterpart, keeping the two in step. Decides stage, hidden vs concrete job, image, script, `needs:` and fail-fast wiring, artifacts, cache, rules and target file — or that the job should not exist. Ships a structural verifier per platform. Absorbs the former `github-port`. |
| [`gitops-app-manager`](./gitops-app-manager/) | ArgoCD (Helm & Extras manifests), Komodo (Docker Compose), GitLab CI/CD | Industrial-grade GitOps application onboarding and environment management platform: automated root app-of-apps bootstrapping, values scaffolding (128Mi memory, dev image suffix), safety review gates, and GitLab CI delivery injection. |

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

Swap `--skill` for any skill in the catalog above — `terraform-module-builder`,
`platform-builder`, `ci-library-builder` or `gitops-app-manager`. Repeat the command to
install more than one.

Each skill is self-contained: the CLI copies that directory and nothing else, so a skill
never reaches outside its own folder for a reference.

---

## 3. Manual Integration Instructions by AI Tool

If you prefer to configure your environment manually, follow the instructions for your specific AI assistant below:

### 3.1. Google Antigravity (AGY)

Antigravity automatically discovers skills placed in `.agents/skills/` or declared in `.agents/skills.json`.

- **Project-Level (Workspace)**:

  ```bash
  mkdir -p .agents/skills
  ln -sfn ../../skills/terraform-module-builder .agents/skills/terraform-module-builder
  ```

  Or register the path in `.agents/skills.json`:

  ```json
  {
    "entries": [
      { "path": "skills" }
    ]
  }
  ```

- **Global (All Projects on Machine)**:

  ```bash
  mkdir -p ~/.gemini/config/skills
  ln -sfn /path/to/library/skills/terraform-module-builder ~/.gemini/config/skills/terraform-module-builder
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
  ln -sfn ../../skills/terraform-module-builder .cursor/skills/terraform-module-builder

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
  - Apply standards in `.cursor/skills/terraform-module-builder/references/`.
  - Execute audit scripts from `.cursor/skills/terraform-module-builder/scripts/`.
  ```

- **Activation**: Cursor Agent auto-activates this rule whenever you open or edit Terraform files, or when you tag `@terraform-module-builder` in chat.

---

### 3.3. Anthropic Claude (Claude Code & Claude Projects)

Claude Code supports project-level skills and `CLAUDE.md` instruction files.

- **Claude Code Installation**:

  ```bash
  mkdir -p .claude/skills
  ln -sfn ../../skills/terraform-module-builder .claude/skills/terraform-module-builder
  ```

  Add the skill reference to your project's `CLAUDE.md`:

  ```markdown
  ## Skills
  - **terraform-module-builder**: Located at `.claude/skills/terraform-module-builder/SKILL.md`. Consult this runbook whenever designing, auditing, testing, or documenting Terraform modules.
  ```

- **Claude Projects (Web)**:
  Upload `SKILL.md` and the documents in `references/` directly into your Claude Project's **Project Knowledge**.

---

### 3.4. Kiro / Kirao IDE

Kiro recognizes skills in `.kiro/skills/` or standard `.agent/skills/`.

- **Installation**:

  ```bash
  mkdir -p .kiro/skills
  ln -sfn ../../skills/terraform-module-builder .kiro/skills/terraform-module-builder
  ```

- **Activation**: Kiro reads `.kiro/skills/terraform-module-builder/SKILL.md` when executing infrastructure automation tasks or when prompting with `terraform-module audit` or `terraform-module add`.

---

### 3.5. OpenAI / Codex / ChatGPT

- **Custom GPTs**:
  Create a Custom GPT (e.g. "Terraform Module Platform Architect"), paste the contents of `SKILL.md` into the **Instructions**, and upload the files in `references/` into **Knowledge**.
- **Codex / API Agents**:
  Symlink or place the skill into `.codex/skills/` or pass `SKILL.md` as a system prompt directive.

---

### 3.6. Universal Multi-Agent Frameworks (Windsurf, Roo, Continue, GitHub Copilot)

Most modern agentic tools natively parse `.agent/skills/` or workspace instruction files:

```bash
mkdir -p .agent/skills
ln -sfn ../../skills/terraform-module-builder .agent/skills/terraform-module-builder
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
├── SKILL.md          # Required: YAML frontmatter + core orchestrator instructions
├── scripts/          # Optional: Automated CLI linters, generators, and validators
├── references/       # Optional: In-depth technical specifications and knowledge baselines
└── assets/           # Optional: Visual architectural diagrams and templates
```

### Progressive Disclosure

To prevent overwhelming the AI assistant's context window:

1. Only the skill's `name` and `description` from the YAML frontmatter are indexed initially.
2. The complete `SKILL.md` is loaded only when a prompt matches the skill's triggers.
3. Bulky reference guides in `references/` are read on-demand when specific deep-dive procedures are required.

## License

Copyright 2026 Grootan Technologies Pvt Ltd.

Licensed under the [GNU Affero General Public License v3.0](./LICENSE.md)
(`AGPL-3.0-only`). External contributions are not accepted; see
[CONTRIBUTING.md](./CONTRIBUTING.md) for bug and security reporting.
