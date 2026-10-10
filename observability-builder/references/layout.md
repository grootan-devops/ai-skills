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
   settings), tracing (with the function wrapper), logging (with the access log), metrics (the
   ops metrics and the `/metrics` server), profiling (with the controller-label helper), jobs
   (the job-run helper, when the service runs jobs), errors, genai (when the service calls a
   model: [below](#per-workload)), and `adapters/` holding only the frameworks the project
   actually uses. Frontends add RUM; fullstack apps add the
   same-origin telemetry proxy routes.
4. **Business code** uses only the OpenTelemetry API and the module's helpers: the span
   wrapper or decorator, the controller-label helper and the logger.
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
├── logging.py                       # json | plain + OTel handler
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
instrumentation-client.ts            # RUM bootstrap before hydration
src/observability/
├── server/                          # config.ts tracing.ts access-log-processor.ts logger.ts metrics.ts profiling.ts
├── client/                          # rum.ts web-vitals.ts route-spans.ts errors.ts replay.ts
└── shared/                          # attributes.ts sanitize-url.ts
app/telemetry/v1/[signal]/route.ts               # same-origin OTLP proxy
app/telemetry/session-replay/[version]/[artifact]/route.ts   # session-replay proxy
app/error.tsx, app/global-error.tsx              # report through client/errors.ts
```

### Single-page app (React, Vue, Angular with Vite) served by nginx

```text
src/observability/                   # rum.ts web-vitals.ts route-spans.ts errors.ts replay.ts config.ts
public/observability.json            # runtime RUM config, mounted per environment by the chart
nginx default.conf                   # JSON access log with $http_traceparent; /telemetry/ → collector (chart file mount)
```

## Per workload

Each workload adds to the language module above.

| Workload | Addition |
| --- | --- |
| API backend | `adapters/<framework>` only |
| WebSocket endpoints | `adapters/websocket` — connection and message root spans, lifecycle records, `websocket_*` metrics |
| Async workers and messaging | `adapters/consumer` — context from message headers, per-message job log, span links, lag and job metrics |
| BFF / integration service | `adapters/clients` — outbound client setup, propagation allow-list, retry and timeout attributes |
| Gateway (APISIX, Envoy Gateway, Traefik, Kong) | no application code; plugin and values snippets in the gateway's configuration repository |
| MCP server | `adapters/mcp` — `params._meta` trace propagation, tool spans, stdio logs to stderr |
| AI features and agents | the genai files: the feature registry and `genai.feature()`, the stamping processor, the content helper and redactors, the ops guard, the GenAI metrics hook, the GenAI instrumentations, and, when Langfuse is on, its processor or exporter, the one Langfuse client app code takes for its prompts and datasets, and the score client |
| Batch job, cron job, scheduled job, CLI | `job` helper — `job {name}` root span, job log, job metrics, flush before a process exits |

[Documentation index](README.md)
