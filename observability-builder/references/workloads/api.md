# API backend

REST, GraphQL, gRPC and WebSocket services that answer requests. The API backend is the
reference workload: it implements the full hook-point contract and every server-side signal.
Other server workloads are described by how they differ from it — the WebSocket, BFF and
async-worker notes on this page, and [batch jobs, cron jobs and CLIs](batch.md).

## Module layout

An API adds only `adapters/<framework>` to the language module described in
[layout.md](../layout.md). The adapter registers the framework's middleware, interceptor or
filter and its error hook; SDK setup, exporters, the logger, metrics and profiling stay in the
module, and business code uses only the OpenTelemetry API and the module's helpers.

| Service shape | Adapter additions |
| --- | --- |
| REST or plain HTTP | `adapters/<framework>`: the outermost middleware or filter and the global error hook |
| GraphQL | the HTTP adapter plus the GraphQL server's execution hooks for operation spans |
| gRPC | `adapters/grpc`: server interceptors or a stats handler, and the health-service exclusion |
| BFF or integration service | `adapters/clients` as well: outbound client setup and the propagation allow-list |
| WebSocket endpoints | `adapters/websocket` as well: connection and message spans, lifecycle records and the `websocket_*` metrics (see [WebSocket](#websocket)) |
| Consumers in the same process | `adapters/consumer` as well (see [async workers](#async-workers-and-messaging)) |

## Hook-point contract

Server-side request hooks go in the framework's native middleware, interceptor or filter chain;
outbound HTTP, database and cache clients may use official auto-instrumentation. The table is
the order a request sees; [hooks.md](../hooks.md) has the full rules.

| # | Where | Rule |
| --- | --- | --- |
| 1 | Process start | `init` runs before the framework loads; pre-fork and multi-process servers create providers after the fork, in each worker |
| 2 | Outermost hook | extract W3C `traceparent`, `tracestate` and baggage and start the SERVER span; at a public edge, don't trust the incoming sampling flag |
| 3 | Request start | accept `x-correlation-id` when valid (length and charset capped), otherwise generate one and log the replacement — never reject the request; bind trace, span and correlation IDs to the logger context |
| 4 | Request start | `client.address` from the framework's trusted-proxy resolver, never the raw, spoofable `X-Forwarded-For` |
| 5 | Request start | tee bodies only when the access log enables them: capped, without consuming the stream, into logs only |
| 6 | After routing | `http.route` is the route **template** (`unmatched` when nothing matched); the span is named `{method} {http.route}`; the framework's middleware, router and handler layers get no spans |
| 7 | After authentication | request attributes come from the auth layer's verified principal and are read at response end — never from parsing an unverified token |
| 8 | Handler | function spans, enrichment and step logs ([functions.md](../functions.md), [enrichment.md](../enrichment.md)) |
| 9 | Error | one global error handler records the failure once and keeps the existing error response format |
| 10 | Response end | the access log (independent of trace sampling; it records `sampled`), the HTTP server metrics and the span status (UNSET unless the request failed, never OK) |
| 11 | Response end | `exclude_paths` (probes and API docs paths) suppress successful responses only; failed probes are reported ([health probes](#health-probes)) |
| 12 | Shutdown | flush every provider on SIGTERM and before any explicit exit, bounded by the exporter timeout |

Pitfalls the contract guards against:

- A framework that already ships OpenTelemetry, metrics or logging is the base: configure it to
  the standard (exporters, names, exclusions, resource) and add what it lacks. Never run a
  second instrumentation of the same signal next to it.
- Some router and wrapper combinations lose the route template, or its prefix when it is read
  after the route ran — take it when the framework matches the route, and test that
  `http.route` is a template, not a path, on failed requests too ([hooks.md](../hooks.md)).
- Instrumentation libraries match excluded paths differently (exact, prefix, regex, unanchored
  search), and their own exclusion lists act when the span starts, before the status is known,
  so a 404 or a failed probe on a listed path would lose its span too. Leave those lists empty
  and decide when the response ends: the module drops a successful excluded request's spans in
  its export filter ([hooks.md](../hooks.md), rule 12). Where a tool can't filter in code (an
  agent, for example), use its rule-based sampler, with a collector filter as fallback.
- Preload order matters: ESM loader hooks, `--import` and `-javaagent` run before any
  application code.
- In containers, bind listeners — `/metrics` included — on all interfaces; some default to
  localhost.
- Non-standard HTTP stacks need framework-specific instrumentation; eBPF and compile-time tools
  don't see them.
- Framework "structured logging" formats (ECS, logstash, GELF) use other field names — map them
  to the data model ([data-model.md](../data-model.md)).

## Signals

| Signal | What the API emits | Standard |
| --- | --- | --- |
| Access log | one record per request at response end, on by default; level by status (5xx ERROR, 4xx WARN, otherwise INFO); allow-listed headers, cookies and body fields only; the framework's default access log turned off | [signals/access-log.md](../signals/access-log.md) |
| Application logs | all logging through the app's logger (the language's standard logger with the OpenTelemetry bridge), `json` or `plain`, native trace and span IDs, step logs for every wrapped function at the policy's levels | [signals/app-logs.md](../signals/app-logs.md) |
| Traces | a SERVER entry span per request, nested INTERNAL spans for the project's own functions on the request path, a CLIENT span for every outbound call, no framework middleware spans; root spans for job runs and WebSocket traffic; semantic-convention groups chosen from what the code does | [signals/traces.md](../signals/traces.md), [functions.md](../functions.md) |
| Metrics | the ops metrics from the stack's Prometheus client library on `/metrics`, on its own port: HTTP server and client, process and runtime, the database pool, WebSocket, failed probes and jobs; short, bounded labels only | [signals/metrics.md](../signals/metrics.md) |
| Errors | one ERROR log record with `exception.*` where the failure is handled; span status ERROR with `error.type`; process hooks for uncaught errors; 4xx not reported by default; no stack traces in responses | [signals/errors.md](../signals/errors.md) |
| Profiling | continuous profiles over the Pyroscope protocol (or OTLP profiles where the backend takes them); the resource attributes as static labels; `controller` labels on slow or CPU-, memory- or lock-heavy code only | [signals/profiling.md](../signals/profiling.md) |

A sampled request then reads as one tree, and every log record written on the way carries its
trace and span IDs:

```text
SERVER    POST /orders             http.route=/orders  user.id=u-123  tenant.id=t-1  status=UNSET
  INTERNAL  OrderController.create   code.function.name=…  app.cart.items_count=3
    INTERNAL  OrderService.create      app.order.id=o-77
      CLIENT    INSERT orders            db.system.name=postgresql  db.collection.name=orders
      CLIENT    POST                     server.address=payments-api  http.response.status_code=201
```

## Entry-span request attributes

The resource is created once per process, so per-request facts go on each service's entry span
(the local root — SERVER for a request) and on its access log: never on the resource, and not
on every child span. The middleware sets them. Values are written as they are, except secrets,
which are never captured ([safe-capture.md](../safe-capture.md)).

| Attributes | Source | Rule |
| --- | --- | --- |
| `http.request.method`, `url.scheme`, `url.path`, `server.address`, `server.port`, `network.protocol.version`, `user_agent.original` | HTTP instrumentation | semantic conventions |
| `http.route` | the router, after routing | template; `unmatched` when nothing matched |
| `url.query` | request URL | values of allow-listed parameters only |
| `client.address` | trusted-proxy resolver | the real client; `X-Forwarded-For` from an untrusted peer is ignored |
| `http.request.header.<name>` | request headers | allow-list only; never `Authorization` or `Cookie` |
| `http.request.body.size`, `http.response.body.size` | server | sizes only — bodies never go on spans |
| `user.id`, `user.roles`, `tenant.id`, `session.id` | verified principal and session, after authentication | read at response end |
| `correlation.id` | `x-correlation-id`, or generated | also on every log record |
| `app.client.name`, `app.client.version`, `app.client.platform` | allow-listed request headers | custom namespace (`app.` by default) |
| `http.response.status_code`, `error.type` | response | status ERROR on 5xx; a 4xx leaves the server span UNSET |

- Child spans don't repeat request attributes; they carry their own operation's attributes,
  result and status.
- Request attributes are never resource attributes, metric labels or profile labels.
- Another service receives request context only through allow-listed baggage, which its own
  middleware puts on its own entry span ([enrichment.md](../enrichment.md)).

### GraphQL

Every operation shares one HTTP route, so the HTTP span alone says little. Add an operation span
named `{graphql.operation.type} {graphql.operation.name}` with `graphql.operation.type` and
`graphql.operation.name`; the document text stays off by default. A response with an `errors`
list sets that span to ERROR with `error.type`, even when the HTTP status is 200. Operation names
come from clients: use them as metric labels only when the set is bounded (persisted or
allow-listed operations).

### gRPC

Each call gets a SERVER span named `{rpc.method}` with `rpc.system.name`, `rpc.method` and
`rpc.response.status_code`, status ERROR for the server-error codes the conventions list, and the
`rpc.server.call.duration` metric. The SDK may need the semantic-convention opt-in for these
names; legacy `rpc.system` and `rpc.service` are accepted. Each call writes one access-log
record, and the standard gRPC health service is excluded like the HTTP probes.

## Health probes

- Successful responses (status 200–399) on the excluded paths — `/health`, `/ready`, `/live`,
  `/metrics` and `/status` by default, plus the service's API docs paths such as `/api-docs`
  and `/api-docs/*` — produce no span, no metric and no access-log record. Each signal has its
  own `exclude_paths` list ([config.md](../config.md#path-exclusions)).
- A failed probe (for example `/ready` returning 503) always produces a `health.probe.failed`
  record (WARN, or ERROR on 5xx) with the access-log fields, the probe and the dependency, in
  place of its access-log record and even when the access log is off. It
  increments `app_health_probe_failures_total{probe,dependency}` (`probe` is the matching
  `exclude_paths` entry; `dependency` is the name the readiness response gives the failing
  check, or `unknown` when it names none), and its trace is kept only when
  `tracing.trace_failed_probes` is true ([hooks.md](../hooks.md)). A 404 on an excluded path is
  an ordinary request, not a failed probe: the path isn't served there, like `/metrics` asked
  on the app port.
- An entry matches the exact path, query string ignored, or with a trailing `/*` every path
  below it: `/api/healthcare` is an ordinary endpoint and is recorded in every signal.
- Readiness handlers name each check in their response, for example
  `{"checks": {"upstream": {"status": "unhealthy"}}}`, and the failure record and the counter
  take the failing check's name (`upstream`) from it. The instrumentation never picks a name of
  its own, such as the client span's peer name.
- `/metrics` and the health endpoints stay off public ingress ([charts.md](../charts.md)).

## WebSocket

A WebSocket connection lives for minutes or hours, so it is never one span. Its handshake and
each of its messages start their own traces, and the connection's lifecycle is logged and
counted.

| Unit | Span | Logs | Metrics |
| --- | --- | --- | --- |
| Connection: handshake and authentication | a root `SERVER` span `WS {path}` (the endpoint's route, for example `WS /ws`), ended once the connection is accepted or rejected | INFO `websocket connected`; WARN `websocket rejected` when the server refuses the client; INFO `websocket disconnected` when the connection ends | `websocket_connections`, `websocket_connections_total`, `websocket_disconnections_total` |
| Message, other than a ping | a root `SERVER` span `WS {type}`, linked to the connection span | the handler's step logs; one ERROR record when handling fails | `websocket_messages_received_total`, `websocket_receive_errors_total` |
| Ping, pong or heartbeat | none | none | counted in `websocket_messages_received_total` |
| Message the server sends | part of the span that sends it | none of its own | `websocket_messages_sent_total`, `websocket_send_errors_total` |

- **Connection span.** `url.path`, `client.address` from the trusted-proxy resolver, and
  `user.id` and `tenant.id` once authentication succeeds. A rejection records the close code as
  `app.websocket.close_code` on the span and the WARN record; the close reason a client sends is
  not recorded. A client the server refuses (a wrong origin, missing or invalid credentials) is
  not a server failure: the span stays UNSET. The status is ERROR only when the server itself
  failed during the handshake.
- **Message spans.** Named `WS {type}` from a bounded set of message types defined in code; a
  type outside the set is named `WS unknown`. Each is a root span, never a child of the
  connection span, with a link to it, `app.websocket.message.type`, and the connection's
  `user.id` and `tenant.id`, since it is an entry span. The handler runs inside it, so function
  spans, client spans and client metrics belong to the message's trace. The status is ERROR only
  when handling the message failed.
- **Pings and heartbeats** are counted, never traced or logged.
- **Connection counters** pair up: `websocket_connections_total` counts accepted connections and
  `websocket_disconnections_total` the accepted ones that closed, so their difference is the
  `websocket_connections` gauge. A rejected handshake is logged and traced, not counted.
- **No access log.** The connected, rejected and disconnected records take its place; the
  upgrade request writes no access-log record of its own. The disconnected record carries
  `app.websocket.close_code`.
- **One source.** Where a framework instrumentation already spans WebSocket traffic (one span
  for the whole connection, or one for every send and receive), turn that off; the module makes
  the connection and message spans.
- **Sampling** decides per root: each handshake and each message on its own.

## BFF and integration services

A BFF or integration service follows every rule above, and adds:

- **One CLIENT span per downstream call**, named `{method} {url.template}`, with `url.full`
  (allow-listed query values only), `server.address`, `server.port` and
  `http.request.resend_count`; status ERROR on 4xx and 5xx. Add `app.downstream.name`,
  `app.retry.attempt`, `app.circuit_breaker.state` and `app.rate_limit.remaining`, and a
  `circuit_breaker.opened` event log when a breaker opens.
- **Per-downstream metrics:** `http_client_request_duration_seconds` and
  `http_client_active_requests` by `service` (the downstream's name), `method`, `route` (the URL
  template) and `status_code`.
- **A propagation allow-list:** trace context goes only to internal and allow-listed downstreams,
  and baggage only with allow-listed keys; third-party APIs get neither. Sampling flags from
  public clients are not trusted.
- **Retry and timeout attributes** on each client span: the attempt number, the resend count,
  and `error.type` naming the timeout when a call times out.
- **Downstream error mapping:** the downstream failure stays on its CLIENT span, the BFF answers
  in its own error format, and only the layer that handles the failure writes the ERROR log.

## Async workers and messaging

Consumers, queue workers and stream processors follow the API rules with a message in place of a
request. A dedicated workers page will follow; in short:

- **Context from message headers:** producers inject `traceparent` (and allow-listed baggage)
  into message headers; consumers extract it before processing.
- **Spans:** PRODUCER spans for publishing, and one CONSUMER span per processed message with
  `messaging.system`, `messaging.destination.name`, `messaging.operation.type`,
  `messaging.consumer.group.name` and `messaging.message.id`. A batch is processed under one
  span with **links** to each message's context instead of a single parent.
- **Per-message job log** instead of an access log: one record per message when processing ends,
  with the outcome, duration, destination and attempt; `job.retry_scheduled` and
  `job.dead_lettered` events when they happen.
- **Metrics:** the job metrics `job_runs_total{job_name,outcome}` and `job_duration_seconds{job_name}`
  per message type; consumer lag and queue depth from the broker's own exporter. Never message
  IDs as labels.
- **Adapter:** `adapters/consumer` ([layout.md](../layout.md)).

## Conformance

Drive requests through the running service and check the result through the collector or the
receiver, and scrape `/metrics`: request attributes on the entry span and access log, the nested
span tree without framework middleware spans, probe and docs-path behaviour, WebSocket and job
spans and records, the metrics contract and safe capture ([acceptance.md](../acceptance.md)).

[Documentation index](../README.md)
