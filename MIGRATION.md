# Migration Guide

This document records required consumer actions when upgrading between releases.
Breaking changes must include an entry before release.

To upgrade, apply every section after your installed version up to the target, oldest first.
Newer sections are split into **Required** (the upgrade breaks or misbehaves without it),
**Recommended** (aligns existing projects with the current standards) and **Verify**.

## 3.0.0

### Required

- **Resource attributes.** Remove `k8s.pod.uid`, `service.instance.id`, `process.pid`, `process.command_line`, `process.executable.*`, `vcs.*`, `host.id` and `deployment.id` from `observability.yml` and from code; the schema now rejects them. Stop dropping `telemetry.sdk.language/version`, `process.runtime.*` and `os.type`. Remove the pod UID from the chart's downward API, and update collectors to the pod association in `observability-builder/references/collector/README.md` (namespace and pod name, then IP; workload names extracted).
- **Identity.** Services that set `user.id` or `tenant.id` from client headers must take them from the authentication or tenant middleware instead. Remove `request.id`; use `correlation.id`.
- **Metrics.** Rename the GenAI `token_type` value `cache_write` to `cache_creation` in code and dashboards.
- **GenAI content size.** `genai.content.max_chars` now defaults to 8000. A yml that sets a larger value must also set the span attribute value limit to the same number, with a comment naming `max_chars`, or lower it to 8192 or less.
- **Collectors with GenAI routing:** add the `langfuse_request_context` transform from `observability-builder/references/collector/README.md` before the Langfuse batch.
- **OneUptime is no longer a supported backend.** Point each service's telemetry at a remaining
  backend (the Grafana stack, Dynatrace, New Relic or AWS) by changing only `observability.yml`
  and the collector configuration ([backends](observability-builder/references/backends/README.md)):
  - remove the OneUptime exporter, its `x-oneuptime-token` auth and the
    `deployment.environment` transform from collectors;
  - remove `x-oneuptime-token` headers and `oneuptime.label.*` resource attributes from
    `observability.yml` and from the chart's `resource.opentelemetry.io/*` annotations;
  - remove `oneuptime.database.server.id` stamping from database-engine pipelines.
- **Delete `genai.content.destination` from every `observability.yml`.** The schema rejects it
  now, and `validate.py` reports it as an error.
- **Browser RUM moves to Grafana Faro.** The old `rum:` keys (`app_identifier`, `sample_ratio`,
  `session_replay`, `exporter`) are rejected; the new `rum` block has a different shape
  ([config](observability-builder/references/config.md#rum)). In each frontend:
  - remove the OpenTelemetry Web SDK module (`src/observability/client/`, `rum.ts`), the
    OneUptime replay recorder and its `oneuptime-*` masking classes, and the OneUptime
    `/telemetry/session-replay/` proxy routes or nginx locations;
  - run `observability instrument` on the frontend: it adds the Faro pieces from
    `observability-builder/assets/js/faro`, asks for the transport and whether a same-origin
    proxy is needed, and rewrites the runtime `observability.json` (or the fullstack config
    script) to the new `rum` block;
  - hand the platform team the `faro.receiver` set-up from
    `observability-builder/references/collector/README.md` (exact CORS origins, rate limiting,
    payload cap, source maps from files with downloads off, a TLS ingress for `/collect` only
    when the transport is `faro`), and the source-map delivery per release;
  - drop the OneUptime RUM secrets from the chart; keep a Faro key Secret only for transport
    `proxy`.
- **Collectors with GenAI routing:** update the ops branch from
  `observability-builder/references/collector/README.md`. It no longer copies `user.id` and
  `team.id` onto AI spans, keeps no content keys, and has no list of `ops` services; the Langfuse
  branch goes straight to its batch processor (the `langfuse_content` transform is gone).
- **Scripts:** drop `--replay-assets` and `--genai-content-destination` from any
  `scripts/receive.py` invocation.

### Recommended

- Services that set `genai.content.destination: ops` now send no content anywhere unless
  Langfuse is on: turn Langfuse on where prompts and answers must be read.
- Dashboards or queries that read `user.id` or `team.id` from AI spans read them from the
  entry span (ops backend) or from Langfuse's trace context instead.
- Frontends with another RUM SDK (Datadog, New Relic, Elastic, Application Insights) move to
  Faro with `observability instrument`; `detect.py` lists them under existing telemetry. Import
  `observability-builder/assets/grafana/rum-dashboard.json` as the RUM dashboard.
- Run `node observability-builder/scripts/rum_overhead.mjs` for each frontend with RUM.
- Run `observability instrument` on each service to apply the classified data model: platform rows outside Kubernetes, the masking helper for personal data, the audit log, A2A spans where agents call agents, and the new conditional metrics.
- Run `scripts/overhead.py` against each instrumented service and fix anything over the budget (`observability-builder/references/overhead-and-security.md`).
- Bind `/metrics` to loopback where the scraper runs in the same pod, task or instance, and comment every plaintext exporter endpoint with its private hop.
- Rename a Python module's `logging.py` to `logs.py`.
- Generate correlation ids as URL-safe base64 (`secrets.token_urlsafe(16)`, `randomBytes(16).toString('base64url')`) instead of hex UUIDs: hex ids can hold digit runs that secret scanners read as card numbers.
- Python metrics servers: on "address in use", check for a real listener and retry with reuse when none answers, so a restart right after a scrape doesn't lose `/metrics` ([metrics](observability-builder/references/signals/metrics.md)).
- AI content: pass it to the content helper as a function, so nothing is converted while capture is off ([genai](observability-builder/references/signals/genai.md)).
- nginx in front of pages: log the request path from `$request_uri` with the query cut off, not `$uri` (which is `/index.html` after `try_files`) or `$request` ([spa](observability-builder/references/workloads/spa.md#nginx)).

### Verify

- `python3 observability-builder/scripts/audit.py <project> --module <module path>` reports no `resource-excluded-key`, `identity-from-header` or `telemetry-credential-literal` findings.
- The RUM dashboard shows data in every panel after a frontend session (Loki and Tempo data sources selected on import).
- `scripts/receive.py --platform <platform>` writes a `conformance.json` with no P0 or P1 findings for a test run of the service.
- `python3 -m unittest discover -s observability-builder/scripts/tests` passes, and the receiver
  tests pass with `uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf
  --with pyyaml python -m unittest discover -s scripts/tests -p "test_receive.py"` from
  `observability-builder/`.
- `python3 observability-builder/scripts/validate.py <project>/observability.yml` reports no
  `genai.content.destination` key and no `rum` finding.
- For frontends, `audit.py` reports no `faro-*` P0 or P1 finding, and a browser run against
  `scripts/receive.py` (Faro on its `/collect` endpoint) shows every record kind with no Faro
  data-model findings.

## 2.0.0

### Required

- **`gitops-manager` is removed; its work is part of `platform-builder`.** Uninstall it, and
  install or reinstall `platform-builder`:
  `npx skills add https://github.com/grootan-devops/ai-skills --skill platform-builder`.
  - **Commands:** `argocd env add|update|audit|clone` became `platform env …`, and
    `argocd app add|promote` became `platform app …`. There is no separate deploy-job command:
    those commands wire the CI deploy job and ask only whether it runs automatically or
    manually.
  - **Scripts**, now under `platform-builder/`:
    - `scripts/app_add.py` → `gitops/argocd/scripts/app_add.py`
    - `scripts/override_check.py` → `gitops/argocd/scripts/override_check.py`
    - `scripts/library.py` → `core/scripts/gitops_library.py` (the Argo CD part is in
      `gitops/argocd/scripts/argocd.py`)
  - **References:**
    - `references/argocd-environment.md` → `gitops/argocd/references/environment.md`
    - `references/service-bootstrap.md` → `gitops/argocd/references/service-bootstrap.md`

  Update any prompt, alias or automation that names the old skill, commands or paths.
- **`platform ship` is removed.** Use `platform app add` to put an app into an existing
  environment, or `platform env add` for a new one. Both wire the app's CI deploy job and ask
  only whether it runs automatically or manually.
- **New environments are named differently.** `platform env add` takes the cluster label from
  the GitOps repository's README, else from the repository's name. The label prefixes the
  chart name, and `cluster` is left unset. The default branch is `<product>/<env>`. To keep
  the previous naming for a new environment, name the chart, branch and `cluster` value in
  the request. Existing environments are read as they are and never renamed.

### Recommended

- Each skill now checks for a newer release when it runs. A git checkout knows its release from its tag or the repository's root `VERSION`. A copied install doesn't record one, so it names the latest release and offers `npx skills add … --skill <name>`; reinstall when it does. Maintainers bump only the root `VERSION`.
- `PROMPT.md` is gone from every skill. Type the command instead (each skill's README lists
  them, with what each asks for), or name the skill if your tool didn't load it. Replace any
  saved prompts or automation that pasted from PROMPT.md.
- New apps and environments now wait for a developer's review. `platform app add` opens an
  MR/PR into the environment branch, and a new environment's root Application is created only
  after its branch is approved in chat. Give the dev who reviews push access to the GitOps
  repository, and the person running the skill `glab` or `gh` access to open and merge MRs/PRs.
- Install `k8s-upgrade` where clusters are upgraded:
  `npx skills add https://github.com/grootan-devops/ai-skills --skill k8s-upgrade`.

### Verify

- `python3 -m unittest discover -s platform-builder/core/scripts/tests` and
  `-s platform-builder/gitops/argocd/scripts/tests` pass, and so do the `k8s-upgrade`,
  `terraform-module-builder` and `observability-builder` suites.
- `python3 platform-builder/core/scripts/naming.py --repo-url <gitops-url> --product <p> --env <e>`
  prints the label and its source for a repository you use.

## 1.8.0

### Required

- **Libraries are read at a release, not at `main`.** A skill reads each library at the
  release the project pins, or at the latest release tag for a new project or an upgrade
  (`platform update`, `audit.py --latest`). To read another ref, name it: a `--<library>`
  source, `$PLATFORM_BUILDER_LIB_<NAME>` or `.platform-builder.json`.
- **The libraries' data files are required.** The checks read their rules from the data file
  a library release ships. A project pinned to an older release stops with the release to
  upgrade to: run `platform update` first, or stay on ai-skills `1.7.0` until then.
- **`COMPATIBILITY.md` no longer lists library releases.** The `1.4.0` rule that a library pin
  be at least the listed release is gone: pin any release, and the skill reads that release.

### Recommended

- Remove `ref: main` from a `.platform-builder.json` that only pinned it to follow the
  libraries: the project's own pins now decide.
- `gitops-manager` needs helm to add a service: it renders the pinned library to name the new
  Application.

### Verify

- `python3 -m unittest discover -s platform-builder/core/scripts/tests` passes, and so do the
  `gitops-manager`, `terraform-module-builder` and `observability-builder` suites.
- `audit.py` prints each library with `via project pin` or the latest release, and a project
  on a release without the data file stops with exit code 2 and the release to upgrade to.

## 1.7.0

No migration is required. This release only updates dependencies.

## 1.6.0

### Required

No migration required. `genai.content.destination` defaults to `langfuse`, the 1.5.0 behaviour,
so existing `observability.yml` files validate and behave unchanged.

### Recommended

- Re-run `observability instrument` on services that call a model, to add the user, team and
  conversation to their AI spans (OneUptime's Usage shows their spend as Unattributed without
  them), the retrieval operation, the new allow-list keys, and the removal of AI keys from spans
  that aren't AI calls.
- Where prompts and answers should be read in OneUptime instead of Langfuse, set
  `genai.content.destination: ops`, with Langfuse mode `hybrid` or `otel`, or Langfuse off.
- Instrument MCP servers and clients with `references/workloads/mcp.md`, including the server
  and client MCP metrics.
- Collectors with GenAI routing: update the ops branch from `references/collector/README.md`
  (identity on AI spans, AI keys off other spans, the new allow-list) and list the services whose
  content destination is `ops` in both branches (`^$` while there are none).
- Services exporting straight to OneUptime: add `deployment.environment: "${OBS_ENVIRONMENT}"`
  under `resource.attributes`, arrange a scraper for `/metrics`, and add OneUptime prices for
  self-hosted or aliased models.

### Verify

- `python3 -m unittest discover -s observability-builder/scripts/tests` passes, and the receiver
  tests pass with `uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf
  --with pyyaml python -m unittest discover -s scripts/tests -p "test_receive.py"` from
  `observability-builder/`.
- A re-instrumented AI service's receiver summary shows the user, team and conversation on its AI
  calls and no `AI KEYS ON OTHER SPANS` alarm.

## 1.5.0

### Required

No migration required. This release adds the `observability-builder` skill; no existing skill
changed, and the reusable workflow pin moves to `github-ci-library` `1.5.0`, whose inputs,
outputs and behaviour are unchanged.

### Recommended

- Install the new skill where services need observability work:

  ```bash
  npx skills add https://github.com/grootan-devops/ai-skills \
    --skill observability-builder
  ```

- Its scripts need Python 3.10 or newer with PyYAML. The local receiver also needs grpcio,
  protobuf and opentelemetry-proto, which `uv run --no-project --with …` supplies, as the
  skill's README shows.
- `observability-builder` bundles its standard in its own `references/` and `assets/`; it reads
  no library and needs no entry in `.platform-builder.json`.

### Verify

- `python3 -m unittest discover -s observability-builder/scripts/tests` passes (the receiver
  tests skip themselves without grpcio).
- The pull request pipeline passes with the `1.5.0` library pin.

## 1.4.0

### Required

- Reinstall or re-link the skills from this release. `platform-builder` no longer ships
  `core/references/`, `platforms/*/references/` or `assets/`, `gitops-manager` no longer ships
  `assets/bootstrap/`, and `terraform-module-builder` no longer ships `references/` or
  `assets/`. Point anything that read those files, such as editor rules, project instructions
  or uploaded Project Knowledge, at the library documentation instead.
- The standards now come only from the libraries, read at the resolved ref. A library pinned in
  `.platform-builder.json` or `$PLATFORM_BUILDER_LIB_*` must be at least the release listed in
  [COMPATIBILITY.md](./COMPATIBILITY.md); an older pin lacks the guidance the skills follow.
  The same applies to the terraform-modules checkout the Terraform skills read.

### Recommended

- Run `platform update` on onboarded repositories. The audit now reports a PID 1 shape other
  than `ENTRYPOINT ["/usr/bin/dumb-init", "--"]` with the process in `CMD`, chart
  `command`/`args` overrides and start scripts that do not `exec` as P1 findings.
- `argocd app add` adds a service to an existing environment, and `terraform-module-consumer`
  composes infrastructure from released modules.

### Verify

- `python3 platform-builder/core/scripts/libraries.py <repo> --all` lists each library's
  README index and `MIGRATION.md` at the expected ref.

## 1.3.0

Terraform module interfaces require no consumer migration. Prompt scaffolds now feature Dual-Format options (Quick for streamlined pasting and Full for complete governance). Reusable workflows are pinned to `@1.3.1`. Terraform module work still stops after local validation by default; an explicitly requested apply now requires confirmation of the exact saved plan and target after review.

For a new module, write and review its README narrative and public release-tag examples before running `generate-module-docs.py`. The tool now updates only the generated tables in an existing README; it no longer creates a file with unverified security claims or a relative module source.

## 1.2.0

### Skill Removals

- The broad `gitops-app-manager` skill has been removed. Switch to `gitops-manager` for Argo CD environment bootstrap, standard consumer chart scaffolding, and root Application creation.

### Documentation & Templates

The documentation restructuring requires no consumer configuration changes. Start at the README index and follow its
task-specific documentation links; update any bookmarks to moved sections. Library source/ref precedence is unchanged.

Updated chart generation follows the resolved library's registry contract. GitLab users
upgrading the CI library must review its chart-registry migration: a nonempty `CHART_REGISTRY`
now selects OCI, while an unset/empty value selects the current project's Helm Package Registry.

## 1.1.0

No migration is required. The platform-builder Helm ignore baseline now excludes
repository metadata files that must not be packaged into charts.

## 1.0.0

No migration is required for the initial release.
