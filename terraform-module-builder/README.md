# Terraform Module Builder

This Skill creates, updates, and audits Terraform modules against the
checked-out terraform-modules repository. [SKILL.md](./SKILL.md) is the
execution policy, and AGENTS.md is a discovery pointer. Its own rules cover
resource attributes from the provider schema; general Terraform craft comes
from the companion skills below.

## Workflows

| Command | Result | Asks for, when missing |
| --- | --- | --- |
| `terraform-module add` | Create a module with an inspected provider schema, documentation, and useful tests. | provider, resource type and module name |
| `terraform-module update` | Target the latest stable provider and make a reviewed, surgical module change. | the module and the change |
| `terraform-module audit` | Report findings without editing. | the module, or the whole repository |

Commands work on the terraform-modules checkout in the current directory when it is one (a
`modules/` folder and `MIGRATION.md` at its root), and otherwise ask for its path.

The reference repository's agent files (`CLAUDE.md`, `AGENTS.md`) and README documentation
index link its module API, naming, security, documentation, release and test standards; its
MIGRATION.md records release-specific consumer actions. Read them at the selected ref, then
inspect actual modules and tests before treating examples as shipped behavior. The current
reference repository is AWS-focused; other providers require their own schema and contract
evidence.

## Companion skills

| Skill | Source | Covers |
| --- | --- | --- |
| `terraform-skill` | [antonbabenko/terraform-skill](https://github.com/antonbabenko/terraform-skill) | Module design, review, security and testing strategy |
| `terraform-style-guide` | [hashicorp/agent-skills](https://github.com/hashicorp/agent-skills) | HCL style and layout |
| `terraform-test` | [hashicorp/agent-skills](https://github.com/hashicorp/agent-skills) | Native `terraform test` syntax, assertions and mocks |

Claude Code, per project:

```bash
claude plugin marketplace add antonbabenko/agent-plugins
claude plugin install terraform-skill@antonbabenko --scope project
claude plugin marketplace add hashicorp/agent-skills
claude plugin install terraform@hashicorp --scope project
```

Other agents:

```bash
npx skills add https://github.com/antonbabenko/terraform-skill
npx skills add https://github.com/hashicorp/agent-skills --skill terraform-style-guide --skill terraform-test
```

The repository's docs outrank both companions and this Skill.

## Install by following a clone

To pick up edits and pulls at once, link the skill from an ai-skills clone into the project
instead of copying it:

```bash
mkdir -p .claude/skills
ln -s ~/.ai-skills/terraform-module-builder .claude/skills/terraform-module-builder
```

`scripts/update_check.py` follows the link to the clone and checks it for updates. Keep
`.claude/` out of the module library's commits.

## Local tools

- scripts/check-module-rules.py checks a textual subset of module rules. A variable read
  only in another variable's validation counts as used; outputs named `*_arn`, `*_id` or
  `*_name` are identifiers, not secrets.
- scripts/detect-migrations.py compares variables, outputs, and top-level
  resource declarations with a Git ref and reports candidate migrations.
  A module that does not exist at the ref is reported as new (a MINOR release). It cannot
  prove plan safety or detect every Terraform instance-key change.
- scripts/generate-module-docs.py renders or checks Requirements, Inputs, and
  Outputs tables in an existing reviewed README. A section ends at the next heading, a
  `---` rule or the end of the file; tables use `| --- |` delimiters and providers follow
  `versions.tf`. It refuses to invent a new module's architecture, security claims, or
  public Git example.

Use make verify from terraform-modules for its contract, format, validation,
and available native-test gate. Inspect a representative consumer plan before
claiming that a refactor avoids replacement or destruction. A moved block only
covers a verified resource-address mapping. An apply requires a separate request
and confirmation of the exact saved plan and target; local module work does not
authorize it. No workflow commits or pushes work without an explicit request.

For Skill maintainers, the checker, generator and migration detector regressions live
in `scripts/tests/`. From the `ai-skills` repository root, run
`python3 -m unittest discover -s terraform-module-builder/scripts/tests -v`.
The separate `terraform-modules/tests/verify_modules.py` remains that library's
native `make verify` entry point so its contract gate works in a standalone clone.

## Standards

The module contract, provider-schema mapping, naming bounds, security capability statuses,
versioning and test shapes live in terraform-modules, indexed from its README.
[SKILL.md](./SKILL.md) says which to read for each task.
