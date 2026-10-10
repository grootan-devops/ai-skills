# Prompts

Ready-to-use prompts for the observability-builder skill, one per task. In Claude Code with the
skill installed, start with `/observability-builder` or name the skill; elsewhere, start with
"Read <path>/observability-builder/SKILL.md and follow it". Copy a prompt, replace the
`<placeholders>`, and delete the lines that don't apply. Every answer you give up front is a
question the skill doesn't have to ask ([instrument step 2](references/instrument.md#2-ask));
leave a line out and the skill proposes an answer from the code and asks.

The prompts work for any language, framework and backend. The skill changes application code,
`observability.yml` and the dependency manifest only; charts, Dockerfiles and CI come back as a
handoff list. It never commits, pushes or deploys unless you say so. Keep secrets out of the
conversation: point to mounted files or environment variables, never paste keys.

- [Add end-to-end monitoring](#add-end-to-end-monitoring)
- [Find gaps in an instrumented project](#find-gaps-in-an-instrumented-project)
- [Extend an instrumented project](#extend-an-instrumented-project)
- [Verify and report](#verify-and-report)

## Add end-to-end monitoring

For a project with no observability yet, or with telemetry that doesn't follow the standard.

```text
Use the observability-builder skill: observability instrument <path>.

Goal: end-to-end observability for every part of this project that runs in production:
- logs: the app's own logger only, JSON on stdout, levels per the policy, constant messages with
  values as attributes, native trace and span ids on every record; every print or console call in
  application code routed through it; one access-log record per request
- traces: one trace per request, rooted at the server span with the route template; function
  spans for the API work (handlers, services, repositories that do I/O) with business attributes;
  a CLIENT span with a peer name for every call to a database, cache, queue, third-party API or
  other service; a root span for every scheduled or background job run and every WebSocket
  message; no middleware, start-up or helper spans
- metrics: the ops metrics on their own /metrics port with short, bounded labels and the
  standard buckets: HTTP server and client, connection pools, jobs, failed probes, runtime
- errors: one ERROR record with exception details per failure, written where it is handled;
  recovered failures as warnings; response bodies, status codes and routes unchanged
- profiling: continuous profiles with the resource as static labels, controller labels on the
  CPU-heavy code, and span ids on profiles where the runtime supports it
- correlation: logs, profiles and exceptions linked to their spans; outbound calls carry the
  trace context; browser traces continue into the API
- frontend, if the project has one: RUM and session replay through a same-origin proxy, with
  sensitive fields masked
- AI features, if the project calls a model:
  - a span for every model call, tool, retrieval and agent step, with token usage, and the GenAI
    metrics;
  - each AI feature declared once with its user and conversation;
  - prompts, completions and tool data only through the content policy (full outside production,
    off in production), and only to the LLM-observability backend, never to logs or the ops
    backend;
  - the backend's mode (hybrid, sdk or otel) switchable by configuration; its exporter takes
    nothing from the ops exporter's settings;
  - prompt versions linked (fetching prompts and datasets stays the app's own); feedback and
    approvals scored by the server
- shutdown: drain requests, wait for running jobs, flush every signal within the grace period

Answers I already have (delete what doesn't apply; the skill asks about the rest):
- service name <service>, namespace <app-group>, environment from <env var or value>
- destination: <collector endpoint and protocol | backend URL with the key in <mounted file>>;
  profiles to <profiles endpoint>
- logs: <stdout for the collector | OTLP | both>; level <info> in production, slow-step
  threshold <2s>
- metrics port <9464>, scraped by <collector | Prometheus>
- dependencies and their peer names: <host or client> as <peer-name>, ...
- scheduled and background jobs: <job-name> (<cron | manual | internal>), ...; work that
  continues after a response: <function>
- WebSocket endpoints and message types: <path>: <type>, <type>
- probes <paths> and API docs <paths>, excluded from traces, metrics and the access log
- heavy code for profiling labels: <files or functions>
- access-log allow-lists: request headers <names>, response headers <names>, body fields <names>
- business attributes to record: <function>: <attribute>, ...
- labels for the backend that vary by service: <key>=<value>, ...
- several clusters send to one backend: <yes | no>
- frontend: sensitive fields <fields>, consent tool <name | none>
- AI features and their entry points: <feature> (<routes, message types or jobs>), ...;
  LLM-observability backend <URL> with keys in <mounted files>, route <app | collector>; mode
  <hybrid | sdk | otel>, or switchable per environment (no sdk mode where the backend has no SDK
  for the language); content per environment <full in non-production | redacted in production
  for <features> | off>; prompts managed in <the backend, label <label> | code>; scores
  <feedback | approvals | evaluations | none>
- breaking changes allowed now: <access-log format | log format | metric names | removing a
  vendor SDK | none>

How to work:
1. Read the project's own rules (agent files, contributing guide, README) and follow them.
2. Detect the stack, the real start command and production runtime, existing telemetry,
   outbound clients, background work, WebSocket endpoints, probes, docs paths, where the
   verified user and tenant become available, and the code that calls models.
3. Ask only what the code can't answer, suggesting an answer for each question.
4. Before changing anything: run the audit, the project's build, type check and tests (keep the
   results per suite), record the API responses with scripts/contract.py while the app runs on
   throwaway dependencies, and draft the migration note.
5. Research current libraries for this stack from the package registries and
   references/sdk-notes.md; record each source and version.
6. Show me the plan: files, libraries, hook points, observability.yml, attributes per function,
   metrics and labels, jobs and WebSocket coverage, the chart handoff and the migration note.
   Wait for my confirmation.
7. Implement, then verify as in "Verify and report" below.
8. Report: files changed, dependencies, answers used, handoffs, migration note, functions the
   wrappers skipped and why, checks and results, open observability risks (nothing about
   security or code outside telemetry), and the telemetry inventory built from what the app
   really exported.

Rules: stop only the processes you start, by PID; never read .env or credential files; keep
secrets in mounted files; never call a real model provider (use scripts/llm_stub.py with a
dummy key); don't commit or push.
```

## Find gaps in an instrumented project

For a project instrumented earlier (with this skill or otherwise): compare it with the current
standard and change nothing.

```text
Use the observability-builder skill: observability audit <path>. Change nothing yet.

The observability module is <module path, or "find it">; observability.yml is <path, or "find it">.

1. Run scripts/audit.py <path> --module <module path> --json, then review by hand what the script
   can't judge (references/audit.md): attributes taken from function arguments, URLs with query
   values, SQL literals, payloads in exception messages, header or body capture in
   auto-instrumentation, browser input values.
2. Compare the code with its instrumentation and list every gap, by signal:
   - resource: exactly the standard attributes, identical across signals and the collector-side
     identity; nothing from the SDK's default detectors
   - traces: route templates on server spans, "unmatched" for 404s, status UNSET unless the
     span's own work failed, no middleware or start-up spans, function spans on the API work,
     a CLIENT span with a peer name for every outbound call, root spans for jobs and WebSocket
     messages, the sampler right for the service's position (public edge or internal)
   - logs: everything through the app's logger, the level policy, constant messages, native
     trace ids after the collector's mapping, one access-log record per request with
     allow-listed fields only, probes and docs paths excluded, failed probes reported
   - errors: one ERROR record with exception details per failure, none on warnings or recovered
     failures, response formats unchanged
   - metrics: the ops set on its own port, contract names, short bounded labels, standard
     buckets, no series for probes or docs, types preserved through the collector
   - profiling: identity labels, controller labels on the heavy code, span links where supported
   - correlation: logs, profiles and exceptions joined to spans; trace context on outbound calls
     and from the browser
   - coverage: routes, jobs, WebSocket message types, dependencies and heavy functions added
     since the last instrumentation that have no telemetry
   - safety: secrets, tokens, cookies, bodies, personal data or ids anywhere in telemetry
   - shutdown and failure behaviour: flush on signals and exits, the app keeps serving when the
     collector is down or the config is broken
   - frontend, if any: the proxy holds the key, the app identifier, web vitals delivered on tab
     close, delta metrics, replay masking
   - AI, if the project calls a model:
     - model, tool, retrieval and agent spans with the conventions' names and usage;
     - the feature context on every AI span and never in baggage, with baggage allow-listed on
       extract and inject;
     - content only through the content policy and only on the LLM-observability path, with no
       prompts or completions in logs, and a span attribute limit no smaller than the content cap;
     - capture switches set by the configuration loader, never left at a library's defaults;
     - one instrumentation per AI library;
     - the mode wired as configured, with an exporter that reads none of the ops exporter's
       headers, TLS files or compression;
     - scores sent by the server
3. If the app can run, run it on throwaway dependencies against scripts/receive.py (and
   scripts/llm_stub.py in place of model providers), send one request of each kind, and use the
   captured telemetry as evidence.
4. Report each gap as "[P0|P1|P2] [rule] path:line - what is wrong - the fix", most severe first,
   marking manual findings [judged]; then a short list of what already conforms. Never print a
   secret value. Wait for my go before fixing anything.
```

## Extend an instrumented project

For new or changed code in a project that is already instrumented: bring it to the same standard
with the existing module, all signals by default.

```text
Use the observability-builder skill on <path>; it is already instrumented. Keep observability.yml,
the observability module, its conventions and the earlier answers; reuse what exists and never
add a second way to do the same thing.

Scope: <folder | files | functions | everything changed since <git ref>>.
New in this scope (delete what doesn't apply): routes <routes>; outbound dependencies <name as
peer-name>; jobs <job-name> (<cron | manual | internal>); work after a response <function>;
WebSocket message types <types>; heavy code <functions>; frontend pages <pages> and sensitive
fields <fields>; AI features <feature> (<entry points>) and the content each may record.

For everything in scope, cover all signals:
- logs: logging through the app's logger with constant messages and attributes; print or console
  calls routed through it; step logs from the function wrappers; access-log allow-list entries
  for new fields <fields>, if any
- traces: function spans with the existing wrapper on handlers, services and repositories,
  business attributes <function: attribute, ...>; a CLIENT span with the peer name for each new
  outbound call; a job root span for each new job run; WebSocket message spans for new types;
  route templates for new routes
- errors: failures reach the global error handler or the job helper; recovered failures become
  warnings
- metrics: new dependencies in the client and pool metrics, new jobs in the job metrics, new
  probes and docs paths excluded; app metrics only those I list here: <name> (<counter | gauge
  around a function | histogram>) labelled <label> with values <values>
- profiling: controller labels, with span ids where supported, on the new heavy synchronous code
- correlation: the new logs, spans and profiles share trace ids; outbound calls carry the trace
  context; new frontend calls continue into the backend trace
- AI, in the project's configured mode: the feature declared at each new AI entry point; spans
  with usage for new model calls, tools and retrieval; content through the content policy; the
  GenAI metrics for new providers or models; scores for new feedback or approval steps

1. List what you will add per item in scope, and anything in scope you would skip and why. Wait
   for my go.
2. Implement only inside the scope.
3. Verify: the project's tests, scripts/audit.py --module <module path> with no P0 or P1, the API
   contract unchanged, and a run against scripts/receive.py (with scripts/llm_stub.py for AI
   features) showing the new spans, logs, metrics and profile labels.
4. Report what changed, the new telemetry, and anything left uncovered.
```

## Verify and report

For checking an instrumentation before merging or after any change, and for the report the
team and the platform owners need.

```text
Use the observability-builder skill on <path>: verify the instrumentation and give me the report.
Change application code only to fix what the checks find, and tell me before you do.

1. Static checks: scripts/validate.py on observability.yml; scripts/audit.py --module <module path>
   (no P0 or P1, and every remaining finding explained).
2. The project's build, type check and tests, compared suite by suite with the results from
   before the instrumentation (<baseline results | the commit <git ref>>). A failure that was
   there before is not a regression; show flaky tests by running them twice.
3. The API contract: scripts/contract.py compare against the recording from before the change;
   every status, content type and body unchanged except the changes I approved.
4. A real run on throwaway dependencies against scripts/receive.py, with the project's own start
   command. Send a success, a write that calls a dependency, a 404, a 401, a failure, the probes,
   a failed readiness probe and the API docs page; trigger or wait for each job; then stop the app
   with SIGTERM. Check every acceptance check in references/acceptance.md: resource attributes,
   trace shape, span status, log records and their trace ids, the /metrics scrape, profiles and
   their labels, exceptions, correlation between signals, and the last telemetry flushed at stop.
   For AI features, run twice against scripts/llm_stub.py and the receiver's LLM-observability
   stand-in, as a non-production environment with full content and as production with content
   off: content appears only on the LLM-observability path of the first run. Where environments
   switch modes, repeat the full-content run in each mode.
5. Failure behaviour, the app serving through each: the collector down, the SDK disabled, a
   missing or broken observability.yml, the metrics port already taken.
6. If <backend URL> is available, run the same traffic through the collector into it and confirm
   traces, logs, metrics, profiles and exceptions arrive, linked to each other.
7. Grep everything captured, stdout and stderr for the test secrets and personal values sent,
   and for the prompt canaries outside the LLM-observability path.

Report: the checks with their results and evidence; files changed; dependencies with versions
and sources; the platform handoff list (chart values, environment, metrics port and monitor,
pod annotations, mounted secrets, collector settings) as an exact list; the migration note;
open observability risks; and the telemetry inventory: metrics with types and labels, logging
that bypasses the logger and why, work outside the API with its coverage, the attributes per
kind of span, and the AI features with their content modes and the mode in use.
```
