# Documentation Contract

A port is not complete until these three files are updated. Documentation drift in a CI
library is worse than in most code: consumers copy the README verbatim into their pipelines.

---

## 1. README.md

Mirror the source library's structure so a reader can move between platforms. Required
sections:

| Section | Must contain |
|---|---|
| Quick Start | Two complete, copy-pasteable workflow files (PR + release) |
| Pipeline Phases & Lifecycle | The phase table, mapping each GitLab stage to the workflow · job that now owns it |
| Execution Model & Trigger Strategy | The two-tier release model, the scenario-workflow table, an execution matrix |
| Workflow DAGs | Mermaid graphs for PR verification, release promotion, deployment, standalone audits |
| Module Catalog | One subsection per module, with a mermaid job graph and a job table |
| Dockerfile Standards | Packaging-only rule, non-root `10001:10001`, the build-arg base image table, one example per stack |
| Inverted `.dockerignore` | The default-deny rule plus a per-stack allowlist table |
| Key Variables & Configuration | Required variables, build/base images, behavioural variables, secrets |
| Ignored CVEs & Licenses | The `ignored-cves.yml` schema and the reason-validation rules |
| Scan Exit Codes | The 0/1/2 table, and how exit 2 is handled without `allow_failure` |
| DevOps Reference | Organisation-injected config, build-container tool requirements, registry auth |
| Integration Examples | One per project shape — every permutation the library supports |
| Migration Guide | A pointer to MIGRATION.md and the changelog/migration standard for consumers |

**Verify mechanically** before reporting done:

```bash
python3 scripts/verify-port.py <library_dir>   # includes anchor + YAML-fence checks
```

Every TOC anchor must resolve, every ```yaml fence must parse, and every workflow file
referenced must exist. GitHub's heading slug lowercases, strips punctuation, and replaces
each space with a hyphen **without collapsing** — so `A & B` becomes `a--b`.

## 2. CHANGELOG.md

Keep a Changelog format. A port entry states the behaviour change, not the file list:

```markdown
## [1.0.0]

Breaking. <One paragraph: what changed structurally and why.>

Input count across the library: **275 -> 161**.

### Added
- New workflows, with the GitLab job each one ports.

### Changed
- Structural changes: containerisation, job splitting, configuration source.

### Removed
- Removed workflows and inputs, each with its replacement.

### Fixed
- Real defects found during the port.
```

Report the **input-count delta** — it is the clearest single measure of whether
configuration moved to the right place.

Do not leave stale claims in an inherited `[Unreleased]` section. If a port changed the
behaviour an unreleased note describes, correct the note rather than shipping it.

## 3. MIGRATION.md

A section per breaking version transition, headed `## [previous...current] - YYYY-MM-DD`,
covering in order:

1. **Configuration to set first** — the organisation variables and secrets table. This is
   the section that makes the difference between a working first run and a confusing one.
2. **Renamed secrets/variables** — old name → new name.
3. **Removed inputs** — each with where the value comes from now.
4. **Merged or removed workflows** — a table of old → new.
5. **New workflows** — and the GitLab job each one closes the gap on.
6. **Split jobs** — and the consequence for branch protection required-status-check names.
7. **Behaviour changes** — anything that now passes where it used to fail, or vice versa.
8. **Worked examples** for the non-obvious rewires.

## 4. The consumer-facing standard

The library enforces on its consumers what it follows itself:

- `CHANGELOG.md` with a `## [x.y.z]` section per release — `check.yml` extracts it as the
  release notes and fails without it.
- `MIGRATION.md` with a section covering the upgrade path; "No migration required" is a
  valid body, an empty section is not.
- The version lives in the project manifest or `Chart.yaml`; `init.yml` discovers it and
  nothing is hand-stamped.
