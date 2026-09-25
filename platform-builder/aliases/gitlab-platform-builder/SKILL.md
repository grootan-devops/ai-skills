---
name: gitlab-platform-builder
description: >-
  GitLab CI pipelines, packaging-only Dockerfiles, and Helm charts. Scaffolds, migrates, and audits
  gitlab CI with least-privilege tokens, adaptive workflow selection, and a shared Docker/Helm
  contract. Use for onboarding a repo to gitlab CI, migrating library versions, or auditing
  pipeline, Dockerfile, and chart compliance.
---

# GitLab CI Platform Builder

Read [`../../SKILL.md`](../../SKILL.md) with the platform fixed to `gitlab`.
Skip platform detection and load only the GitLab references relevant to the task.

```bash
python3 core/scripts/audit.py [repo] --platform gitlab [--strict] [--json]
```
