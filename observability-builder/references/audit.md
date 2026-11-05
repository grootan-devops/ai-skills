# Audit telemetry

Steps for `observability audit`. The audit checks that instrumentation is safe, in the right
place and not bypassed; it changes nothing unless the user asks for fixes. Rules come from
[safe-capture.md](safe-capture.md), [overhead-and-security.md](overhead-and-security.md),
[layout.md](layout.md), [data-model.md](data-model.md),
[signals/app-logs.md](signals/app-logs.md) and [workloads/batch.md](workloads/batch.md).

## 0. Platform and context

Before scanning, find where the service runs, so the resource rules check the right rows
([data-model.md](data-model.md#instance-identity-per-platform)). Read the deployment files in
the repository:

| Evidence | Platform |
| --- | --- |
| Helm chart, Kustomize or plain Kubernetes manifests | Kubernetes |
| An ECS task definition (JSON, Terraform `aws_ecs_task_definition`, CDK, Copilot manifest) | ECS / Fargate |
| A Lambda handler with SAM, Serverless Framework, CDK or Terraform `aws_lambda_function` | Lambda |
| A Knative `serving.knative.dev` service, `gcloud run deploy` in CI | Cloud Run service |
| A Cloud Run job definition, `gcloud run jobs` in CI | Cloud Run job |
| systemd units, VM provisioning (Ansible, Packer, cloud-init) | VM |
| A Dockerfile run with `docker run` or Compose, and no orchestrator | plain Docker |

A service may run on several (Compose locally, Kubernetes in production): check each. Where
nothing says, ask the user, and name the assumption in the report. Then note which auth,
session and tenant code the service has: it decides which identity rows apply, and the
reference-catalogue rows are checked only where that code has the value
([data-model.md](data-model.md#request-and-identity-context)).

## 1. Static scan

```bash
python3 scripts/audit.py <project> --module <observability-module-path> [--json]
```

The script flags patterns it can see in source code. A value read into a variable is followed
through its block, and a call spread over several lines is read as one statement:

| Rule | Severity | What it catches |
| --- | --- | --- |
| `secret-capture` | P0 | Span attributes, log or print calls, or baggage carrying authorization, cookies, passwords, tokens, secrets or API keys. Caught directly, or through a variable read earlier in the same block (`const h = req.get('authorization')` then `console.log(h)`). Includes framework-injected header parameters such as FastAPI `Header()`, `@RequestHeader`, `@HeaderParam`, NestJS `@Headers()`, Django `request.META`, Gin `GetHeader`. |
| `body-in-span` | P1 | Request or response bodies (or stringified payloads) set as span attributes. |
| `body-logged` | P1 | A raw request body logged: directly (`body: req.body`), or through a variable that holds the whole body or its serialization. Single fields, sizes and validation errors are not flagged. |
| `secret-in-error` | P1 | A value named like a secret (`debugToken`, `api_key`) put into an error message. Messages reach logs and `exception.message`. |
| `secret-named-value` | P1 | A value named like a secret reaching a log, print or span call when the script can't see where it came from. Confirm it isn't one, or log allow-listed values only. |
| `id-metric-label` | P1 | ID-like values used as metric attributes: user, session, tenant, organisation, account, customer, order, request, correlation, trace or span IDs, emails, raw URLs or paths ([data-model.md](data-model.md#metrics)). |
| `id-profile-label` | P1 | IDs used as profiling labels. |
| `profile-label-mismatch` | P2 | Static profile labels that don't come from the resource: a literal service name, version or environment in the profiler's start call instead of the module's resolved values, a platform label the resource doesn't have, or an R or O resource row (`process_runtime_*`, `telemetry_*`, `os_type`) as a label ([data-model.md](data-model.md#profile-labels)). |
| `identity-from-header` | P1 | `user.id`, `tenant.id`, `session.id`, `user.roles` or another identity row set on a span, a log or baggage from a raw request header, cookie or query value (`req.headers['x-user-id']`, `request.headers.get('X-Tenant-Id')`, `@RequestHeader("X-User-Id")`, `c.GetHeader("X-Tenant")`), rather than from the trusted auth or tenant middleware after authentication ([data-model.md](data-model.md#request-and-identity-context)). |
| `request-id-attribute` | P2 | A `request.id` attribute or log field. The standard uses `correlation.id` only. |
| `code-namespace` | P2 | `code.namespace` set on a span or log. It is deprecated: `code.function.name` carries the fully qualified name. |
| `pii-unmasked` | P1 | A value named like personal data (email, phone, name, address, date of birth, national ID, passport, tax ID, IBAN, bank account) reaching a log, print, span attribute or exception message without passing through the module's masking helper ([data-model.md](data-model.md#pii-masking)). |
| `baggage-over-limit` | P2 | A baggage allow-list with more than 8 keys, or a baggage propagator without the 512-byte cap; baggage set outside the module; a secret, authorisation or AI context key on the allow-list ([data-model.md](data-model.md#trace-context)). |
| `resource-excluded-key` | P2 | A resource attribute the standard never adds, set in the yml, a chart, `OTEL_RESOURCE_ATTRIBUTES` or code: `k8s.pod.uid` (and a `K8S_POD_UID` downward-API variable), `service.instance.id`, `host.id`, `deployment.id`, `process.pid`, command lines, `process.executable.*` or `vcs.*`; or a process or host-id detector enabled unfiltered ([sdk-notes.md](sdk-notes.md#resource-detectors)). |
| `raw-headers-logged` | P1 | Whole header objects logged. |
| `sdk-outside-module` | P1 | SDK, provider or exporter setup outside the observability module: also Langfuse clients and processors (`Langfuse(`, `new LangfuseSpanProcessor(`), OpenLLMetry's `Traceloop.init`, GenAI instrumentors (`OpenAIInstrumentor().instrument()`) and `registerInstrumentations(`. The loader's switches must be written before any of them starts. |
| `otel-env-outside-loader` | P2 | `OTEL_*`, `LANGFUSE_*`, `TRACELOOP_*` or `OPENINFERENCE_*` variables read or written outside the module. |
| `raw-xff` | P2 | `X-Forwarded-For` logged instead of a resolved client address. |
| `logger-bypass` | P2 | Output written around the app logger: `console.log`, `.info`, `.warn`, `.error` and `.debug` (JS, TS, `.vue`, `.svelte`); `print(` (Python); `fmt.Print*`, and `log.Print*` where the file imports the standard `log` package (Go); `System.out` and `System.err` `print*`, and Kotlin's top-level `println(` (JVM); `println!` and `eprintln!` (Rust). Checked only where the project has an app logger in the same language (a file named like `logger.*` or `logging.*`, a logging library in the manifest, or the logging module used in code), which the finding names. Those lines carry no level, format or trace ids, and an error printed that way never becomes an exception record ([signals/app-logs.md](signals/app-logs.md)). Skipped: tests, the `--module` paths, browser code, the logger files, and code that prints by design (`scripts/`, `cli/`, `bin/`, migrations, seeds, `management/commands/`, top-level `examples/`, `samples/`, `benches/` and `docs/`, `cli.*`, `migrate.*`, `seed.*`, `manage.py`, `setup.py`, `__main__.py`, files with a shebang, `*.config.js` and `.ts`, `build.rs` and `build.js`); also comments, docstrings and multi-line strings. More than 5 calls in one file are one finding, "and N more in this file". |
| `scheduler-no-root-span` | P2 | A scheduled job registered in a file that starts no job span: node-cron `cron.schedule(`, `schedule.scheduleJob(`, `new CronJob(` or `CronJob.from(`, NestJS `@Cron(` (JS); APScheduler `add_job(` or `@scheduler.scheduled_job`, `@periodic_task`, `schedule.every(` (Python); robfig/cron `AddFunc` and `AddJob` (Go); `@Scheduled` (JVM); tokio-cron-scheduler `Job::new*` (Rust). A job span is a job helper call (`withJobRun`, `with_job_run`, `run_job`, `job_span` …) or a span started with a new-root marker (`root: true`, `ROOT_CONTEXT`, `new_root`, `setNoParent`, `WithNewRoot`, `context=Context()`). Repeating queue jobs (agenda `.every(`, bull `repeat:`, BullMQ `upsertJobScheduler(`, celery `beat_schedule` and `add_periodic_task(`) keep their body elsewhere, so any job span in the project counts for them. `setInterval` and other plain timers aren't checked. With the Java agent, `@Scheduled` runs already get a span, so there the finding is about the missing job helper: the job log record and the job metrics ([workloads/batch.md](workloads/batch.md)). |
| `sdk-internal-patch` | P2 | A patch of an observability SDK's internals, in the observability module too: an assignment to the prototype or a private member of a name imported from `@opentelemetry/*`, `opentelemetry.*`, `io.opentelemetry.*`, `prom-client`, `@prometheus-io/client`, `prometheus_client` or `io.prometheus.*`, or of an object built from one (`X.prototype.y =`, `X._y =`, `(X as any).y =`, `Object.defineProperty` or `Object.assign` on `X.prototype`, `shimmer.wrap(X.prototype, …)`); Python `setattr(X, …)`, `X._y =` and assignments to an SDK class's attributes; JVM reflection (`getDeclaredField`, `getDeclaredMethod`, `Class.forName`) into those classes; Go `//go:linkname` into `go.opentelemetry.io/*` or `github.com/prometheus/*`. The patch breaks silently on the next upgrade: use a documented option, a wrapper or a processor, or report the gap ([sdk-notes.md](sdk-notes.md)). |
| `browser-key` | P0 | A telemetry backend key, a key header (Dynatrace's `Api-Token` scheme), a public build variable named like a telemetry key (`NEXT_PUBLIC_`, `VITE_`, `PUBLIC_`, `NUXT_PUBLIC_`, `REACT_APP_`, `NG_APP_` with `OTEL`, `OTLP`, `TELEMETRY`, `PYROSCOPE`, `OBSERVABILITY` or `INGESTION` … `_TOKEN` or `_KEY`) or such a key in public runtime config (`useRuntimeConfig().public`) in code that runs in the page: `'use client'` modules, `.vue` and `.svelte` components, `*.client.*` modules, Angular app code, `instrumentation-client`. Telemetry keys stay on the server. |
| `public-key-var` | P1 | The same telemetry public variable or public config key anywhere else. Bundlers ship it to the page as soon as client code reads it; the key stays a server-only variable. Other secrets in a bundle aren't telemetry and aren't reported. |
| `langfuse-key` | P0 | A Langfuse secret key in source (`sk-lf-` followed by hex), or a Langfuse secret key read in code that runs in the page. |
| `genai-content-outside-module` | P1 | An AI content key set on a span outside the module: `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, tool definitions, arguments and results, retrieval query and documents, `langfuse.observation.input`/`output`, `traceloop.entity.*`, `input.value`, `llm.*_messages.*`, or their convention constants (`GEN_AI_INPUT_MESSAGES` …). The content policy (mode, redaction, sampling, size cap) can't apply ([signals/genai.md](signals/genai.md#content)). |
| `genai-content-unguarded` | P1 | A Langfuse SDK call that records an input or output outside the module, in a file that imports a Langfuse SDK: `span.update(output=…)`, `start_as_current_observation(…, input=…)`, `startObservation(…, { input })`, `updateActiveObservation({ output })`. Content goes through the module's content helper. |
| `genai-content-logged` | P1 | A value named like AI content (`prompt`, `messages`, `completion`, `choices`, `system_prompt`, `chat_history`, `tool_args`, `retrieved_docs` …) reaching a log or print call, in a project whose manifests name a model provider, an AI framework or an LLM-observability tool. Lengths and counts aren't flagged. |
| `genai-capture-on` | P1 | AI content capture switched on in code or framework configuration: `TRACELOOP_TRACE_CONTENT`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` or `LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED` set on, an `OPENINFERENCE_HIDE_*` set to false, `traceContent`, `recordInputs`, `recordOutputs`, `captureInput` or `captureOutput` set to `true`, `capture_input=True`, and Spring AI's `log-prompt`, `log-completion` or `include-*` set to true in `application*` or `bootstrap*` configuration. Not flagged in the module, where the loader writes the switches. |
| `genai-baggage` | P1 | `as_baggage=True` or `asBaggage: true`: the AI trace context goes into baggage, which every outbound call carries, the model provider included. Flagged in the module too. |
| `genai-capture-default` | P2 | Capture left at a library's default where no variable governs it: the AI SDK's `experimental_telemetry` on without `recordInputs` and `recordOutputs`, Langfuse's JS `observe()` without `captureInput` and `captureOutput`. |
| `genai-context-outside-module` | P2 | `propagate_attributes`, `propagateAttributes`, `update_current_trace` or `updateActiveTrace` outside the module, in a file that imports a Langfuse SDK. The trace context comes from `genai.feature()`, which writes neither `session.id` nor baggage. |
| `genai-browser-score` | P2 | Langfuse's browser SDK (`@langfuse/browser`, `new LangfuseWeb(`) or a score call in code that runs in the page: the public key lets anyone score any trace. Score through the app's API ([workloads/ai-agent.md](workloads/ai-agent.md#user-feedback)). |
| `genai-langfuse-two-senders` | P2 | Langfuse's OTLP endpoint (`/api/public/otel`) configured outside the module: in code, env and compose files, chart values or app configuration. The module already sends each span once; a second exporter sends every observation twice ([signals/genai.md](signals/genai.md#modes)). |
| `genai-duplicate-instrumentation` | P2 | One AI library (OpenAI, Anthropic, LangChain, LlamaIndex) instrumented by two families, from the manifests and imports: OpenTelemetry's GenAI packages, OpenLLMetry (its packages or `traceloop-sdk`), OpenInference, Langfuse's OpenAI wrapper or LangChain callback handler. Every call is recorded twice. |
| `secret-in-query` | P2 | An API key, token, password or signature sent as a URL query parameter. `url.full`, `url.query` and access logs carry it unless the instrumentation redacts query values. Check that it does, and check whether the URL is logged anywhere. |
| `sync-exporter` | P1 | A simple or synchronous span or log processor (`SimpleSpanProcessor`, `SimpleLogRecordProcessor`, `WithSyncer`) outside tests: every request waits on the export ([overhead-and-security.md](overhead-and-security.md#never-on-the-request-path)). |
| `exporter-no-timeout` | P2 | An exporter built in code, or an exporter block in the yml, without a timeout: a hung export holds a worker or the shutdown. |
| `plaintext-remote-endpoint` | P1 | An `http://` or `insecure` exporter endpoint to a host that isn't loopback, without a comment naming the private hop it uses (a sidecar, or an in-cluster collector behind a NetworkPolicy or mesh mTLS) ([overhead-and-security.md](overhead-and-security.md#transport)). |
| `telemetry-credential-literal` | P0 | An auth header, API key or token for an exporter, collector or Langfuse written as a literal in code or the yml, instead of `${file:...}` or `${env:...}` ([overhead-and-security.md](overhead-and-security.md#credentials)). |
| `metrics-public-bind` | P2 | The `/metrics`, profiling or debug server bound to `0.0.0.0` without a yml comment saying a remote scraper needs it; a same-pod, same-task or same-instance scraper takes loopback ([overhead-and-security.md](overhead-and-security.md#exposed-surfaces)). |
| `attribute-limit-raised` | P2 | The global span attribute value length limit set above 8192 (`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `SpanLimits`): it applies to every span. The content helper caps AI content itself at `genai.content.max_chars` ([overhead-and-security.md](overhead-and-security.md#size-limits)). Accepted when the limit equals the yml's `max_chars` and a comment on the line or above names `max_chars`: the project raised both together and records the exception in the report. |
| `log-extras-passthrough` | P1 | A log formatter that emits every extra field on the record (all of `record.__dict__`, every MDC entry, a spread of the whole bindings) instead of an allow-list: any library's extras reach the logs unchecked. |
| `eager-debug-format` | P2 | An f-string, `format` call or string concatenation passed to a debug call in the module's wrappers: it is built on every call, even with debug off. Check the level first or pass the values as arguments ([overhead-and-security.md](overhead-and-security.md#cheap-hot-paths)). |

The last eight rules check the instrumentation's own performance and security
([overhead-and-security.md](overhead-and-security.md)).

Browser RUM with Grafana Faro, checked in every file that imports `@grafana/faro-*` or calls
`initializeFaro` ([signals/rum.md](signals/rum.md)):

| Rule | Severity | What it flags |
| --- | --- | --- |
| `faro-key-in-bundle` | P0 | An `apiKey` literal in page code while `observability.yml` sets `rum.transport: proxy`: the proxy adds the key and the page must hold none. |
| `faro-event-raw-url` | P1 | `pushEvent`, `pushLog`, `pushMeasurement` or `pushError` with `location.href`, `document.URL` or `window.location`: the full URL with its query goes out as an attribute. |
| `faro-propagate-all` | P1 | A match-all `propagateTraceHeaderCorsUrls` (`/.*/`, `'*'`, `new RegExp('.*')`, `/https?:\/\/.*/`): trace headers and CORS preflights go to every host the page calls. |
| `faro-user-pii` | P1 | `setUser` or a `user` meta with an email, username, full name or phone. Faro copies the user meta onto every record and span. |
| `faro-no-scrub` | P2 | `initializeFaro` without a `beforeSend` scrubber in the file: URLs with query values, and emails and tokens in messages, leave the page unchanged. |
| `faro-console-all` | P2 | `disabledLevels: []`: every console level captured, `debug` and `log` included. |
| `faro-build-env-config` | P2 | Faro's URL, key, endpoint or environment read from a build-time public variable (`import.meta.env.*`, `process.env.NEXT_PUBLIC_*`, `REACT_APP_*`): one build can't serve two environments. |
| `faro-key-literal` | P2 | The receiver key as a literal in page code (transport `faro`): read it from the runtime config. A Faro key is reported by these two rules only, never as `telemetry-credential-literal`: in transport `faro` it is public by design. |
| `faro-geolocation` | P2 | `trackGeolocation: true`: Grafana Cloud only, and personal data. |
| `faro-report-all-changes` | P2 | `reportAllChanges: true`: a debugging setting that sends every web-vital change. |
| `faro-user-not-reset` | P2 | `setUser` with no `resetUser` anywhere in the project: after sign-out, the next person's activity is recorded as the previous user. |
| `rum-access-log-query` | P2 | In a project with Faro, an nginx config whose access log records the query (`combined` or the default format, `$request`, `$request_uri`, `$args`): OAuth codes and search text from page URLs reach the logs. |
| `faro-public-sourcemaps` | P2 | In a project with Faro, a bundler config that emits public source maps (`sourcemap: true` or `'inline'`, `devtool: 'source-map'`, `productionBrowserSourceMaps: true`). Build them hidden and give them to the receiver. |

Judge as well:

- user-action names taken from element text instead of `data-faro-user-action-name`;
- a data router without a reporting `errorElement`;
- views left at `default` (no `setView` on route change);
- `pushEvent` or `pushMeasurement` attributes holding ids of other users, input values or free
  text;
- an SPA fallback that would serve `index.html` for a missing `observability.json`;
- an nginx access log with `$request_uri`;
- a public `faro.receiver` without rate limiting, with `cors_allowed_origins = ["*"]`, or with
  source-map downloads on.

Then judge what regexes can't: attributes built from function arguments, a secret passed into a
function that logs it in another file, URLs with query values, emails in request paths left
unmasked, SQL with literals, exception messages that embed payloads, log messages that
interpolate values ([signals/app-logs.md](signals/app-logs.md)), auto-instrumentation with
header or body capture switched on, a framework
instrumentation left creating middleware spans, a platform detector wired for a platform the service doesn't run on (or missing for one it does), a per-request value in the resource, jobs run from
`setInterval` or other plain timers without a job span, WebSocket handlers without root spans,
and Node.js profiling labels wrapped around whole async functions. For identity: context set
before authentication succeeds, request context kept in a global or a thread-local that isn't
cleared (one tenant's identity reaching another's request), a placeholder written for an
anonymous user, and a reference-catalogue row set from a value the code doesn't really have.
For AI code: content passed
into a function that logs it in another file, AI content in `app.*` attributes (the ops
allow-list keeps them), AI entry points without a feature declaration, a feature allowed
`redacted` without a redactor, streamed model calls without usage, generations of a managed
prompt that don't pass the prompt it used to the model-call helper, a Langfuse client built
outside the module, scores without stable ids, AI entry points whose user or team comes from
request input rather than the verified context, a sub-agent wrapped in both a tool span and an
agent span, and an MCP tool call traced twice (by the SDK and the module, or by the agent's
framework and the MCP client), and an A2A call without an `invoke_agent` CLIENT span or
`traceparent` ([workloads/a2a.md](workloads/a2a.md)). Tag these findings `[judged]`.

## 2. Dynamic scan (when telemetry is available)

When the app runs, capture its telemetry with the local receiver, `scripts/receive.py`
([acceptance.md](acceptance.md), "Local receiver"; see instrument step 7 for the command) and search it,
and the app's stdout and stderr, for secret patterns (JWT, bearer tokens, API keys, card numbers, private keys,
`Authorization` / `Cookie` values), bodies in span attributes, unbounded metric label values
(in the `/metrics` scrape too), unexpected baggage or baggage over 8 keys or 512 bytes, raw
personal data that the masking helper should have masked, and resource rows that don't match
the detected platform ([acceptance.md](acceptance.md#platform-resource)). For AI services, the receiver's summary lists the content keys per
stream and flags any in the ops stream, AI keys on spans that aren't AI calls, AI spans without
the conversation, and a second Langfuse span sender; content in stdout
or in a production run's streams is a finding too.

The receiver also checks the captured telemetry against the [data model](data-model.md) and
writes `conformance.json` plus a "Data model checks" section in `summary.md`: the M, C and P rows
for the resource, spans, logs, metric labels and profile labels. Pass `--platform` (`k8s`, `ecs`,
`lambda`, `cloud-run`, `cloud-run-job`, `vm`, `cloud-vm`, `docker`, `local`) when it can't be inferred from the resource.
R and O rows are never findings.

For a page with RUM, point Faro at the receiver's `/collect` (the `faro` endpoint it prints;
CORS is open for local testing) and drive the page. The receiver stores each batch, and its
summary has a "Browser RUM (Faro)" section with counts per record kind, apps, sessions and
views. Its data-model checks cover the Faro rows: required metas, the user's personal fields,
personal data and secrets in any field, URLs with queries, browser spans carrying `user.email` or
`user.name`, and every record left on the `default` view. Canaries are searched in the Faro stream
too.

## 3. Report

One line per finding, most severe first:

```text
[P1] [id-metric-label] src/orders/service.ts:88 metric attribute "user.id" — use a bounded dimension (plan, outcome) and keep the ID on the span
```

After the findings, add the telemetry inventory from [instrument step 8](instrument.md#8-report):
metrics and labels, logging outside the logger, work outside the API, and attributes. Build it
from the dynamic scan when telemetry was available, otherwise from the code, and say which.

Exit codes from the script: `0` no findings, `1` findings with `--strict`, `2` any P0. Offer
fixes; apply them only when the user agrees, then re-run the audit.
