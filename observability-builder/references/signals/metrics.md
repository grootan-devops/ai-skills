# Metrics

Operational measurements with short, bounded labels: request rate, errors and duration for the
service and its calls, runtime health, the database pool, failed probes and job runs, plus RPC,
messaging, WebSocket, authentication, MCP, A2A and GenAI traffic in a service that has it. Each stack produces them with its mainstream Prometheus client library and serves
them on `/metrics`, on a port of their own, for Prometheus or the collector to scrape.
OpenTelemetry metric push over OTLP is off by default.

## Required behaviour

1. **Ops metrics only by default.** The module produces the [default set](#the-default-set),
   the contract every stack meets, plus the [conditional metrics](#conditional-metrics) for the
   kinds of traffic the service has, the [GenAI metrics](#genai-metrics) in a service that
   calls a model provider, and the [MCP](#mcp-metrics) and [A2A](#a2a-metrics) metrics. App and business metrics are added by the team when it needs them,
   with the same library and the same rules ([enrichment.md](../enrichment.md)).
2. **The stack's Prometheus client library** produces them: its default process and runtime
   collectors, plus the module's own HTTP, pool, RPC, messaging, WebSocket, authentication, probe
   and job metrics
   ([per stack](#per-stack)).
3. **Pull from `/metrics` on its own port,** never the application port, bound no wider than
   the scraper needs. The instrument step chooses per platform: loopback (`127.0.0.1`) when the
   scraper runs in the same pod, task or instance (a sidecar on ECS or Cloud Run, an agent on a
   VM); `0.0.0.0` only when it scrapes across the network, as in a Kubernetes pod scrape. The
   yml comment on `host` says which. The port is never routed through a load balancer, ingress
   or public service, and a NetworkPolicy or security group restricts it
   ([overhead-and-security.md](../overhead-and-security.md#exposed-surfaces)). On Kubernetes
   the chart exposes the same port as the metrics port ([charts.md](../charts.md)). Pre-fork and multi-process servers use the
   library's multiprocess or cluster mode, so one `/metrics` covers every worker; workers never
   share one port. A taken metrics port fails loudly at start: in Python the module first binds
   without `SO_REUSEADDR` (a WSGI server subclass with `allow_reuse_address = False`), because
   `prometheus_client.start_http_server` sets it and on macOS a `127.0.0.1` bind then succeeds
   beside a held `0.0.0.0` port. If that bind fails with "address in use", it checks for a real
   listener (a connect to the port) and retries with reuse only when none answers: right after a
   restart, connections in `TIME_WAIT` from the last scrape also block a no-reuse bind
   ([sdk-notes](../sdk-notes.md#python)). Node's
   `http.createServer` and Go's listeners already fail.
4. **OTLP push is off by default** (`metrics.otlp.enabled: false`). A team may switch it on for
   its own app metrics made with the OpenTelemetry API. The ops metrics stay on `/metrics`, and
   a series leaves by one path only: the instrumentations' own metrics (such as
   `http.server.request.duration` from the HTTP instrumentation) are turned off or dropped with
   a view, so they never repeat the ops metrics.
5. **Short label names, bounded values.** The labels are `method`, `route`, `status_code`,
   `service`, `pool`, `probe`, `dependency`, `job_name` and `outcome` (never `job`, which the
   scrape sets: [below](#the-default-set)), plus `system`, `destination`, `operation` and `type`
   on the messaging and WebSocket metrics, `result` and `provider` on the authentication counter,
   `provider`, `model`, `operation` and `token_type` on the GenAI metrics, `server` and `target`
   on the MCP metrics and `agent` on the A2A metrics. `method` is a standard HTTP method, or
   `_OTHER` (the RPC or JSON-RPC method on RPC, MCP and A2A metrics). `route` is the route
   template (the URL template for client calls), or `unmatched` when nothing matched. `service`
   is the called dependency's name from the code's dependency map. Each metric's required
   labels are M and always set; its optional labels are O
   ([data model](../data-model.md#metrics)). Prohibited as labels (P): user, tenant, org,
   account, customer, session, request, correlation, trace and span ids, raw URLs and paths,
   SQL, error messages, emails, free text and secrets.
6. **One bucket list** for the duration histograms, in seconds:
   `0.005 0.01 0.025 0.05 0.1 0.25 0.5 1 2.5 5 10 30 60`. `job_duration_seconds` adds
   `120 300 600 1800` for jobs that run longer than a minute.
7. **Client metrics measure request work.** Only calls made inside a request, job or WebSocket
   trace are recorded; calls made at start-up or in the background are not. The SDK's own
   export calls are never counted, and an active span alone doesn't rule them out: the
   OpenTelemetry JS SDK runs exports with tracing suppressed, sometimes from a timer that
   inherited a request's async context, so the check also skips calls made while tracing is
   suppressed (`isTracingSuppressed(context.active())` from `@opentelemetry/core`;
   [sdk-notes.md](../sdk-notes.md)).
8. **Probe and docs paths are not measured** ([exclusions](#exclusions)). Failed probes are
   counted in `app_health_probe_failures_total`.
9. **The same names on every stack.** Where a library's built-in metric has other names
   (Spring's `http_server_requests_seconds`, Go's `go_sql_*`), rename it to the contract where
   the library allows it; otherwise keep it and list the mapping in the migration note.

## The default set

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `http_server_request_duration_seconds` | histogram | `method`, `route`, `status_code` | requests served, from arrival to response end |
| `http_server_requests_total` | counter | `method`, `route` | requests served |
| `http_server_active_requests` | gauge | `method` | requests in flight; the route isn't known yet when a request arrives |
| `http_client_request_duration_seconds` | histogram | `service`, `method`, `route`, `status_code` | outbound calls; `status_code` is `error` when no response arrived |
| `http_client_active_requests` | gauge | `service`, `method`, `route` | outbound calls in flight |
| process and runtime metrics | per library | the library's own | the client library's defaults; for Node.js: `process_cpu_seconds_total`, `process_resident_memory_bytes`, `process_heap_bytes`, `process_open_fds`, `process_max_fds`, `process_start_time_seconds`, `nodejs_heap_size_total_bytes`, `nodejs_heap_size_used_bytes`, `nodejs_eventloop_lag_seconds`, `nodejs_gc_duration_seconds`, `nodejs_active_handles`, `nodejs_active_resources`; other stacks [below](#per-stack) |
| `db_pool_connections` | gauge | none | connections open in the pool |
| `db_pool_connections_active` | gauge | none | connections checked out |
| `db_pool_connections_idle` | gauge | none | idle connections |
| `db_pool_connections_waiting` | gauge | none | callers waiting for a connection |
| `db_pool_wait_duration_seconds` | histogram | none | time to get a connection |
| `websocket_connections` | gauge | none | open connections |
| `websocket_connections_total` | counter | none | connections accepted |
| `websocket_disconnections_total` | counter | none | accepted connections that closed |
| `websocket_messages_received_total` | counter | none | messages received, pings included |
| `websocket_messages_sent_total` | counter | none | messages sent |
| `websocket_send_errors_total` | counter | none | sends that failed |
| `websocket_receive_errors_total` | counter | none | received messages that couldn't be handled |
| `app_health_probe_failures_total` | counter | `probe`, `dependency` | failed responses to an excluded path other than a 404; `probe` is the matching `exclude_paths` entry, `dependency` the name the readiness response itself gives the failing check (never one the instrumentation picks), or `unknown` when the response names none ([hooks.md](../hooks.md)) |
| `job_runs_total` | counter | `job_name`, `outcome` | job runs; `outcome` is `success`, `partial` or `failure` |
| `job_duration_seconds` | histogram | `job_name` | job run duration |

A service with several database pools adds a `pool` label with each pool's configured name.
The job metrics label the job `job_name`, never `job`: Prometheus and the collector put `job`
(the scrape target's name) on every scraped series, so an app label `job` arrives renamed to
`exported_job`, or, with `honor_labels`, replaces the target's `job`, which the collector's
OTLP conversion turns into `service.name`.
Several of the Node.js process metrics read `/proc`, so they appear on Linux only.

## Conditional metrics

Added in a service that has the traffic, recorded where each call, message or attempt
completes, so unsampled traffic counts too. Durations use the standard buckets.

| Metric | Type | Unit | Labels | Emitted when |
| --- | --- | --- | --- | --- |
| `rpc_server_duration_seconds` | histogram | s | `method`, `status_code`; optional `service` | the service serves gRPC or another RPC protocol: each call handled, from arrival to the response; `status_code` is the RPC status (gRPC code name) |
| `rpc_client_duration_seconds` | histogram | s | `method`, `status_code`; optional `service` (the called dependency) | the service calls RPC services: each call inside a request, job or WebSocket trace, until the response; `status_code` is `error` when none arrived |
| `messaging_process_duration_seconds` | histogram | s | `system`, `destination`, `operation`; optional `outcome` | the service consumes messages: each message (or batch) processed, from receipt to ack or failure |
| `messaging_messages_total` | counter | messages | `system`, `destination`, `operation`; optional `outcome` | the service produces or consumes messages: each message sent or processed; `operation` is the `messaging.operation.type` value (`send`, `process`) |
| `auth_attempts_total` | counter | attempts | `method`, `result`; optional `provider` | C: the service itself authenticates (a login, a token exchange, a credential check); each attempt, with `method` the `app.auth.method` value, `result` `success`, `denied` or `error`, and `provider` from a fixed list |

- **`system`** is the `messaging.system` value (`kafka`, `rabbitmq`, `aws_sqs`); **`destination`**
  is the topic or queue name from a fixed list in code, a template for generated names, else
  `other`; **`outcome`** is `success`, `failure`, `retried` or `dead_lettered`.
- **A rejected WebSocket handshake** is logged and traced, not counted
  ([WebSocket](../workloads/api.md#websocket)).
- **Authentication is counted once,** where the service decides the result; a service that only
  checks a token another service issued counts nothing here.

## Per stack

What each stack's library provides, what the contract's names map to, and what has no
equivalent. Check the versions you install; the table records what was found.

| Stack | Library and default runtime metrics | HTTP server and client metrics | Pool metrics | Mapping and gaps |
| --- | --- | --- | --- | --- |
| Node.js | `@prometheus-io/client`, the renamed `prom-client` (deprecated under its old name). `collectDefaultMetrics()` gives the `process_*` and `nodejs_*` series above | None built in. The access-log middleware records the server metrics; Node's diagnostics channels (`http.client.request.*` for `node:http`, `undici:request:*` for `fetch`) record the client ones | `pg` `Pool`: `totalCount`, `idleCount` and `waitingCount`, read at scrape time; active is total minus idle | `pg` has no wait time: time `connect()`, which `pool.query()` also uses ([sdk-notes.md](../sdk-notes.md)) |
| Python | `prometheus_client`. Its default collectors: process (`process_*`, read from `/proc`, so Linux only), GC (`python_gc_*` counters) and platform (`python_info`) | None built in: an ASGI or WSGI middleware with the short labels; client calls through the client wrappers | SQLAlchemy pool: `size()`, `checkedin()` (idle), `checkedout()` (active), `overflow()`. psycopg_pool `get_stats()`: `pool_size`, `pool_available` (idle), `requests_waiting` | Wait time: time the checkout (psycopg_pool's `requests_wait_ms` is a running total); SQLAlchemy has no waiting count. Pre-fork servers: multiprocess mode serves one `/metrics` for all workers, without the per-process collectors |
| Go | `client_golang`. `collectors.NewGoCollector()` and `NewProcessCollector()`: `go_goroutines`, `go_threads`, `go_gc_duration_seconds` (a summary), `go_memstats_*`, `process_*` | An `http.Handler` middleware with the short labels (promhttp's instrumenting handlers label `code` and `method`, not the contract's labels); a `RoundTripper` wrapper for client calls | `collectors.NewDBStatsCollector(db, name)` for `database/sql`: `go_sql_open_connections`, `go_sql_in_use_connections`, `go_sql_idle_connections`, labelled `db_name` | Map the `go_sql_*` names in a small collector over `db.Stats()`, or keep them and list the mapping. Waits are running totals (`go_sql_wait_count_total`, `go_sql_wait_duration_seconds_total`): no waiting gauge and no wait histogram |
| JVM: Spring Boot (Quarkus and Ktor similar) | Micrometer with the Prometheus registry; the `jvm_*`, `process_*` and `system_*` binders | Spring's `http_server_requests_seconds` (`method`, `uri`, `status`, `outcome`, `exception`) and `http_client_requests_seconds` (plus `client_name`), renamed to the contract with `MeterFilter` renames, the extra tags dropped; buckets from `management.metrics.distribution.slo` | HikariCP, labelled `pool`: `hikaricp_connections`, `hikaricp_connections_active`, `hikaricp_connections_idle`, `hikaricp_connections_pending` (waiting), `hikaricp_connections_acquire_seconds` (wait) | The timers give no separate request counter and no in-flight gauges with the contract's labels: add `http_server_requests_total` and the active-request gauges in the module's filter. Map the HikariCP names or list the mapping |
| Rust | The `prometheus` crate with its `process` feature (Linux only), or `metrics` with `metrics-exporter-prometheus` and `metrics-process` | A tower or axum middleware with the short labels (`MatchedPath` gives the route); client calls through the client wrappers | sqlx `Pool::size()` and `num_idle()`; deadpool `Pool::status()` | No garbage collector, so no GC metrics. Wait time: time the acquire |

The conditional metrics, probe-failure and job metrics are the module's own on every stack.

## GenAI metrics

A service that calls a model provider adds these, with `genai.enabled` and
`genai.metrics.enabled` on ([genai.md](genai.md)). They are recorded at the model-call hook
(the module's model-call helper, a call-site wrapper, or a framework callback such as
LangChain's `on_llm_end`, an AI SDK middleware or a Spring AI observation handler), not derived
from spans, so unsampled requests count too:

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `gen_ai_client_operation_duration_seconds` | histogram | `provider`, `model`, `operation`, `outcome` | model and embedding calls, from the request to the last chunk; `outcome` is `success`, `error`, `rate_limited` or `timeout` |
| `gen_ai_client_tokens_total` | counter | `provider`, `model`, `token_type` | tokens the provider reported; `token_type` is `input`, `output`, `cache_read`, `cache_creation` or `reasoning` |
| `gen_ai_client_time_to_first_chunk_seconds` | histogram | `provider`, `model` | streamed calls, from the request to the first chunk |

- **Buckets:** the standard list, plus `120` and `300` on the duration histogram for long
  generations and agent turns.
- **Token types overlap:** `cache_read` and `cache_creation` are part of `input`, and `reasoning`
  is part of `output`, as the providers count them. Never add the types together.
- **`model`** is the requested model from a fixed list in code, anything else `other`; a dated
  response model never makes new series. `provider` is the `gen_ai.provider.name` value;
  `operation` the `gen_ai.operation.name` value.
- **No feature, user, team or conversation label.** Cost and quality per feature, user or team
  are questions for the LLM tool, which reads them off each AI span: Langfuse
  ([genai.md](genai.md#who-spent-what)).
- **One path per series.** With OTLP push on, the instrumentations' own `gen_ai.client.*`
  metrics are dropped with a view.
- **App metrics on request:** feedback, guardrail decisions, tool calls, agent steps, approvals
  and classifications, with the [rules below](#app-metrics). The approval wait histogram,
  `app_genai_approval_wait_seconds`, uses buckets from `60` seconds to one day (`86400`), a
  documented exception for waits on people ([ai-agent.md](../workloads/ai-agent.md#human-in-the-loop-approval)).

## MCP metrics

An MCP server or client adds one duration histogram per side, recorded where each message
completes, so unsampled traffic counts too ([mcp.md](../workloads/mcp.md#metrics)):

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `mcp_server_operation_duration_seconds` | histogram | `method`, `target`, `outcome` | each request or notification the server handles |
| `mcp_client_operation_duration_seconds` | histogram | `server`, `method`, `target`, `outcome` | each request the client sends, until its response |

- **`target`** is a tool or prompt name from the server's registered list, `other` beyond it,
  and empty for other methods; never a resource URI or an argument.
- **`outcome`** is `success`, `error`, `rejected` (the caller's JSON-RPC errors) or `timeout`
  (client only); **`server`** is the configured server name, from a fixed list.
- **Buckets:** the standard list, plus `120` and `300` for long tool calls.

## A2A metrics

An agent that calls or serves the agent-to-agent protocol adds one duration histogram per side,
recorded where each JSON-RPC call completes (for `message/stream`, when the stream ends):

| Metric | Type | Unit | Labels | Emitted when |
| --- | --- | --- | --- | --- |
| `a2a_client_request_duration_seconds` | histogram | s | `agent`, `method`, `outcome` | the service calls a remote agent: each call until its response or the end of its stream |
| `a2a_server_request_duration_seconds` | histogram | s | `agent`, `method`, `outcome` | the service is an A2A agent: each call it handles |

- **`agent`** is the remote agent's card name on the client and the service's own agent name on
  the server, from a fixed list; **`method`** is the A2A method (`message/send`,
  `message/stream`, `tasks/get`, `tasks/cancel`); **`outcome`** is `success`, `error`,
  `rejected` (the caller's JSON-RPC errors) or `timeout`. Never a task, context or message id
  ([data model](../data-model.md#a2a-agent-to-agent-protocol)).
- **Buckets:** the standard list, plus `120` and `300` for long agent turns.

## App metrics

Added only when the team asks for them (a metric around a function, a business count), with the
same library, in the module's registry, so they appear on the same `/metrics`:

- **Names** `app_<what>_<unit>`: base units (`_seconds`, `_bytes`), `_total` on counters, for
  example `app_ocr_requests_in_flight`, `app_export_rows`, `app_notifications_total`.
- **Types:** a counter for events; a gauge for a value right now, either in flight around a
  function (incremented on entry and decremented in `finally`, so a failure can't leave it
  raised) or read at scrape time in the gauge's `collect` callback; a histogram for durations
  (the standard buckets above) and sizes (explicit buckets that fit the values).
- **Labels:** short names, each with a fixed list of values decided in code (a kind, a channel,
  an outcome). A value outside the list is recorded as `other`. Never ids, user input, phone
  numbers, raw paths or messages: the function's span carries the per-call detail as attributes.
- **Where:** in the function's body or its wrapper, never in a second registry or metrics port.
  With `metrics.otlp` on, the same metric is an OpenTelemetry instrument whose Prometheus
  translation gives the same name.
- **Tests:** assert the series in the registry's text output, labels included.

The enrichment catalogue lists companion metrics for common functions
([enrichment.md](../enrichment.md)).

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `metrics.enabled` | `true` | `false` turns off `/metrics` and OTLP push |
| `metrics.prometheus.enabled` | `true` | the `/metrics` server |
| `metrics.prometheus.host` | `0.0.0.0` | listen address; keep `0.0.0.0` only where the scraper is remote (a Kubernetes pod scrape), use `127.0.0.1` for a same-pod, same-task or same-instance scraper, and say which in a yml comment |
| `metrics.prometheus.port` | `9464` | never the application port |
| `metrics.prometheus.path` | `/metrics` | |
| `metrics.prometheus.exclude_paths` | `[/health, /ready, /live, /metrics, /status]` | add the API docs paths; exact paths, or `/path/*` for a subtree |
| `metrics.otlp.enabled` | `false` | OTLP push of the team's own app metrics |
| `metrics.otlp.interval` | `60s` | `OTEL_METRIC_EXPORT_INTERVAL` |
| `metrics.otlp.temporality` | `cumulative` | `delta` for a delta-only backend in direct mode |
| `metrics.otlp.exporter.*` | `grpc`, `gzip`, `10s` | the OTLP exporter block ([config.md](../config.md)) |

## Exclusions

A successful response (status 200–399) to a path in `metrics.prometheus.exclude_paths` is not
measured: the probes, `/metrics` and the service's API docs paths (for example `/api-docs`
and `/api-docs/*`). An entry matches the exact path, query string ignored, and an entry ending
in `/*` matches every path below it, so `/health` is excluded while `/api/healthcare` is
measured. A failed response to an excluded path increments
`app_health_probe_failures_total{probe,dependency}` in place of the HTTP server metrics, so probe
traffic never skews the request rate or the error ratio ([hooks.md](../hooks.md)). A 404 on an
excluded path is an ordinary request, measured like any other: the path isn't served there.

## Failure behaviour

- **Never on the request path.** The client library records in memory; a scrape reads the
  values separately and never blocks a request.
- **Port conflicts don't stop the service.** If the metrics port can't be bound, the module logs
  an ERROR and the service keeps serving without `/metrics`.
- **No configuration, no port.** When `observability.yml` is missing, unreadable or not valid
  YAML, or its `metrics` section is invalid, no `/metrics` server starts and no port is bound:
  there is no configuration to say which port, and binding a default port can collide with
  another process. One ERROR names the file, the key and the reason; without a usable file
  nothing is exported, and logs still reach stdout with the defaults
  ([config.md](../config.md#validation-and-failure-behaviour)).
- **Bounded by construction.** Most Prometheus client libraries have no cardinality limit, so
  label values come only from fixed sets, route templates and the dependency map. OpenTelemetry
  instruments pushed over OTLP also get the SDK's cardinality limit, set in code.
- **Push fails open.** When OTLP push is on, export retries within bounds and times out; failed
  batches are dropped and counted.

## Verification

[acceptance.md](../acceptance.md) scrapes `/metrics` on the declared port and checks the
contract's names, types, labels and buckets, the default runtime metrics, no id or raw path in
a label value, client metrics only for calls inside a trace and never for the SDK's own
exports, successful probes and docs pages absent while `/api/healthcare` is measured, the
failure counter on a 503 from `/ready`, the conditional metrics for the traffic the service has,
and, in a service that calls a model provider, the GenAI metrics with their bounded labels.

[Documentation index](../README.md)
