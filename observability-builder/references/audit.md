# Audit telemetry

Steps for `observability audit`. The audit checks that instrumentation is safe, in the right
place and not bypassed; it changes nothing unless the user asks for fixes. Rules come from
[safe-capture.md](safe-capture.md), [layout.md](layout.md), [data-model.md](data-model.md),
[signals/app-logs.md](signals/app-logs.md) and [workloads/batch.md](workloads/batch.md).

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
| `id-metric-label` | P1 | User, session, tenant, order, request or trace IDs used as metric attributes. |
| `id-profile-label` | P1 | IDs used as profiling labels. |
| `raw-headers-logged` | P1 | Whole header objects logged. |
| `sdk-outside-module` | P1 | SDK, provider or exporter setup outside the observability module: also Langfuse clients and processors (`Langfuse(`, `new LangfuseSpanProcessor(`), OpenLLMetry's `Traceloop.init`, GenAI instrumentors (`OpenAIInstrumentor().instrument()`) and `registerInstrumentations(`. The loader's switches must be written before any of them starts. |
| `otel-env-outside-loader` | P2 | `OTEL_*`, `LANGFUSE_*`, `TRACELOOP_*` or `OPENINFERENCE_*` variables read or written outside the module. |
| `raw-xff` | P2 | `X-Forwarded-For` logged instead of a resolved client address. |
| `logger-bypass` | P2 | Output written around the app logger: `console.log`, `.info`, `.warn`, `.error` and `.debug` (JS, TS, `.vue`, `.svelte`); `print(` (Python); `fmt.Print*`, and `log.Print*` where the file imports the standard `log` package (Go); `System.out` and `System.err` `print*`, and Kotlin's top-level `println(` (JVM); `println!` and `eprintln!` (Rust). Checked only where the project has an app logger in the same language (a file named like `logger.*` or `logging.*`, a logging library in the manifest, or the logging module used in code), which the finding names. Those lines carry no level, format or trace ids, and an error printed that way never becomes an exception record ([signals/app-logs.md](signals/app-logs.md)). Skipped: tests, the `--module` paths, browser code, the logger files, and code that prints by design (`scripts/`, `cli/`, `bin/`, migrations, seeds, `management/commands/`, top-level `examples/`, `samples/`, `benches/` and `docs/`, `cli.*`, `migrate.*`, `seed.*`, `manage.py`, `setup.py`, `__main__.py`, files with a shebang, `*.config.js` and `.ts`, `build.rs` and `build.js`); also comments, docstrings and multi-line strings. More than 5 calls in one file are one finding, "and N more in this file". |
| `scheduler-no-root-span` | P2 | A scheduled job registered in a file that starts no job span: node-cron `cron.schedule(`, `schedule.scheduleJob(`, `new CronJob(` or `CronJob.from(`, NestJS `@Cron(` (JS); APScheduler `add_job(` or `@scheduler.scheduled_job`, `@periodic_task`, `schedule.every(` (Python); robfig/cron `AddFunc` and `AddJob` (Go); `@Scheduled` (JVM); tokio-cron-scheduler `Job::new*` (Rust). A job span is a job helper call (`withJobRun`, `with_job_run`, `run_job`, `job_span` …) or a span started with a new-root marker (`root: true`, `ROOT_CONTEXT`, `new_root`, `setNoParent`, `WithNewRoot`, `context=Context()`). Repeating queue jobs (agenda `.every(`, bull `repeat:`, BullMQ `upsertJobScheduler(`, celery `beat_schedule` and `add_periodic_task(`) keep their body elsewhere, so any job span in the project counts for them. `setInterval` and other plain timers aren't checked. With the Java agent, `@Scheduled` runs already get a span, so there the finding is about the missing job helper: the job log record and the job metrics ([workloads/batch.md](workloads/batch.md)). |
| `sdk-internal-patch` | P2 | A patch of an observability SDK's internals, in the observability module too: an assignment to the prototype or a private member of a name imported from `@opentelemetry/*`, `opentelemetry.*`, `io.opentelemetry.*`, `prom-client`, `@prometheus-io/client`, `prometheus_client` or `io.prometheus.*`, or of an object built from one (`X.prototype.y =`, `X._y =`, `(X as any).y =`, `Object.defineProperty` or `Object.assign` on `X.prototype`, `shimmer.wrap(X.prototype, …)`); Python `setattr(X, …)`, `X._y =` and assignments to an SDK class's attributes; JVM reflection (`getDeclaredField`, `getDeclaredMethod`, `Class.forName`) into those classes; Go `//go:linkname` into `go.opentelemetry.io/*` or `github.com/prometheus/*`. The patch breaks silently on the next upgrade: use a documented option, a wrapper or a processor, or report the gap ([sdk-notes.md](sdk-notes.md)). |
| `browser-key` | P0 | A telemetry backend key, a key header (`x-oneuptime-token`), a public build variable named like a telemetry key (`NEXT_PUBLIC_`, `VITE_`, `PUBLIC_`, `NUXT_PUBLIC_`, `REACT_APP_`, `NG_APP_` with `ONEUPTIME`, `OTEL`, `OTLP`, `TELEMETRY`, `RUM`, `REPLAY`, `PYROSCOPE`, `OBSERVABILITY` or `INGESTION` … `_TOKEN` or `_KEY`) or such a key in public runtime config (`useRuntimeConfig().public`) in code that runs in the page: `'use client'` modules, `.vue` and `.svelte` components, `*.client.*` modules, Angular app code, `instrumentation-client`. RUM and replay must go through the same-origin proxy. |
| `public-key-var` | P1 | The same telemetry public variable or public config key anywhere else. Bundlers ship it to the page as soon as client code reads it; the same-origin proxy holds the key. Other secrets in a bundle aren't telemetry and aren't reported. |
| `rum-propagate-all` | P1 | `propagateTraceHeaderCorsUrls` set to match every origin. |
| `rum-input-capture` | P1 | An input value or page text (`.value`, `.textContent`, `.innerText`) passed to browser telemetry. |
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

Then judge what regexes can't: attributes built from function arguments, a secret passed into a
function that logs it in another file, URLs with query values, emails in request paths left
unmasked, SQL with literals, exception messages that embed payloads, log messages that
interpolate values ([signals/app-logs.md](signals/app-logs.md)), auto-instrumentation with
header or body capture switched on, RUM code that reads input values, a framework
instrumentation left creating middleware spans, SDK resource detectors left on, jobs run from
`setInterval` or other plain timers without a job span, WebSocket handlers without root spans,
and Node.js profiling labels wrapped around whole async functions. For AI code: content passed
into a function that logs it in another file, AI content in `app.*` attributes (the ops
allow-list keeps them), AI entry points without a feature declaration, a feature allowed
`redacted` without a redactor, streamed model calls without usage, generations of a managed
prompt that don't pass the prompt it used to the model-call helper, a Langfuse client built
outside the module, scores without stable ids, AI entry points whose user or team comes from
request input rather than the verified context, a sub-agent wrapped in both a tool span and an
agent span, and an MCP tool call traced twice (by the SDK and the module, or by the agent's
framework and the MCP client). Tag these findings `[judged]`.

## 2. Dynamic scan (when telemetry is available)

When the app runs, capture its telemetry with the local receiver, `scripts/receive.py`
([acceptance.md](acceptance.md), "Local receiver"; see instrument step 7 for the command) and search it,
and the app's stdout and stderr, for secret patterns (JWT, bearer tokens, API keys, card numbers, private keys,
`Authorization` / `Cookie` values), bodies in span attributes, unbounded metric label values
(in the `/metrics` scrape too), unexpected baggage and raw input values from the browser. For
AI services, the receiver's summary lists the content keys per destination and flags any
outside the service's content destination, AI keys on spans that aren't AI calls, AI spans
without the user, team or conversation, and a second Langfuse span sender; content in stdout
or in a production run's streams is a finding too.

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
