# Telemetry data model

The field dictionary every service follows: where each field belongs, where its value comes from
and the rules it obeys. Names follow OpenTelemetry semantic conventions; custom names use the
project's namespace, `app.` by default ([enrichment](enrichment.md#naming-and-safety)). Values are
written as they are, `client.address` and user, tenant and session IDs included; secrets are never
captured ([safe capture](safe-capture.md)).

## Where each field belongs

| Location | What belongs there |
|---|---|
| Resource attributes | Identity of the producer: the eight standard attributes [below](#resource), the service and its pod, plus the cluster's name where several clusters send to one backend. Created once per process and shared by its traces, logs, OTLP metrics and profiles. |
| Span attributes | Details of one operation: an HTTP request, a database query, a message, an RPC, a GenAI or MCP call, an application function, a job run, a WebSocket message. AI content (prompts, completions, tool arguments and results) only on the Langfuse path, through the content policy ([genai](signals/genai.md#content)). |
| Log attributes | Details of one log event, including exception information. |
| Metric labels | Short, bounded labels of a measurement: method, route, status code, dependency, job, outcome. |
| Profile labels | Static identity labels and bounded dynamic labels such as `controller`. |
| Native OTLP fields | Trace and span IDs, timestamps, span kind, status, events and links, log severity and body, instrumentation scope; for OTLP metrics, the metric type, unit, temporality and buckets. Always set natively, never as ordinary attributes. |

## Resource

Resource attributes, on every signal. The resource carries the first eight attributes below,
and the ninth only where several clusters send to one backend:

| Attribute | Example | Source and rule |
|---|---|---|
| `service.name` | `checkout-api` | `observability.yml`; stable across releases and replicas, because backends key services on it |
| `service.namespace` | `commerce` | `observability.yml`; the application group the deployment is part of (its `partOf` name), shared by every service of the application |
| `service.version` | `1.8.2` | `observability.yml`, filled from the build (the image tag); never a default, which would merge releases |
| `deployment.environment.name` | `production` | `observability.yml`; never a default, which would merge environments |
| `k8s.namespace.name` | `commerce-prod` | the downward API, through `resource.attributes` |
| `k8s.pod.name` | `checkout-api-7d9c6b5f4-x2k8p` | the downward API, through `resource.attributes` |
| `k8s.pod.uid` | `3f1c9a2e-5b7d-4c1e-9f0a-2b6d8e4c7a11` | the downward API, through `resource.attributes`; the collector matches telemetry to its pod by it, so pod association doesn't depend on the connection's source IP |
| `k8s.node.name` | `worker-a1` | the downward API, through `resource.attributes` |
| `k8s.cluster.name` | `prod-eu-1` | optional, for setups where several clusters send to one backend. It isn't in the downward API: the chart sets it per cluster (for example an env var `K8S_CLUSTER_NAME` from a cluster-level value), and `resource.attributes` lists it like the other `k8s.*` keys |

Empty values are dropped: outside Kubernetes every `k8s.*` value, and `k8s.cluster.name`
wherever the chart doesn't set it. Nothing else goes in the resource:

- **SDK detector attributes are dropped** where the SDK allows it: `telemetry.sdk.*`,
  `process.*`, `host.*`, `os.*` and `vcs.*`. How per SDK is in [sdk-notes.md](sdk-notes.md);
  where an SDK can't drop one, it stays and the report says so. The SDK is never patched for it.
- **`service.instance.id` is not set:** the pod name and UID identify the instance.
- **Build and deployment details** (commit, branch, pipeline run) stay out: `service.version`
  identifies the release.

A web page has a resource of its own, for RUM ([below](#rum-and-profiling)).

### Cross-signal identity

Telemetry the SDK sends and telemetry the collector derives (stdout logs, scraped metrics, eBPF
traces and profiles) carry the **same** identity:

- The deployment renders `observability.yml` and the pod annotations
  `resource.opentelemetry.io/<attribute>` (for `service.name`, `service.namespace`,
  `service.version` and `deployment.environment.name`, and for any extra attribute the yml adds,
  such as a backend's label attributes) from the same values, and passes the pod's
  namespace, name, UID and node through the downward API ([charts](charts.md)), plus the cluster's
  name from a chart value where it is used.
- The collector's `k8sattributes` processor runs with `extract.otel_annotations: true`, so it reads
  those annotations, and extracts the same four pod `k8s.*` attributes. It can't provide
  `k8s.cluster.name`, which comes from the app's environment; where the cluster name is used,
  each cluster's collector adds it to the telemetry it collects itself (scraped metrics, file
  logs) with a resource processor set from the same per-cluster value
  ([collector](collector/README.md)). Without the annotations,
  `service.name` is derived from the `app.kubernetes.io/instance` label, which can differ from
  the SDK's value, and one service shows up under two names.
- The collector adds no host, OS, process, container or cloud attributes to application
  telemetry ([collector](collector/README.md)).

Never put a trace ID, or any other per-request value, in resource attributes or
`OTEL_RESOURCE_ATTRIBUTES`: the resource is fixed for the life of the process.

## Resource vs request attributes

The resource is created once when the SDK starts and is shared by every trace, log and metric the
process emits; it can't change per request. Request facts the middleware learns (`user.id`,
`session.id`, `http.route`, tenant, client version) therefore go on **each service's entry span**,
the local root (`SERVER` or `CONSUMER`), and on its access log. Child spans stay lean: their own
operation's attributes, result and status. Repeating the request context on every span would
multiply storage without adding information, since backends join child spans to the entry span by
trace ID.

Another service gets the request context only through allow-listed baggage, which its own
middleware puts on *its* entry span. Values are written as they are, IDs and client IP included,
except secrets, which instrumentation never captures.

```text
Resource (once per process; identical for trace A and trace B)
  service.name=checkout-api  service.namespace=commerce  service.version=1.8.2
  deployment.environment.name=production  k8s.namespace.name=commerce-prod
  k8s.pod.name=checkout-api-7d9c6b5f4-x2k8p  k8s.pod.uid=3f1c9a2e-…  k8s.node.name=worker-a1

Trace A (user u-123, session s-9)
  SERVER     POST /orders          http.route=/orders  user.id=u-123  session.id=s-9
                                   tenant.id=t-1  client.address=203.0.113.27
                                   app.client.version=5.2  status=UNSET
    INTERNAL   OrderService.create code.function.name=com.example.OrderService.create
                                   app.order.id=o-77  app.cart.items_count=3  status=UNSET
      CLIENT     INSERT orders     db.system.name=postgresql  db.operation.name=INSERT
                                   db.collection.name=orders  status=UNSET

Trace B (user u-456, session s-4)
  SERVER     GET /orders/{id}      http.route=/orders/{id}  user.id=u-456  session.id=s-4
                                   status=ERROR  error.type=500
    CLIENT     GET                 server.address=payments-api  http.response.status_code=503
                                   status=ERROR  error.type=503
```

## Operations

Span attributes and results. Semantic-convention groups are chosen from what the code and the
service actually do: every operation type found during the audit gets its group's attributes, not
only HTTP. How spans are produced per framework: [traces](signals/traces.md).

| Operation | Native fields | Attributes | Result |
|---|---|---|---|
| HTTP server | kind `SERVER`, name `{method} {http.route}` | `http.request.method`, `http.route` (the template, or `unmatched`), `url.scheme`, `url.path` (a segment that holds an email masked as `{email}`), `url.query` (allow-listed parameter values only), `server.address`, `server.port`, `client.address` (trusted-proxy resolver), `user_agent.original`, `network.protocol.version`, `http.request.header.<name>` (allow-list), `http.request.body.size` | `http.response.status_code`, `http.response.body.size`, `error.type`; status ERROR on 5xx or an escaped exception |
| HTTP client | kind `CLIENT`, name `{method} {url.template}` (`{method}` when no template) | `http.request.method`, `url.full` and `url.path` (sanitised: no credentials, allow-listed query values, an email in the path masked as `{email}`, no other content in the path), `url.template`, `server.address`, `server.port`, `service.peer.name`, `http.request.resend_count` | `http.response.status_code`, `error.type`; status ERROR on 4xx and 5xx |
| Call to another service (client module) ([functions](functions.md#calls-to-other-services)) | kind `CLIENT`, name `{service.peer.name} {operation}` | `service.peer.name`, `server.address`, `server.port`, `network.transport` (non-HTTP), safe operation attributes | `error.type`; status ERROR on failure; the HTTP client span nests under it |
| Database or cache | kind `CLIENT`, name `{db.query.summary}` or `{db.operation.name} {db.collection.name}` | `db.system.name`, `server.address`, `server.port` (integer), `db.namespace`, `db.operation.name`, `db.collection.name`, `db.query.summary`, `db.query.text` (optional; parameterised or sanitised, never parameter values) | `db.response.status_code`, `db.response.returned_rows`, `error.type` |
| Messaging | kind `PRODUCER` to publish, `CONSUMER` to process; links for batches | `messaging.system`, `messaging.destination.name`, `messaging.operation.type`, `messaging.operation.name`, `messaging.consumer.group.name`, `messaging.destination.partition.id`, `messaging.message.id`, `server.address`, `server.port`; trace context travels in message headers | `messaging.batch.message_count`, `error.type` |
| RPC, gRPC | kind `SERVER` or `CLIENT`, name `{rpc.method}` | `rpc.system.name`, `rpc.method`, `server.address`, `server.port` (release-candidate names through the `rpc` stability opt-in; legacy `rpc.system` and `rpc.service` accepted) | `rpc.response.status_code`, `error.type` |
| GenAI model call or embedding ([genai](signals/genai.md#step-kinds)) | kind `CLIENT`, name `{gen_ai.operation.name} {gen_ai.request.model}` (`chat gpt-4o-mini`, `embeddings text-embedding-3-small`) | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` and parameters, `gen_ai.conversation.id`, `gen_ai.prompt.name`, `app.genai.prompt.version`; content only through the content policy, on the Langfuse path | `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.*`, `gen_ai.response.time_to_first_chunk`, `error.type`; a WARNING level for a degraded success |
| GenAI agent, tool, retrieval, workflow, guardrail or evaluation step | kind `INTERNAL` (`CLIENT` for a remote store or agent), names `invoke_agent {agent}`, `execute_tool {tool}`, `retrieval {data source}`, `invoke_workflow {name}`, `guardrail {name}`, `evaluate {name}` | `gen_ai.agent.*`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.data_source.id`, `gen_ai.workflow.name`, `gen_ai.evaluation.*`, the module's `app.genai.*` and `app.rag.*` attributes; the Langfuse type where the operation doesn't imply it | `error.type`; status ERROR only for the step's own failure |
| AI trace context (every span of an AI feature) | — | `langfuse.trace.name` (the feature), `langfuse.user.id`, `langfuse.session.id` (the conversation), `langfuse.release`, `langfuse.trace.tags`, `langfuse.trace.metadata.*`, stamped by the module on every span started inside the feature: the one scoped exception to request context on the entry span only, because Langfuse filters per observation | stripped on the ops path; never baggage |
| MCP | kind `SERVER` or `CLIENT`, name `{mcp.method.name} {target}` | `mcp.method.name`, `mcp.protocol.version`, `mcp.session.id`, `jsonrpc.request.id`, `gen_ai.tool.name`, `network.transport` | `rpc.response.status_code`, `error.type` |
| Application function ([functions](functions.md)) | kind `INTERNAL`, name `{Class}.{method}` or `{module}.{function}` | `code.function.name`, `code.file.path`, `code.line.number`, `thread.name`, allow-listed argument summaries, [enrichment](enrichment.md) attributes | result summary (count, size, status), `error.type`; status ERROR on failure |
| Job run ([batch](workloads/batch.md)) | kind `INTERNAL`, a root span named `job {name}`; a link to the request that started it, if any | `app.job.name`, `app.job.trigger` (`cron`, `manual` or `internal`) | `app.job.items.processed`, `app.job.items.failed`, `error.type`; status ERROR when the run failed |
| WebSocket connection ([api](workloads/api.md#websocket)) | kind `SERVER`, a root span named `WS {path}` (the endpoint's route, for example `WS /ws`), covering the handshake and authentication | `url.path`, `client.address`, `user.id`, `tenant.id` | `app.websocket.close_code` on a rejection; UNSET for a client rejection, ERROR only when the server failed |
| WebSocket message | kind `SERVER`, a root span named `WS {type}` from a bounded set of types, linked to the connection span; pings get none | `app.websocket.message.type`, `user.id`, `tenant.id` | `error.type`; status ERROR when handling failed |
| GraphQL | name `{graphql.operation.type} {graphql.operation.name}` | `graphql.operation.name`, `graphql.operation.type`; document text off by default | the errors list mapped to `error.type` |
| Cloud SDK or object storage | kind `CLIENT` | `rpc.system.name` (e.g. `aws-api`), `rpc.method`, `cloud.region`, service attributes such as `aws.s3.bucket` (object keys only if they are not sensitive) | `http.response.status_code`, `error.type` |
| Serverless invocation | kind `SERVER` or `CONSUMER` | `faas.trigger`, `faas.invocation_id`, `faas.coldstart` | `error.type` |
| Request context (each service's entry span only) | — | `user.id`, `tenant.id`, `session.id`, `correlation.id`, `http.route`, client app and version, set by the middleware on the `SERVER` or `CONSUMER` span as they are; `client.address` from the trusted-proxy resolver; child spans don't repeat them | never resource attributes, never metric labels |

`tenant.id` and `correlation.id` are this standard's own names, not OpenTelemetry's. The
correlation ID comes from a valid incoming `x-correlation-id` header or is generated
([hooks](hooks.md)). `gen_ai.conversation.id` is the AI conversation or thread, while
`session.id` stays the login or replay session; neither is derived from the other.

Native span fields (`traceId`, `spanId`, `parentSpanId`, a low-cardinality name, kind, start and
end time, status `{code, message}`, events, links, `traceState`, flags) are generated by the SDK.
The status is UNSET by default and ERROR only when the span's own operation failed; it is never
set to OK ([traces](signals/traces.md#status)).
W3C `traceparent` carries the context across every boundary. Where an instrumentation library can
emit older attribute names, the semantic-convention stability opt-in
(`OTEL_SEMCONV_STABILITY_OPT_IN`) is fixed in code so it emits the names above
([configuration](config.md)).

## Logs, errors and trace correlation

| Location | Field | Rule |
|---|---|---|
| Native | `timeUnixNano`, `observedTimeUnixNano` | event time from the application |
| Native | `severityNumber`, `severityText` | TRACE 1, DEBUG 5, INFO 9, WARN 13, ERROR 17, FATAL 21 |
| Native | `body` | message text (a constant template, see [functions](functions.md#message-templates)) or a structured map |
| Native | `traceId`, `spanId`, `flags` | must be native: set by the OpenTelemetry log bridge, or by the collector when it maps stdout lines; a JSON `trace_id` field on its own does not correlate |
| Native | `eventName` | `exception` on error records; `access_log`, `health.probe.failed` and `job_run` on those records; the event's name on other event records ([enrichment](enrichment.md)) |
| Scope | instrumentation scope name and version | the logger name, or the library that emits the record |
| Attributes | `exception.type`, `exception.message`, `exception.stacktrace` | errors and exceptions; the message is sanitised before recording ([safe capture](safe-capture.md)) |
| Attributes | `code.function.name`, `code.file.path`, `code.line.number`, `thread.id`, `thread.name` | where the event came from |
| Attributes | `correlation.id` on every record; the full request context (`user.id`, `tenant.id`, `session.id`, client) on the access log, optional on application logs | application logs join the entry span through the native trace IDs |
| Attributes (access log) | the HTTP server span's attributes, plus allow-listed `http.request.header.<name>` and `http.response.header.<name>`, `http.request.cookie.<name>` (custom) and selected body fields (custom, opt-in) | the raw `Cookie` and `Set-Cookie` headers are never logged |

Lines on stdout carry the event fields only; the collector attaches the resource from the same
values as the SDK ([cross-signal identity](#cross-signal-identity)). JSON lines are parsed field by
field. The `plain` format is one fixed prefix pattern that a single collector regex maps to the
native fields, plus a multiline rule for stack traces ([collector](collector/)).

## Metrics

Ops metrics with short, bounded labels, produced by each stack's Prometheus client library and
scraped from `/metrics` ([metrics](signals/metrics.md)).

| Metric | Labels |
|---|---|
| `http_server_request_duration_seconds`, `http_server_requests_total`, `http_server_active_requests` | `method`, `route` (the template, or `unmatched`), `status_code`; the counter has no `status_code`, the gauge only `method` |
| `http_client_request_duration_seconds`, `http_client_active_requests` | `service` (the dependency's name), `method`, `route` (the URL template), `status_code` (not on the gauge) |
| Process and runtime | the client library's defaults (`process_*`, `nodejs_*`, `python_gc_*`, `go_*`, `jvm_*`) |
| `db_pool_connections`, `db_pool_connections_active`, `db_pool_connections_idle`, `db_pool_connections_waiting`, `db_pool_wait_duration_seconds` | none; `pool` when the service has several pools |
| `websocket_*` | none |
| `app_health_probe_failures_total` | `probe` (the matching `exclude_paths` entry), `dependency` (the name the readiness response itself gives the failing check, never one the instrumentation picks; `unknown` when the response names none) |
| `job_runs_total`, `job_duration_seconds` | `job_name`, and `outcome` on the counter |
| `gen_ai_client_operation_duration_seconds`, `gen_ai_client_tokens_total`, `gen_ai_client_time_to_first_chunk_seconds` | `provider`, `model` (a fixed list, else `other`), and `operation` and `outcome` on the duration, `token_type` on the counter ([metrics](signals/metrics.md#genai-metrics)) |
| App and business metrics, added by the team | bounded values only, e.g. plan, payment method or outcome |

Rules:

- **Names and labels** are the contract's on every stack, with short label names (`method`, not
  `http_request_method`). A framework's ready-made metric is renamed to the contract, or its
  mapping is documented.
- **Bounded labels.** Never request, message, user, session, tenant or order IDs, raw URLs or
  paths, or secrets. `route` is the template (`/orders/{id}`), or `unmatched` when no route
  matched; `method` is a standard HTTP method, or `_OTHER`. Every distinct value is a new time
  series, so one per user or URL overwhelms the metrics backend.
- **No trace ids in metrics.** Metrics join traces and logs through the service, the route and
  the time, never through a label.
- **Preserved end to end.** Names, label sets, types and histogram buckets pass the collector
  unchanged.
- **OTLP push is opt-in,** for a team's own app metrics: OpenTelemetry instruments with bounded
  attributes, the SDK's cardinality limit set in code, and the temporality from
  `metrics.otlp.temporality` (cumulative, or delta where a backend requires it).

## RUM and profiling

| Signal | Metadata |
|---|---|
| Browser RUM resource | `service.name`, `service.version`, `deployment.environment.name`, `browser.platform`, `browser.language`, `browser.brands`, `browser.mobile`, `user_agent.original` |
| Browser spans and logs | `app.route` (the route template), `session.id` (the same value as the replay session), `correlation.id`, HTTP client attributes on fetch and XHR spans; exceptions with `exception.*` |
| Web vitals | `web_vital.lcp`, `web_vital.inp`, `web_vital.cls`, `web_vital.fcp`, `web_vital.ttfb` histograms with explicit buckets; dimensions `web_vital.rating`, `app.route` |
| Browser to backend | `traceparent` and `tracestate` on same-origin requests and allow-listed origins only, never a match-all pattern; the backend's CORS policy allows `traceparent`, `tracestate` and `baggage` |
| Session replay (OneUptime) | the recorder's session ID is `session.id`; a replay entry in `tracestate` links each session to its backend traces |
| Sensitive-input masking | replay uses the provider's masking mode and mask, block and ignore selectors on sensitive fields; the OpenTelemetry browser SDK never records input values or element text, strips query and fragment from `url.full` except allow-listed parameters, and sanitises exception messages ([details](safe-capture.md#browser-and-session-replay)) |
| Profiles | application name = `service.name`; the other resource attributes as static labels in Pyroscope syntax, dots replaced by underscores (`service_namespace`, `service_version`, `deployment_environment_name`, `k8s_namespace_name`, `k8s_pod_name`, `k8s_pod_uid`, `k8s_node_name`, and `k8s_cluster_name` when set); a `span_id` sample label only from a supported span-profile integration (none for Node.js); dynamic labels bounded ([profiling labels](functions.md#profiling-labels)) |

RUM is standardised on OneUptime: the OpenTelemetry Web SDK plus OneUptime session replay, with RUM
classified by the `browser.*` resource attributes. Its OTLP destination and keys still come from
configuration. Sessions, replay and profiles need their own instrumentation; resource attributes
alone record nothing.

Trace and span IDs are never set by hand as profile labels, because each value would create a
profile series per request. Only a supported span-profile integration attaches them, where the
runtime supports span profiles.

[Documentation index](README.md)
