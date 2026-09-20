---
name: github-platform-builder
description: >-
  GitHub Actions pipelines, packaging-only Dockerfiles, and Helm charts. Scaffolds, migrates, and audits
  github CI with least-privilege tokens, adaptive workflow selection, and a shared Docker/Helm
  contract. Use for onboarding a repo to github CI, migrating library versions, or auditing
  pipeline, Dockerfile, and chart compliance.
---

# GitHub Actions Platform Builder

> **Thin alias.** This is a discovery entry point, not an implementation. Two narrowly-named
> skills trigger more reliably than one broad one, but maintaining two implementations is what
> let them drift apart in the first place — so this file carries **no rules of its own**.
>
> **Read [`../../SKILL.md`](../../SKILL.md) and follow it, with the platform fixed to `github`.**
> Skip its platform-detection step: you already know the answer. Load
> `platforms/github/references/*` and never the other platform's.

Everything else — the reference index, the single-source law, the minimal-change law, commands,
standards, and the audit protocol — lives in that one file.

```bash
# from the platform-builder root
python3 core/scripts/audit.py [repo] --platform github [--strict] [--json]
```
