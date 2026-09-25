# Platform Builder: Copyable Engineer Prompts

Replace angle-bracket placeholders before pasting. Each command has a quick form
and a full form. Follow [SKILL.md](./SKILL.md) and the selected library checkout
for exact inputs, security rules, and migration contracts.

## `platform onboard`

### Quick

```text
Run platform onboard for <repo>. Detect GitHub Actions or GitLab CI. Inspect docker-compose.yml, compose.yaml, .env, .env.*, Dockerfile, existing charts/Kubernetes manifests, .gitlab-ci.yml, .github/workflows, tests, and application manifests. Harvest ports, commands, mounts, services, config keys, probes, and resources; classify runtime settings as ConfigMap or Secret without printing secret values. Resolve the CI and Helm libraries, use only supported components, omit optional chart resources without evidence, ask about image smoke and chart unit tests, then validate against those same library checkouts. Make local changes only and report library provenance and any unpublished pin.
```

### Full

```text
Act as a Platform Engineer using the platform-builder skill. Onboard <repo>.

1. Discover: detect the CI platform and repository shape. Inspect every existing
   docker-compose.yml/compose.yaml, .env and .env.* (keys only for secrets),
   Dockerfile, legacy Helm chart/Kubernetes manifest, .gitlab-ci.yml,
   .github/workflows/*.yml, source manifest, and test fixture. Extract services,
   port and command contracts, environment keys, mount targets, probes, ingress,
   resources, replicas, and intentional CI rules. Classify non-sensitive runtime
   keys as configmapEnvs and credentials as secretEnvs; never print secret values.
2. Resolve: run core/scripts/libraries.py for the detected platform. Use any
   explicit --lib NAME=SOURCE or named source flags, then environment,
   repository pin, and defaults according to the Skill's precedence. Read each
   selected README and the relevant linked guides from the same checkout. Show
   exact sources, refs, dirty local state, and any pin not yet published.
3. Decide: show the proposed CI, image, and chart files. Ask whether to run an
   image smoke test and chart unit tests when applicable; use existing runnable
   fixtures by default. Ask about PVC, batch jobs, cronjobs, and metrics only
   when the source evidence does not settle them. Omit persistence:, jobs:,
   cronjobs:, and metrics: for a stateless app without those needs.
4. Implement: preserve existing runtime behavior. Generate only workflows/jobs
   the repository can execute. Package the image using the selected CI library
   contract. For a chart, use the selected helm-tpl-library's values, schema,
   and tpl entrypoints. Let its main image repository auto-resolve from
   partOf/component/subComponent when the selected version supports that and
   image.repository is empty. Use matching tpl.pvc/job/cronjob/servicemonitor
   entrypoints for each optional feature enabled. Never copy example pins.
5. Verify: run core/scripts/audit.py <repo> --strict with the same --lib sources.
   Run applicable native CI, Helm lint/template/schema, and test checks. Report
   the files changed, exact checks/results, unresolved choices, and publishing
   prerequisites. Do not commit, push, deploy, or release unless requested.
```

## `platform update`

### Quick

```text
Run platform update for <repo> using <target CI/Helm library sources>. Inspect existing CI, Dockerfile, chart, compose files, .env keys, and test fixtures first. Compare current and target refs, read every applicable MIGRATION.md section, and make a surgical in-place diff. Preserve custom cache keys, jobs, rules, configmapEnvs/secretEnvs, probes, replicas, resources, mounts, and intentionally absent chart features. Validate with the same library sources; show each change and any migration ambiguity. Do not commit, push, or deploy.
```

### Full

```text
Act as a Platform Engineer using the platform-builder skill. Update <repo> to
<target CI/Helm library sources or versions>.

1. Inventory the current .gitlab-ci.yml or .github/workflows, Dockerfile,
   docker-compose.yml/compose.yaml, .env and .env.* keys, chart values/schema/
   templates, Kubernetes manifests, test scripts, and repository changes.
   Determine the current library pins and intentional application contracts.
2. Resolve target libraries with core/scripts/libraries.py; record source, ref,
   and local dirty state. Read each selected README and only relevant guides.
   Read MIGRATION.md across every intervening version of each changed library.
   Distinguish shipped code/schema from standards documented as future work.
3. Plan an in-place diff: update only required library pins, breaking schema or
   include changes, and verified defects. Preserve custom PROJECT_CACHE_KEY,
   user jobs and rules, configmapEnvs/secretEnvs, replicas, resources, probes,
   mounts, annotations, and formatting. Do not regenerate from starter files.
   Keep persistence:, jobs:, cronjobs:, and metrics: absent if previously
   absent and still unneeded. If a migration conflicts with a customization,
   show the concrete diff and ask before changing that behavior.
4. Apply the minimal edit. Keep image and chart test fixtures wired where they
   already exist. If a newly required test or optional feature is unclear, ask
   the corresponding Skill question before adding a job or chart section.
5. Review the diff against the migration notes. Run core/scripts/audit.py
   <repo> --strict with the same --lib sources and applicable native checks.
   Report exact changes, checks, open migration risks, and any unpublished pin.
   Leave remote state untouched unless explicitly requested.
```

## `platform ship`

### Quick

```text
Run platform ship for <repo> and <environment>. Inspect the existing GitHub/GitLab deployment wiring, chart, and target configuration. Resolve the selected libraries, prepare only the required local wiring, validate it, and report target and permission evidence. Do not commit, push, or deploy unless explicitly requested.
```

### Full

```text
Act as a Platform Engineer using the platform-builder skill. Prepare deployment
wiring for <repo> to <environment>. Inspect the existing platform pipeline,
chart, release artifact, GitOps target, and repository-specific permissions.
Resolve CI and Helm sources and read their relevant deployment guides at the
selected refs. Confirm the environment, image/chart provenance, credential
mechanism, and target from repository evidence; ask if a required choice is
missing. Change only the local wiring needed for that target, preserving custom
jobs and chart values. Run the same-source strict audit and applicable native
validation. Report the exact diff and any activation prerequisites. Perform no
commit, push, deployment, or release without an explicit request.
```

## `platform audit`

### Quick

```text
Run platform audit for <repo> against <CI/Helm library sources>. Inspect its existing CI, Dockerfile, chart, compose and environment keys, and tests. Run the strict audit with the selected local paths or refs. Report findings with file evidence, library provenance, and separate manual security judgments. Keep the target read-only.
```

### Full

```text
Act as a Platform Auditor using the platform-builder skill. Audit <repo>.
Detect the CI platform and repository shape. Inspect .gitlab-ci.yml or
.github/workflows, Dockerfile, docker-compose.yml/compose.yaml, .env/.env.*
keys without exposing values, chart values/schema/templates, and test fixtures.
Resolve <CI/Helm library sources> with core/scripts/libraries.py and record each
source and ref. Read the relevant selected-ref guides, then run
core/scripts/audit.py <repo> --strict with those same sources. Manually inspect
secret handling, permission scope, unsupported options, and rendered chart
security where the engine cannot prove them. Report reproducible findings with
file locations, severity, command outcomes, and limits. Do not edit the target.
```
