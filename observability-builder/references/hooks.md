# Hook points

Where the observability module attaches to a framework's request pipeline, and what it does
there. The contract is framework-independent: each adapter finds the framework's native
middleware, interceptor or filter API in its official docs and meets every rule below.
Server-side request hooks live in the framework's native chain; client, database and messaging
calls may use official auto-instrumentation.

## Request lifecycle

```text
request
  │
  ▼
observability middleware (outermost)
  ├─ extract trace context and allow-listed baggage
  ├─ accept or replace the correlation id; bind ids to the logger context
  ├─ start the entry span (every path: exclusions are decided at response end, rule 12)
  ├─ tee the request body (only when enabled, capped, stream untouched)
  ▼
framework: routing ─► auth ─► handler (nested function spans) ─► global error handler
           (no spans for the framework's middleware, router or handler layers: rule 6)
           an AI handler declares its feature first: genai.feature(name, user, conversation)
  │
  ▼
response end (response complete or connection closed)
  ├─ http.route: the template captured when the route matched, or unmatched
  ├─ request attributes from the verified principal → entry span and access log
  ├─ span status (UNSET unless the request failed) and error.type
  ├─ HTTP server metrics
  └─ access-log record, sampled or not; for an excluded path nothing on success,
     health.probe.failed and the failure counter on failure
```

## Contract

1. **Outermost.** The middleware wraps everything else (auth, rate limiting, CORS, body
   parsing, the framework's own middleware), so it sees and times every request, including
   those rejected before the handler.
2. **Trace context.** Extract W3C `traceparent` and `tracestate` and the allow-listed baggage
   keys. At public edges the incoming sampled flag is not trusted
   ([signals/traces.md](signals/traces.md)).
3. **Correlation id.** Take `x-correlation-id` when it is valid: 1 to 128 characters from
   `A-Z`, `a-z`, `0-9`, `.`, `_`, `:` and `-`. Otherwise replace it with a generated random id
   and log a WARN with the reason, never the rejected value. A missing header also gets a
   generated id. A request is never rejected because of its correlation id.
4. **Logger context.** Bind the trace id, span id and `correlation.id` to the logger context,
   so every record written during the request carries them, the ids as native fields
   ([signals/app-logs.md](signals/app-logs.md)).
5. **Route.** Take the route template at the moment the framework matches the route, and set
   `http.route` to it (`/orders/{id}`), or to `unmatched` when nothing matched; never the raw
   path. A template computed later from request fields can lose its prefix: in Express,
   `req.baseUrl` holds the router's mount path only while the request is inside that router, so
   a template built when an error handler responds reads `/:id` instead of `/api/orders/:id`
   ([sdk-notes.md](sdk-notes.md)). The span name becomes `{method} {http.route}`, so
   `{method} unmatched` for a 404. The span, the HTTP server metrics and the access log carry
   the same route. `url.path` keeps the raw path, on the span, in the access log and in
   error-log context, except that a segment holding an email becomes `{email}`; ids stay
   visible ([safe-capture.md](safe-capture.md)).
6. **No framework middleware spans.** Configure the framework instrumentation to skip its
   middleware, router and handler layers (Express: `ignoreLayersType` with `middleware`,
   `router` and `request_handler`), which keeps `http.route` on the `SERVER` span. The module's
   wrappers span the handlers ([functions.md](functions.md)). With every layer skipped, the
   Express instrumentation contributes only `http.route`: the mount path for a request that
   falls through a router mounted on a prefix, and nothing for one rejected before routing. A
   project may therefore leave that instrumentation out and take the route itself (rule 5).
   Either way, the 404 handler sets `unmatched`, so no request keeps a mount path as its route
   ([sdk-notes.md](sdk-notes.md)).
7. **Request attributes.** User, tenant, session and client app and version are set in code
   from the auth layer's verified principal, read at response end: auth runs inside the
   outermost middleware, so its result exists only then. Unverified tokens are never parsed.
   The attributes go on the entry span and the access log only ([enrichment.md](enrichment.md)).
8. **Client address.** `client.address` comes from the framework's trusted-proxy resolver,
   configured with the real proxy addresses (e.g. Express `trust proxy`, Gin
   `SetTrustedProxies`, Spring's forwarded-header support); without a resolver it is the socket
   peer. The raw `X-Forwarded-For`, which any client can spoof, is never used or logged.
9. **Bodies.** Tee request and response bodies only when `logging.access_log.*.body` enables
   it, capped at `max_bytes`, without consuming the stream: the handler still reads the whole
   body and responses still stream. Body content goes into the access log only, never into
   span attributes ([signals/access-log.md](signals/access-log.md)).
10. **Response end.** When the response completes or the connection closes, set the span status
    and `error.type`, record the HTTP server metrics and emit the access-log record. The status
    stays UNSET unless the response is 5xx or an exception escaped, and is never set to OK. The
    access log doesn't depend on trace sampling: unsampled requests are logged too, and the
    record's trace flags show whether the trace was `sampled`.
11. **One global error handler** turns exceptions into responses, writes the one ERROR record
    per failure and keeps the existing response format ([signals/errors.md](signals/errors.md)).
12. **Exclusions.** `exclude_paths` suppress **successful** responses (status 200–399) only.
    Each signal has its own list (`tracing.exclude_paths`, `metrics.prometheus.exclude_paths`,
    `logging.access_log.exclude_paths`). An entry matches the exact path, query string ignored;
    an entry ending in `/*` matches every path below it, so `/api-docs/*` covers the docs
    assets and `/api-docs` is listed as well for the page itself. The API docs paths (Swagger UI,
    the OpenAPI document) are listed like the probes. A failed probe, such as `/ready`
    answering 503:
    - emits a `health.probe.failed` record (WARN, or ERROR on 5xx) with the access-log fields,
      the probe and the dependency, in place of its access-log record, even when the access log
      is turned off;
    - increments `app_health_probe_failures_total{probe,dependency}` in place of the HTTP
      server metrics;
    - keeps its trace only when `tracing.trace_failed_probes` is `true`.

    `probe` is the matching `exclude_paths` entry (the entry itself for a `/*` subtree, never
    the raw path). `dependency` is the name the readiness response itself gives the failing
    check, for example `upstream` in `{"checks": {"upstream": {"status": "unhealthy"}}}`, or
    `unknown` when the response names none. It is never a name the instrumentation picks, such
    as the client span's peer name.

    A 404 on an excluded path is not a failed probe. It means the path isn't served there,
    for example `/metrics` asked on the app port when it has its own port. It is an ordinary
    request, with its access-log record, metrics and span, and the route `unmatched`.
13. **Shutdown.** On SIGTERM or a normal exit, once the server stops accepting requests and
    in-flight ones finish, `shutdown` flushes every provider within a bounded timeout that fits
    the platform's termination grace period. That includes the profiler's last partial
    interval: some profilers drop it on stop, and a process that lives shorter than one upload
    interval then sends no profile at all ([sdk-notes.md](sdk-notes.md)). With Langfuse on,
    the tracer provider is flushed first (its Langfuse processor included), then the Langfuse
    client's pending scores, all within the same timeout.
14. **Initialisation.** `init` runs before the framework and any instrumented library load,
    through a preload flag, an agent or the first line of `main`. Pre-fork and multi-process
    servers create their providers in each worker after the fork ([layout.md](layout.md)).

## Other entry points

The same contract applies, with each entry point's own semantic conventions
([data-model.md](data-model.md)), to gRPC server interceptors, message consumers (a
per-message job log takes the access log's place), WebSocket connections and messages (root
`SERVER` spans; connected and disconnected records take the access log's place), scheduled jobs
and CLIs (a `job {name}` root span per run, flushed before exit) and MCP tool calls. An AI
entry point (a route, a WebSocket message type, a job item) declares its feature once, after
authentication and before the first model call, so every span after it carries the AI trace
context ([genai.md](signals/genai.md)). The workload pages cover each type:
[API backend and WebSocket](workloads/api.md), [jobs and CLIs](workloads/batch.md),
[AI features and agents](workloads/ai-agent.md).

## Generic pitfalls

These are rules, not recipes; the framework's own docs say how to apply them.

- **Built-in telemetry.** The framework may already ship OpenTelemetry, metrics or logging,
  sometimes on by default (e.g. recent FastAPI releases, the Spring Boot OpenTelemetry
  starter, Quarkus, Litestar, go-zero). Use it as the base and configure it to the standard
  (exporters, names, exclusions, resource), then add what it lacks: nested function spans,
  function-wrapper profiling, error reporting, enrichment, metric naming. Never run a second
  instrumentation of the same signal next to it.
- **Route templates.** Assert that `http.route` is a template, on failed requests too; some
  router and wrapper combinations lose it, or lose its prefix when it is read after the route
  ran (rule 5).
- **Entry span.** A framework instrumentation left at its defaults runs each middleware inside
  a span of its own, so the active span inside the observability middleware is not the entry
  span, and that middleware span may have ended by response end. Even with those spans off,
  take the entry span from the server instrumentation's request hook (or the framework's API)
  and set request attributes there.
- **Exclusion matchers.** Translate `exclude_paths`, exact entries and `/*` subtrees, into each
  library's matcher semantics (exact, prefix, regex or unanchored) per signal. Where a tool
  can't filter by path in code (e.g. the Java agent), use its declarative-config rule-based
  sampler, with a collector `filter` as the fallback. Verify that `/health` is excluded **and**
  `/api/healthcare` is kept.
- **Non-standard HTTP stacks** (fasthttp, Bun) need framework-specific instrumentation; eBPF
  and compile-time zero-code tools don't see them.
- **Loader order** matters (ESM hooks, `--import`, `-javaagent`). Pre-fork and multi-process
  servers initialise after the fork and use the metrics library's multiprocess or cluster
  mode, never one metrics port shared by workers.
- **Root spans from start-up.** Client, database and cache instrumentations start a new trace for
  every call made outside a request: start-up queries, cache warm-ups, background refreshes.
  Switch on their "require parent span" option so only the work of a request, a job run or a
  WebSocket message is traced, and record the client metrics only for calls inside such a trace,
  never for the SDK's own export calls, even when an export runs in a request's inherited
  context ([metrics](signals/metrics.md)). A scheduler starts a root span for each run itself
  ([batch](workloads/batch.md)).
- **Off means off.** Some instrumentations patch their libraries as soon as they are created,
  so with `OTEL_SDK_DISABLED=true` the module creates neither the SDK nor the instrumentations
  nor the profiler; otherwise "off" still pays for patching and hands out empty spans.
- **Explicit settings.** Set the OTLP protocol and endpoint and the semconv stability opt-ins
  explicitly, and bind listeners for containers (some default to localhost).
- **Framework structured-logging formats** (ECS, logstash, GELF) use other field names; map
  them to the data model ([data-model.md](data-model.md)).
- **Upstream sampling.** Parent-based sampling trusts upstream decisions, so at public edges
  (gateway, BFF, public APIs) don't trust incoming sampling flags; internal hops keep
  parent-based sampling.
- **Backend capabilities** come from the backend's docs: accepted protocols (e.g. Dynatrace
  takes `http/protobuf` only), metric temporality (Dynatrace and New Relic want delta), auth
  style, attribute limits and profile formats (e.g. OneUptime can't ingest JFR).
  OpenTelemetry is deprecating span events, so prefer log-based exceptions.

[Documentation index](README.md)
