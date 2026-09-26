---
name: github-platform-builder
description: >-
  GitHub Actions pipelines, packaging-only Dockerfiles, and Helm charts. Scaffolds, migrates, and audits
  github CI with least-privilege tokens, adaptive workflow selection, and a shared Docker/Helm
  contract. Use for onboarding a repo to github CI, migrating library versions, or auditing
  pipeline, Dockerfile, and chart compliance.
---

# GitHub Actions Platform Builder

Read [`../../SKILL.md`](../../SKILL.md) with the platform fixed to `github`.
Skip platform detection and load only the GitHub references relevant to the task.

```bash
python3 core/scripts/audit.py [repo] --platform github [--strict] [--json]
```
