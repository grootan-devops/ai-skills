# AI Agent Skills

Release `1.2.0` · [Compatibility](./COMPATIBILITY.md) · [Security](./SECURITY.md) · [Contributing](./CONTRIBUTING.md)

This directory contains reusable, production-hardened **AI Agent Skills**. Skills are modular packages of procedures, automated validation scripts, reference baselines, and architectural contracts that transform AI coding assistants into specialized platform engineers.

---

## 1. Available Skills Catalog

| Skill Name | Supported Platforms | Description |
| --- | --- | --- |
| [`terraform-module-builder`](./terraform-module-builder/) | AWS reference modules; other providers require schema evidence | Terraform module creation, updates, and audits with provider schema checks, migration review, and repository-native validation. |
| [`platform-builder`](./platform-builder/) | GitLab CI/CD, GitHub Actions, Docker, Helm, Kubernetes | Unified CI/CD, container and Helm platform engineering for both platforms: one engine with per-platform adapters. Strict 3-job separation, packaging-only Dockerfiles, Nginx frontend standard, least-privilege `GITHUB_TOKEN` permissions, SHA-pinned actions, breaking-change migrations, and minimal YAML footprints. Supersedes the former `gitlab-platform-builder` and `github-platform-builder`, which remain available as aliases inside it. |
| [`gitops-manager`](./gitops-manager/) | Argo CD, Helm, Git | Focused Argo CD environment bootstrap: authenticated preflight, cluster metadata discovery, standard root/extras charts, validation, and root Application creation. |

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
`platform-builder`, `ci-library-builder` or `gitops-manager`. Repeat the command to
install more than one.

Each skill is self-contained: the CLI copies that directory and nothing else, so a skill
never reaches outside its own folder for a reference.

---

## 3. Manual Integration Instructions by AI Tool

If you prefer to configure your environment manually, clone the repository first — every
instruction below points at that clone, and none of them copies a skill into your project.

```bash
git clone https://github.com/grootan-devops/ai-skills.git ~/.ai-skills
git -C ~/.ai-skills checkout 1.1.0
```

Pin a tag rather than tracking a branch: a branch moves, and a skill that changes underneath
a project changes what the agent does to it without anything in the project's history saying
so.

Where the project should carry its own skill version, add the repository as a submodule
instead. The revision is then recorded in the project's history, and a fresh checkout gets
the same skill:

```bash
git submodule add https://github.com/grootan-devops/ai-skills.git .ai-skills
git -C .ai-skills checkout 1.1.0
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
  ln -sfn ~/.ai-skills/terraform-module-builder .claude/skills/terraform-module-builder
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
  ln -sfn ~/.ai-skills/terraform-module-builder .kiro/skills/terraform-module-builder
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
