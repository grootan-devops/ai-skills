# Platform Builder

One Skill handles GitHub Actions, GitLab CI, packaging Dockerfiles, and Helm
consumer charts, and the GitOps environments its projects deploy to.
[SKILL.md](./SKILL.md) defines the agent workflow. The selected CI and Helm
libraries define their own executable contracts.

## Commands

| Command | Purpose | Asks for, when missing |
| --- | --- | --- |
| `platform onboard` | Add only the CI, image and chart wiring the repository needs. | image registry, pull secret and repository path; partOf, component and subComponent to confirm; image smoke and chart unit tests |
| `platform update` | Surgical migration to the latest library releases, or the ones you name. | how to resolve a migration step that conflicts with a customization |
| `platform audit` | Read-only compliance and security review. | — |
| `platform env add` | Bootstrap a GitOps environment with its first apps, and wire each app's CI deploy job. | GitOps repository link, product, environment, Argo CD URL, the apps; automatic or manual per deploy job |
| `platform env update` | Move an environment to a newer GitOps library release. | which environment branch, Argo CD URL |
| `platform env audit` | Read-only review of an environment. | which environment branch; Argo CD URL for live health |
| `platform env clone` | Create an environment of a product from one of its branches. | source branch, target environment, Argo CD URL; automatic or manual per deploy job |
| `platform app add` | Add an app to an environment and wire its CI deploy job. | what to deploy (application repository, chart or docs), which environment; automatic or manual |
| `platform app promote` | Move a sandbox stack into its own environments, with its data. | sandbox project, product, target environments; automatic or manual per deploy job |

Nothing new goes live before a developer has reviewed it: `env add`, `env clone` and
`app promote` hand over the pushed environment branch, and `app add` opens an MR/PR. The root
Application is created, or the MR/PR merged and synced, only after the dev approves in chat
([review gate](gitops/argocd/references/review-gate.md)).

Type a command in plain words; there is nothing to fill in. A command works on the repository
in the current directory when it is the right kind (an application repository, or a GitOps
clone for the `env` and `app` commands), and otherwise asks for its path or link. Everything
else is read from the repositories, or asked for when it is missing.

The local inspection tools are:

```bash
python3 core/scripts/platform.py /path/to/repo
python3 core/scripts/libraries.py /path/to/repo --platform github
python3 core/scripts/audit.py /path/to/repo --strict
```

Chart helpers:

```bash
core/scripts/chart-lint.sh path/to/chart        # lint + render values.yaml and each overlay
core/scripts/chart-docs.sh path/to/chart         # regenerate README.md (--check reports drift)
python3 core/scripts/names.py path/to/chart -f path/to/chart/values.worker.yaml
python3 core/scripts/verify_siblings.py chart-a chart-b ...
```

GitOps helpers:

```bash
python3 core/scripts/app_facts.py path/to/app-repo     # chart, image and CI pin of an onboarded project
python3 gitops/argocd/scripts/repo_check.py <gitops-url>  # an environment repository that follows its pattern
python3 gitops/argocd/scripts/review_links.py <gitops-url> --branch <b> --changed <base> --repo <clone>  # links the dev reviews
python3 gitops/argocd/scripts/app_report.py <env checkout> --gitops-url <url> --branch <b> --argocd-url <url> --app <key>  # what the dev keeps once it's live
python3 core/scripts/naming.py --repo-url <gitops-url> --product <p> --env <e>
python3 core/scripts/ci_pin.py path/to/app-repo --backend argocd
python3 gitops/argocd/scripts/app_add.py <gitops-root> --app <key> --app-facts <facts.json> --dry-run
python3 gitops/argocd/scripts/override_check.py <values file> <service chart>
python3 gitops/argocd/scripts/yaml_style.py <sibling values files>  # the layout new files follow
```

Standards and starter templates (the chart `README.gotmpl`, the `MODE` dispatcher, a SPA
server config, a project base image and its builder) live in the selected libraries' docs.

The resolver and audit accept --github-ci-library, --gitlab-ci-library,
--helm-tpl-library, or repeatable --lib NAME=SOURCE overrides. Use local
checkout paths when validating uncommitted library changes. The resolver
reports the selected ref and warns when that checkout is dirty. Generated
consumer pins must still pass the target library's publication rules; a local
feature branch is not automatically a releasable GitHub workflow pin.

Python 3.9+ and PyYAML are required for the audit. Run each script with
--help for its current options and exit behavior.

## Maintainer regression checks

The Skill owns checker unit tests for the platform audit and rule engine.
From the `ai-skills` repository root, run the test suite:

```bash
python3 -m unittest discover -s platform-builder/core/scripts/tests -v
python3 -m unittest discover -s platform-builder/gitops/argocd/scripts/tests -v
```

## Ownership

- core/scripts/audit.py combines common Docker/Helm checks with the detected
  CI platform adapter. It reports the resolved library provenance.
- core/libraries.json defines default sources; core/scripts/libraries.py
  implements source precedence and selected-ref resolution.
- platforms/github and platforms/gitlab own their CI checks; the workflow maps they read ship with the CI libraries.
  Do not mix their syntax or cache mechanics.
- gitops/argocd owns the Argo CD environment and app workflows, references and scripts;
  the rules every GitOps backend shares are in core/references and core/scripts.
- aliases/ contains thin platform-specific entry points; AGENTS.md is an
  agent-discovery pointer.

For the actual CI workflow inputs, template behavior, Helm values, and
migration steps, read the selected library's README index and relevant linked
pages at the same ref. An audit labels reproducible checker findings separately
from manual judgments. Local work does not authorize deployment, commit, or push.
