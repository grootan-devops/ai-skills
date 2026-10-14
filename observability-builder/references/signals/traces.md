# Traces

Every operation the service performs becomes a span: the entry span of each request, message,
job run or WebSocket message, a nested span for each of the project's own functions, and a span
for each outbound call. Spans travel over OTLP to the collector, and sampling decides how many
traces are kept.

## Required behaviour

1. **Export** over OTLP/gRPC to the collector by default, with the batch span processor,
   retries, gzip compression and timeouts. `tracing.exporter` changes the destination,
   protocol or auth ([config.md](../config.md)). With Langfuse on the app route, a second
   processor on the same provider sends the AI part of each trace to Langfuse
   ([genai.md](genai.md)); never a second provider.
2. **Sampling** uses any standard sampler, or one the SDK registers, with its argument:
   `tracing.sampler.name` and `tracing.sampler.arg` are written unchanged to
   `OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG`. Sampling controls volume, not skipping
   functions: lower the ratio of `parentbased_traceidratio` to keep fewer traces. AI entry
   points are the exception: the module's rule sampler keeps them all, because feedback,
   approvals and evaluations need the trace ([genai.md](genai.md)).
3. **Operations follow the code.** Semantic-convention groups are chosen from what the code
   does, found during the audit: HTTP server and client, database and cache, messaging, RPC,
   GenAI, MCP, GraphQL, cloud SDKs, serverless. Every group found gets its attributes, not only
   HTTP.
4. **Nested spans for API work and calls to other services.** Handlers, services and
   repositories get `INTERNAL` child spans through the module's wrapper, and every call to a
   third-party or internal service gets a `CLIENT` span with `service.peer.name`. Start-up code,
   helpers and middleware get none, and nothing outside a request, a job run or a WebSocket
   message starts a trace ([functions.md](../functions.md)).
5. **No framework middleware spans.** The framework instrumentation is configured to skip its
   middleware, router and handler layers, which keeps `http.route` on the `SERVER` span; the
   module's wrappers span the handlers instead ([hooks.md](../hooks.md)).
6. **Job runs and WebSocket spans are roots.** Each job run is a root span `job {name}`
   ([batch](../workloads/batch.md)); a WebSocket connection's handshake and each of its messages
   are root `SERVER` spans, the messages linked to the connection
   ([WebSocket](../workloads/api.md#websocket)). Work that continues after a response is a new
   root span linked to the request, never a child of it.
7. **Request attributes on entry spans.** User, tenant, session, client and `correlation.id` go
   on each service's entry span, the local root (`SERVER` or `CONSUMER`), and nowhere else:
   child spans stay lean, and these values are never resource attributes or metric labels
   ([enrichment.md](../enrichment.md)). The one exception is the AI trace context, which the
   module stamps on every span of an AI feature for Langfuse and strips on the ops path
   ([genai.md](genai.md#trace-context)).
8. **Low-cardinality names:** `{method} {http.route}` with the route template
   (`{method} unmatched` when no route matched), `{db.query.summary}`, `{Class}.{method}`,
   `job {name}`, `WS {type}`; never ids or raw paths.
9. **Status and errors** follow the [rules below](#status): UNSET by default, ERROR only when the
   span's own operation failed, never OK.
10. **Propagation** is W3C tracecontext plus allow-listed baggage ([below](#propagation)).
11. **Resource** is the eight standard attributes in the [data model](../data-model.md#resource),
    from the yml and the downward API, plus `k8s.cluster.name` from the chart where several
    clusters send to one backend. The SDK's default detectors are left out where the SDK allows
    it ([sdk-notes.md](../sdk-notes.md)). Never a trace id in the resource.
12. **Limits.** The SDK's attribute value length limit is set in code, and attribute values
    respect it. With GenAI content on, the limit is at least `genai.content.max_chars`: SDKs cut a
    longer value blindly, which breaks JSON before the content cap can shorten it.
13. **No content on spans.** No request or response bodies, raw arguments or payloads in span
    attributes; sizes and summaries only. A path segment that holds an email is masked as
    `{email}` in `url.path` on `SERVER` spans and in `url.full` and `url.path` on `CLIENT` spans;
    ids stay visible. AI content (prompts, completions, tool arguments and results) goes only to
    the content destination, Langfuse or the ops backend's AI views, through the content policy
    ([genai.md](genai.md#content)).

## Fields

Attributes per operation, request context and native span fields are defined in
[data-model.md](../data-model.md). Kinds and names in short:

| Operation | Kind | Name |
| --- | --- | --- |
| HTTP server | `SERVER` | `{method} {http.route}`; `{method} unmatched` when no route matched |
| HTTP client | `CLIENT` | `{method} {url.template}` |
| Call to another service | `CLIENT` | `{service.peer.name} {operation}` |
| Database or cache | `CLIENT` | `{db.query.summary}`, or `{db.operation.name} {db.collection.name}` |
| Messaging | `PRODUCER` to publish, `CONSUMER` to process | per the messaging conventions; batches use links |
| RPC, gRPC | `SERVER` or `CLIENT` | `{rpc.method}` |
| GenAI model call or embedding | `CLIENT` | `{gen_ai.operation.name} {gen_ai.request.model}` |
| GenAI agent, tool, retrieval, memory, planning, workflow, guardrail, evaluation | `INTERNAL` (`CLIENT` for a remote store or agent) | `invoke_agent {agent}`, `execute_tool {tool}`, `retrieval {data source}`, `{memory operation}`, `plan {agent}`, `invoke_workflow {name}`, `guardrail {name}`, `evaluate {name}` ([genai](genai.md#step-kinds)) |
| MCP | `CLIENT` on the sender, `SERVER` on the receiver | `{mcp.method.name} {target}`, the target being the tool or prompt name, or the method alone ([mcp](../workloads/mcp.md)) |
| Application function | `INTERNAL` | `{Class}.{method}` or `{module}.{function}` |
| Job run | `INTERNAL`, root | `job {name}` |
| WebSocket connection (handshake and authentication) | `SERVER`, root | `WS {path}`, the endpoint's route (`WS /ws`) |
| WebSocket message (pings excluded) | `SERVER`, root, linked to the connection span | `WS {type}` |

Span events are not used for exceptions: OpenTelemetry is deprecating them, and the exception is
the ERROR log record ([errors.md](errors.md)).

### Status

The status is UNSET by default. It becomes ERROR only when that span's own operation failed, and
it is never set to OK:

| Span | Status ERROR when | Otherwise |
| --- | --- | --- |
| HTTP server | the response is 5xx, or an exception escaped the handler | UNSET, 4xx included |
| HTTP client | the response is 4xx or 5xx, or no response arrived | UNSET |
| Database, messaging, RPC, GenAI | the operation failed | UNSET |
| MCP | the client: any JSON-RPC error, a tool result with `isError`, a timeout; the server: its own failure or an `isError` result | UNSET, the caller's errors on the server included (-32700, -32600, -32601, -32602, -32002) |
| Application function | the function failed: it threw, or returned an error | UNSET |
| Job run | the run failed: its body threw | UNSET, a run with some failed items included |
| WebSocket connection | the server failed during the handshake | UNSET, client rejections included (bad origin, failed authentication: a WARN record) |
| WebSocket message | handling the message failed | UNSET |

A GenAI step that succeeded in a degraded way (a guardrail block, a truncated answer, a
fallback prompt) stays UNSET and carries a WARNING level for Langfuse instead
([genai.md](genai.md)).

Every failure sets `error.type` (the exception type, or the status code as a string) and a
status message without payloads or credentials. Success leaves the status UNSET, the
OpenTelemetry default that backends read as success. A caller that an exception passes through
failed too and is marked ERROR; a caller that handles the failure and carries on stays UNSET.
Inner layers mark their span and rethrow; only the boundary that handles the failure writes the
ERROR log.

### Propagation

- **Propagators** are fixed in code: W3C `tracecontext` and `baggage`.
- **Baggage is allow-listed.** The middleware puts allow-listed incoming keys on its entry span,
  and outbound calls send only allow-listed keys; never secrets, request content or, by default,
  ids. The allow-list is code, not configuration ([enrichment.md](../enrichment.md)). The baggage
  propagator applies it itself, on extract and inject, because libraries write baggage too:
  Langfuse's JS tracing puts `langfuse_trace_id` there and reads `langfuse_*` keys back as span
  attributes ([sdk-notes.md](../sdk-notes.md#genai-and-langfuse)).
- **Messaging** carries the context in message headers; a consumer that processes a batch links
  to each message's context.
- **Browsers:** a backend that serves browsers allows `traceparent`, `tracestate` and `baggage`
  in its CORS policy, so browser and API spans join one trace.
- **Public edges** (gateway, BFF, public API) keep the incoming trace id, so browser-to-API
  traces still join, but don't trust the incoming sampled flag: they decide with a root sampler
  such as `traceidratio` instead of a `parentbased_*` one. Internal hops keep `parentbased_*`.

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `tracing.enabled` | `true` | `OTEL_TRACES_EXPORTER` `otlp` or `none` |
| `tracing.exporter.*` | `grpc`, `gzip`, `10s` | the OTLP exporter block ([config.md](../config.md)) |
| `tracing.sampler.name` | `parentbased_always_on` | any standard or SDK-registered sampler |
| `tracing.sampler.arg` | none | a string: a ratio from `0` to `1` for `*traceidratio`, the remote settings for `*jaeger_remote` |
| `tracing.exclude_paths` | `[/health, /ready, /live, /metrics, /status]` | successful responses only; add the API docs paths; exact paths, or `/path/*` for a subtree |
| `tracing.trace_failed_probes` | `false` | keep traces of failed probes |

## Exclusions

Successful requests (status 200–399) to `tracing.exclude_paths` produce no spans: the probes,
`/metrics` and the service's API docs paths (for example `/api-docs` and `/api-docs/*`). A path
rule in the sampler, or the runtime's URL exclusion, drops them at start. With
`trace_failed_probes: true` they are recorded instead, and an export filter (an SDK exporter
wrapper or a collector `filter`) drops only the successful ones, so failed probes keep their
traces. Failed probes are always logged and counted either way ([hooks.md](../hooks.md)).

An entry matches the exact path, query string ignored; an entry ending in `/*` matches every
path below it ([config.md](../config.md#path-exclusions)). Translate that rule into each
library's matcher (exact, prefix, regex or unanchored search) so `/health` is excluded and
`/api/healthcare` is kept. Where a tool can't filter by path in code (e.g. the Java agent), use
its declarative-config rule-based sampler, with a collector `filter` as the fallback.

## Failure behaviour

- **Fails open for delivery.** Batch span processor only, with a bounded queue that drops and
  counts on overflow, plus export and shutdown timeouts; SDK self-metrics make drops visible.
  The service keeps serving with the collector or the backend down.
- **Unsampled requests** get no-op spans, so the function wrappers cost little and the access log
  still records the request.
- **Tracing never changes behaviour:** it doesn't alter a response or swallow an exception.

## Verification

[acceptance.md](../acceptance.md) checks the entry span name and HTTP attributes with a templated
`http.route` (`unmatched` on a 404), request attributes on entry spans only, the nested span
tree with `code.*` attributes and no framework middleware spans, ERROR and `error.type` on the
failing span and its callers and no OK status anywhere, job and WebSocket root spans, unsampled
requests with no spans but an access log, one trace id from the browser to the database,
allow-listed baggage only, no bodies in span attributes, and the exclusions.

[Documentation index](../README.md)
