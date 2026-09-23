# Documentation Contract

A change is not complete until the README index, affected topic guides, changelog and any
required migration notes agree. Consumers copy the linked examples into their pipelines.

---

## 1. README index and linked documentation

Keep the root README short: purpose, minimal start instructions, and a task-to-document map.
Mirror topic organization across the two CI libraries so readers can move between platforms.
Keep each contract in one place under `docs/`; one module or complete integration scenario
per page prevents a new monolithic manual. Use relative links and a path back to the index.

The following topics must be reachable from the README, not embedded in it:

| Section | Must contain |
| --- | --- |
| Quick Start | Complete copy-pasteable CI setup; GitHub includes PR and release workflows |
| Pipeline Phases & Lifecycle | The phase table, mapping each GitLab stage to the workflow · job that now owns it |
| Execution Model & Trigger Strategy | The two-tier release model, the scenario-workflow table, an execution matrix |
| Workflow DAGs | Mermaid graphs for PR verification, release promotion, deployment, standalone audits |
| Module Catalog | An index describing each module's purpose and responsibilities, linking to its detailed job graph and job table |
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
python3 scripts/verify-gitlab-library.py <gitlab_library_dir>
python3 scripts/verify-github-library.py <github_library_dir>
python3 scripts/verify-docs.py <library_dir>
```

Validate links and anchors relative to each containing page, including nested paths and
duplicate-heading suffixes. Check YAML fences across README and `docs/**/*.md`; use
`gotmpl` for unrendered Helm templates rather than pretending they are valid YAML.
Referenced library workflow files must exist. Preserve Dockerfile example lint coverage
when examples move. A legacy monolithic README remains valid; a short index does not waive
topic coverage. Static validation may inspect every page; agents doing scoped work should not.

For generated Helm documentation, move both the rendered output and the generator's source
template. Regeneration must preserve the short root README and the separate values reference.

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
