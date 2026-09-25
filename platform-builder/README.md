# Platform Builder

One Skill handles GitHub Actions, GitLab CI, packaging Dockerfiles, and Helm
consumer charts. [SKILL.md](./SKILL.md) defines the agent workflow;
[PROMPT.md](./PROMPT.md) gives copyable requests. The selected CI and Helm
libraries define their own executable contracts.

## Commands

| Agent request | Purpose |
| --- | --- |
| platform onboard [repo] | Add only applicable CI, image, and chart wiring. |
| platform update [repo] | Apply a surgical library migration. |
| platform ship [repo] <env> | Prepare local deployment wiring. |
| platform audit [repo] | Read-only compliance and security review. |

The local inspection tools are:

    python3 core/scripts/platform.py /path/to/repo
    python3 core/scripts/libraries.py /path/to/repo --platform github
    python3 core/scripts/audit.py /path/to/repo --strict

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
```

## Ownership

- core/scripts/audit.py combines common Docker/Helm checks with the detected
  CI platform adapter. It reports the resolved library provenance.
- core/libraries.json defines default sources; core/scripts/libraries.py
  implements source precedence and selected-ref resolution.
- platforms/github and platforms/gitlab own their CI checks, workflow-map.json,
  and platform-specific references. Do not mix their syntax or cache mechanics.
- core/references holds shared Helm, Docker, security, migration, file-mount,
  and ignore-file guidance. Read only the references relevant to the task.
- aliases/ contains thin platform-specific entry points; AGENTS.md is an
  agent-discovery pointer.

For the actual CI workflow inputs, template behavior, Helm values, and
migration steps, read the selected library's README index and relevant linked
pages at the same ref. An audit labels reproducible checker findings separately
from manual judgments. Local work does not authorize deployment, commit, or push.
