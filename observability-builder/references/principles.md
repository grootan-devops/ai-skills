# Principles

The ideas behind every other page of the standard. Read this page first; when a page does not cover
a case, decide it with these principles.

## Rules and standards, not templates

The standard is rules, never per-framework code:

| Part | What it defines | Read |
|---|---|---|
| Telemetry data model | every field a service emits, where it belongs, where its value comes from, and which values never leave the process | [data model](data-model.md), [enrichment](enrichment.md), [functions](functions.md), [safe capture](safe-capture.md) |
| Config contract | the shape of `observability.yml` and its JSON Schema | [configuration](config.md) |
| Integration rules | where observability code lives, how it hooks into a framework, how libraries are chosen, how existing monitoring is migrated | [layout](layout.md), [hooks](hooks.md), this page |
| Conformance checks | black-box checks against the running application | [acceptance](acceptance.md) |

An AI agent applying the standard researches each project's libraries, hooks and versions at run
time, from official documentation and API references. A list shipped here would be out of date by
the time it is read; the rules are what stay stable. Libraries are chosen in this order:

1. the framework's built-in instrumentation;
2. official OpenTelemetry instrumentation, as listed in the OpenTelemetry registry;
3. a plugin published by the framework's own maintainers;
4. a maintained community library: a release in the last 12 months, real adoption and a
   compatible licence.

Versions come from package-manager metadata and usage from official documentation. Deprecated,
squatted and wrong-publisher packages are rejected, and the sources used are recorded.

**Outputs are standardised, code is not.** Each project produces the same telemetry in its own
idioms. Black-box checks prove it against the running application: a local OTLP receiver
(`scripts/receive.py`), real requests, a `/metrics` scrape, stdout capture and a headless browser
for RUM. They assert the data model and the safe-capture rules, inject collector and backend
failures, and show that switching backends needs configuration changes only.

## Vendor neutrality

Applications speak open protocols, and the collector adapts them to each backend:

- traces go to the collector as **OTLP over gRPC** with batching, retries and gzip compression,
  and so do logs when they are pushed;
- ops metrics come from each stack's mainstream Prometheus client library and are served on
  `/metrics`, on their own port, for Prometheus or the collector to scrape; OTLP metric push is
  off unless a team switches it on for its own app metrics;
- logs are written to stdout and shipped by the collector, or pushed over OTLP;
- profiles use the Pyroscope protocol, or OTLP profiles where the backend accepts them;
- span and log attributes follow OpenTelemetry semantic conventions; metric names and their
  short labels follow the standard's contract ([metrics](signals/metrics.md)).

Endpoints, authentication, TLS, protocols, metric temporality, label prefixes and backend-specific
attributes are configuration: `observability.yml` on the application side, or the collector's
exporters. Moving between backends (OneUptime, a Grafana stack with Tempo, Dynatrace, New Relic)
is a configuration change, and the conformance checks confirm that nothing else changes. The collector may
send one signal to several backends. The profile format is the one per-backend difference an
application may see.

The exception is **RUM and session replay**, which target OneUptime; browsers send OTLP over HTTP.
Their destination and keys still come from configuration. **LLM observability** is the second:
the AI part of each trace, with prompts and completions where the content policy allows them,
goes to Langfuse as well, from a processor on the same tracer provider or from the collector.
The module keeps every Langfuse-specific key in one translation table, so the rest of the trace
stays plain OpenTelemetry ([signals/genai.md](signals/genai.md)).

## A thin config wrapper

`observability.yml`, checked by a JSON Schema, is a thin wrapper over OpenTelemetry's own settings,
not a new abstraction. At start-up a small loader in the project's language reads it, resolves
`${...}` references (environment variables and mounted secret files; secrets never appear as
literal values) and writes the standard settings, `OTEL_*` environment variables or SDK options,
before the SDK, agent or framework starts. OpenTelemetry then works unchanged, and its own
documentation stays valid.

- Only features OpenTelemetry has no setting for (the access log, path exclusions, the
  `/metrics` server, the slow-step threshold, profiling, RUM, and GenAI's content policy and
  Langfuse destination) read their own sections of the file.
- Agents that read environment variables when they start get their values by running the loader
  first, in the entrypoint.
- The file is the single source. Environment variables enter only through `${...}`; the loader
  overwrites stray `OTEL_*` values with the file's and logs a warning.
- The file holds connection details, static attributes, toggles and the access-log and exclusion
  lists. Span attributes, enrichment, function wrappers, step logs, profiling labels and RUM masking
  are code.

Details: [configuration](config.md).

## Leverage framework built-ins, enforce the standard on top

Many frameworks ship OpenTelemetry, metrics or structured logging, sometimes switched on by
default. Use it as the base and configure it to the standard: exporters, names, exclusions and
resource. Then add what it lacks: nested function spans, profiling labels, error reporting,
enrichment and the standard's metric names. A built-in is a starting point, not an exemption;
every rule in this library still applies. Built-in structured-log formats with their own field
names (ECS, Logstash, GELF) are mapped to the [data model](data-model.md).

## One source per signal

A process has one producer per signal (one tracer provider, one logging pipeline, one metrics
pipeline, one profiler), all set up in the service's observability module ([layout](layout.md)).
Never run a second instrumentation of a signal next to the first. A vendor agent beside the
OpenTelemetry SDK, or a framework's built-in tracing beside a separate instrumentation library,
duplicates spans, double counts metrics and splits trace context.

The rule holds across the pipeline. eBPF tracing in the collector covers only workloads without SDK
instrumentation, and what the collector derives (logs from stdout, scraped metrics) carries the
same identity as the SDK's telemetry ([cross-signal identity](data-model.md#cross-signal-identity)).

## Fail open for delivery, fail closed for safety

- **Delivery fails open.** If the exporter, the collector or a backend fails, the application keeps
  serving; telemetry is retried, held in bounded queues and eventually dropped.
- **Safety fails closed.** If sanitising a value fails, the value is omitted, never written raw.
  Session replay starts only when its masking is in place, and AI content is left out when its
  redactor fails.

Telemetry becomes less detailed, never less safe. Safety is a property of the instrumentation code,
not of a filter further down the pipeline ([safe capture](safe-capture.md)).

## Telemetry is never a production dependency

A slow or broken telemetry pipeline must not change how the application serves its users:

- spans and logs leave through batch processors only, so export never runs on the request path;
- export queues are bounded; on overflow they drop and count what they dropped;
- export and shutdown have timeouts, and telemetry is flushed on shutdown within them;
- drops are visible through the SDK's self-metrics or a rate-limited warning;
- a broken observability configuration degrades telemetry with one loud error log instead of
  stopping the application: without a usable file nothing is exported and no metrics port is
  bound, since nothing says which port; validation in CI catches it first;
- no telemetry call sits on the request path: spans and scores leave in the background. A
  prompt fetched from Langfuse is the application's own call, not telemetry; the report lists
  it as a dependency that needs a cache, a bounded timeout and a fallback in code
  ([signals/genai.md](signals/genai.md#modes)).

The conformance checks verify this by injecting failures: collector
down, connection refused, slow collector, TLS failure, backend 429 and 500, a full export queue,
and SIGTERM with telemetry still queued. In every case the application keeps serving with an
unchanged error rate, memory stays bounded, drops are visible and shutdown finishes within its
timeout. Overhead tests, the same load with telemetry on and off against a budget (latency,
CPU, memory, spans and log bytes per request), are a later addition ([acceptance](acceptance.md)).

## Errors are reported through logs

A failure produces **one ERROR log record**, written where it is handled (for a failed request,
usually the global error handler), with `exception.type`, `exception.message`,
`exception.stacktrace` and native trace and span IDs. Backends build error tracking from these
records, so there is no separate error-reporting SDK and no error-reporting section in the
configuration.

- Inner layers only mark their span (status ERROR and `error.type`) and rethrow, so one failure is
  reported once ([functions](functions.md)).
- Process-level hooks for uncaught exceptions, unhandled rejections and panics report through the
  same path.
- 4xx responses are not reported as errors by default; they appear in the access log at WARN.
- Stack traces never appear in responses.

Why logs rather than span events: a log record is kept even when its trace is sampled out, and
OpenTelemetry is deprecating span events in favour of log-based events.

## Existing error responses are preserved

An error response body is part of the API's contract. Instrumentation records failures; it does not
change what clients receive. Keep the existing format, status codes and content type, and move to
RFC 9457 problem details (`application/problem+json`) only when the service owner opts in.

## Enforce, don't coexist

Existing monitoring (vendor SDKs and agents, OpenTracing or OpenCensus, ad-hoc `/metrics`
endpoints, default access loggers, error handlers, GenAI tracing) is audited against the data model,
and each piece is marked *conforming*, *fix* or *replace*. Pieces that don't conform are brought up
to the standard or replaced. They don't keep running beside it, because two sources of one signal
break the rule above.

Changes that break what consumers rely on, such as renamed metrics, a new log format or a removed
vendor SDK, come with a **migration note** that says what changes and what depends on it
(dashboards, alerts, saved queries). The owner is asked before they are applied.

[Documentation index](README.md)
