# Observability module layout

Where observability code lives inside a project. Every service gets one module in a
predictable place, so developers know where to look and the audit can check it. The trees
below are conventions, not templates: names follow each language's idioms, and only the
files a project needs are created.

## Rules for every language

1. **One module per service** at a fixed path. It is the only place that sets up SDKs,
   exporters, loggers, metrics servers and profilers.
2. **One entrypoint** (`init`, `setup` or `register`) runs before the framework loads — after
   the fork for pre-fork servers — and a `shutdown` flushes telemetry on exit.
3. **Fixed file responsibilities:** config (`observability.yml` → standard `OTEL_*`
   settings, the platform detection and the filtered resource detectors:
   [config.md](config.md#resource-per-platform)), tracing (with the function wrapper and the
   allow-listed baggage propagator), the shared masking helper (the one way personal data
   reaches telemetry: [data-model.md](data-model.md#pii-masking)), logging (with the access log), metrics (the
   ops metrics and the `/metrics` server), profiling (with the controller-label helper), jobs
   (the job-run helper, when the service runs jobs), errors, genai (when the service calls a
   model: [below](#per-workload)), and `adapters/` holding only the frameworks the project
   actually uses.
4. **Business code** uses only the OpenTelemetry API and the module's helpers: the span
   wrapper or decorator, the controller-label helper, the masking helper and the logger.
5. **`observability.yml`** sits at the repository root for local runs and is mounted at
   `/etc/observability/observability.yml` in Kubernetes; `OBSERVABILITY_CONFIG` points at it.
6. **The audit flags** SDK or exporter setup outside the module (Langfuse clients and GenAI
   instrumentations included), a missing entrypoint call, more than one observability module,
   and `OTEL_*`, `LANGFUSE_*`, `TRACELOOP_*` or `OPENINFERENCE_*` handling outside the config
   loader.

## Per language

### Go

```text
cmd/<service>/main.go                # observability.Init() first, defer Shutdown()
internal/observability/
├── observability.go                 # Init / Shutdown
├── config.go                        # yml → OTEL_* settings
├── tracing.go                       # provider, sampler, span wrapper helper
├── logging.go                       # slog handler (json | plain) + OTel bridge
├── accesslog.go                     # access-log middleware
├── metrics.go                       # client_golang ops metrics, /metrics on its own port
├── jobs.go                          # job-run helper: root span, job log, job metrics
├── profiling.go                     # Pyroscope start, controller-label helper
├── errors.go                        # panic recovery, error recording
├── genai.go                         # features, stamping, content helper, ops guard, Langfuse exporter
└── adapters/                        # http.go | gin.go | echo.go | fiber.go | grpc.go
```

### Java (Spring Boot)

```text
src/main/java/<base>/observability/
├── ObservabilityEnvironmentPostProcessor.java   # yml → OTEL_* before the context starts
├── TracingConfiguration.java
├── Traced.java (+ aspect)                       # function spans
├── RequestAttributesFilter.java                 # request attributes on the entry span
├── AccessLogConfiguration.java
├── MetricsConfiguration.java                    # contract metric names, /metrics port, exclusions
├── JobRuns.java                                 # job-run helper: root span, job log, job metrics
├── ProfilingConfiguration.java                  # Pyroscope, controller labels
├── ErrorObservationAdvice.java                  # records errors, keeps the response format
└── genai/                                       # features, stamping, content, ops guard, Langfuse exporter
src/main/resources/logback-spring.xml            # json | plain + OTel appender
src/main/resources/META-INF/spring.factories     # registers the post-processor
```

### Python

```text
<package>/observability/
├── __init__.py                      # setup() / shutdown(); before the framework import or in post_fork
├── config.py                        # yml → OTEL_* settings
├── tracing.py                       # provider, @traced decorator
├── logs.py                          # json | plain + OTel handler; never logging.py (below)
├── access_log.py                    # ASGI / WSGI middleware
├── metrics.py                       # prometheus_client ops metrics, /metrics on its own port
├── jobs.py                          # job-run helper: root span, job log, job metrics
├── profiling.py                     # Pyroscope, controller-label context manager
├── errors.py
├── genai.py                         # features, stamping processor, content helper, ops guard, metrics hook
├── langfuse.py                      # Langfuse per mode (SDK processor or OTLP exporter), the client, scores
└── adapters/                        # fastapi.py | django.py | flask.py | celery.py
gunicorn.conf.py                     # post_fork → observability.setup()
```

Name no module file after a standard-library module. In Python a `logging.py` in the package
shadows the standard `logging` wherever the package folder is on `sys.path` (a script run from
inside it, a test runner that inserts its rootdir), and the SDK's own `import logging` then
loads the module's file. So the logging file is `logs.py`, and the same goes for `profile.py`,
`trace.py`, `types.py` and `json.py`. Go, Java and Node.js resolve imports by package path or
explicit relative path, so their file names don't shadow the standard library.

### Node.js (TypeScript)

```text
src/observability/
├── register.ts                      # preloaded: node --import ./dist/observability/register.js
├── config.ts                        # yml → OTEL_* settings
├── tracing.ts                       # provider, traced() wrapper / decorator
├── logger.ts                        # the project's logger set to the standard, or a small module logger (sdk-notes)
├── access-log.ts
├── metrics.ts                       # @prometheus-io/client ops metrics, /metrics on its own port
├── jobs.ts                          # job-run helper: root span, job log, job metrics
├── profiling.ts                     # Pyroscope, controller-label helper
├── errors.ts
├── genai.ts                         # features, stamping processor, content helper, ops guard, metrics hook
├── langfuse.ts                      # Langfuse per mode (SDK processor or OTLP exporter), the client, scores
└── adapters/                        # express.ts | fastify.ts | nestjs/ (module, interceptor, filter)
```

### Next.js 16

Instrumentation files go under `src/` when the project uses a `src/` folder.

```text
instrumentation.ts                   # register(): Node-runtime server setup; onRequestError
instrumentation-client.ts            # browser RUM: readRumConfig() + startRum()
src/observability/
├── server/                          # config.ts tracing.ts access-log-processor.ts logger.ts metrics.ts profiling.ts
├── rum/                             # config.ts init.ts scrub.ts (assets/js/faro), client-only
└── shared/                          # attributes.ts sanitize-url.ts
```

### Single-page app (browser RUM)

```text
src/observability/rum/               # config.ts init.ts scrub.ts react.tsx (assets/js/faro)
public/observability.json            # RUM off by default; the deployment mounts the real one
```

## Per workload

Each workload adds to the language module above.

| Workload | Addition |
| --- | --- |
| API backend | `adapters/<framework>` only |
| WebSocket endpoints | `adapters/websocket` — connection and message root spans, lifecycle records, `websocket_*` metrics |
| Async workers and messaging | `adapters/consumer` — context from message headers, per-message job log, span links, the messaging metrics |
| BFF / integration service | `adapters/clients` — outbound client setup, propagation allow-list, retry and timeout attributes |
| Gateway (APISIX, Envoy Gateway, Traefik, Kong) | no application code; plugin and values snippets in the gateway's configuration repository |
| MCP server or client | `adapters/mcp` — `params._meta` trace propagation, `SERVER` and `CLIENT` spans per message (none where the SDK traces MCP itself), the MCP duration metrics, stdio logs to stderr ([mcp](workloads/mcp.md)) |
| A2A agent or client | `adapters/a2a` — the `invoke_agent` CLIENT span per remote agent call, the SERVER span per A2A method, `traceparent` on the HTTP call, the A2A duration metrics ([a2a](workloads/a2a.md)) |
| AI features and agents | the genai files: the feature registry and `genai.feature()`, the stamping processor, the content helper and redactors, the ops guard, the GenAI metrics hook, the GenAI instrumentations, and, when Langfuse is on, its processor or exporter, the one Langfuse client app code takes for its prompts and datasets, and the score client |
| Pages (single-page app, fullstack page side) | `rum/` — Faro start-up from the runtime config, the `beforeSend` scrubber, the router and error hooks ([rum](signals/rum.md), [spa](workloads/spa.md)) |
| Batch job, cron job, scheduled job, CLI | `job` helper — `job {name}` root span, job log, job metrics, flush before a process exits |

[Documentation index](README.md)
