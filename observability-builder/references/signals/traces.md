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
   helpers and middleware get none, GraphQL resolvers get none (the GraphQL operation has the
   span), and nothing outside a request, a job run or a WebSocket message starts a trace
   ([functions.md](../functions.md)).
5. **No framework middleware spans.** The framework instrumentation is configured to skip its
   middleware, router and handler layers, which keeps `http.route` on the `SERVER` span; the
   module's wrappers span the handlers instead ([hooks.md](../hooks.md)).
6. **Job runs and WebSocket spans are roots.** Each job run is a root span `job {name}`
   ([batch](../workloads/batch.md)); a WebSocket connection's handshake and each of its messages
   are root `SERVER` spans, the messages linked to the connection
   ([WebSocket](../workloads/api.md#websocket)). Work that continues after a response is a new
   root span linked to the request, never a child of it.
7. **Request attributes on entry spans.** The request and identity context goes on each
   service's entry span, the local root (`SERVER` or `CONSUMER`), and nowhere else. The core rows
   are `correlation.id` (M), `http.route` (M for HTTP), `user.id` (C, authenticated request),
   `tenant.id` (C, multi-tenant request), `session.id` (C, session-based request),
   `client.address` (C, HTTP and WebSocket), `user_agent.original` and the client app (R). Other
   identity rows (roles, user type, plan, organisation or account ids, `app.auth.*`) are added
   only when the service's code has the value; nothing is fabricated, and `request.id` is not
   used ([data model](../data-model.md#request-and-identity-context)). These values are P on
   child spans, in the resource and as metric or profile labels
   ([enrichment.md](../enrichment.md)). The one exception is the AI trace context, which the
   module stamps on every span of an AI feature for Langfuse and strips on the ops path
   ([genai.md](genai.md#trace-context)).
8. **Low-cardinality names:** `{method} {http.route}` with the route template
   (`{method} unmatched` when no route matched), `{db.query.summary}`, `{Class}.{method}`,
   `job {name}`, `WS {type}`; never ids or raw paths.
9. **Status and errors** follow the [rules below](#status): UNSET by default, ERROR only when the
   span's own operation failed, never OK.
10. **Propagation** is W3C tracecontext plus allow-listed baggage ([below](#propagation)).
11. **Resource** follows the [data model](../data-model.md#resource): the four service and
    deployment attributes (M) from the yml; the platform's rows (C), which on Kubernetes come
    from the downward API, plus `k8s.cluster.name` from the chart where several clusters send to
    one backend, and elsewhere from the SDK's platform detectors (`cloud.*`, `aws.ecs.*`,
    `faas.*`, `gcp.cloud_run.job.*`, `host.name`, `container.id`); and the SDK, runtime and OS
    rows `telemetry.sdk.language`, `telemetry.sdk.version`, `process.runtime.name`,
    `process.runtime.version` and `os.type` (R), which the SDK's detectors provide. Attributes
    the data model doesn't list, such as process ids and command lines or VCS data, are not
    added ([sdk-notes.md](../sdk-notes.md)). Never a trace id or any per-request value in the
    resource.
12. **Limits.** The SDK's attribute value length limit is set in code, 8192 by default, and
    attribute values respect it. With GenAI content on, `genai.content.max_chars` (8000 by
    default) stays at or below it: SDKs cut a longer value blindly, which breaks JSON before the
    content cap can shorten it. A project that needs longer content raises both together, the
    limit equal to `max_chars`, and records the exception in the report.
13. **No content on spans.** No request or response bodies, raw arguments or payloads in span
    attributes; sizes and summaries only. Personal data in a span attribute, a path segment
    included, is written only through the shared masking helper (an email becomes
    `j***@example.com` in `url.path` and `url.full`), and dropped if masking fails; internal ids
    stay visible and secrets are never written
    ([PII masking](../data-model.md#pii-masking)). AI content (prompts, completions, tool arguments and results) goes only to
    Langfuse, through the content policy
    ([genai.md](genai.md#content)).

## Fields

Attributes per operation, request context and native span fields are defined in
[data-model.md](../data-model.md), each with a class per location
([classification](../data-model.md#classification)):

| Class | On a span |
| --- | --- |
| M | set on every span of that operation |
| C | set when the stated condition holds (a response arrived, a retry, an authenticated user); absent otherwise, never fabricated |
| R | set when the instrumentation provides it |
| O | set only where it is useful for the service |
| P | never set on that span: raw query strings, `Authorization`, `Cookie`, bodies, payloads, parameter values, request context on child spans |

The audit checks the M, C and P rows of [operations](../data-model.md#operations) and
[trace context](../data-model.md#trace-context). Kinds and names in short:

| Operation | Kind | Name |
| --- | --- | --- |
| HTTP server | `SERVER` | `{method} {http.route}`; `{method} unmatched` when no route matched |
| HTTP client | `CLIENT` | `{method} {url.template}` |
| Call to another service | `CLIENT` | `{service.peer.name} {operation}` |
| Database or cache | `CLIENT` | `{db.query.summary}`, or `{db.operation.name} {db.collection.name}` |
| Messaging | `PRODUCER` to publish, `CONSUMER` (root) to process | `send {destination}`, `process {destination}`; batches use links |
| RPC, gRPC | `SERVER` or `CLIENT` | `{rpc.method}` |
| GraphQL operation | `SERVER` or `INTERNAL` | `{graphql.operation.type} {graphql.operation.name}` |
| GraphQL resolver | none | resolvers get no spans |
| Serverless invocation | `SERVER` or `CONSUMER`, root | per the FaaS conventions, with `faas.trigger` |
| GenAI model call or embedding | `CLIENT` | `{gen_ai.operation.name} {gen_ai.request.model}` |
| GenAI agent, tool, retrieval, memory, planning, workflow, guardrail, evaluation | `INTERNAL` (`CLIENT` for a remote store or agent) | `invoke_agent {agent}`, `execute_tool {tool}`, `retrieval {data source}`, `{memory operation}`, `plan {agent}`, `invoke_workflow {name}`, `guardrail {name}`, `evaluate {name}` ([genai](genai.md#step-kinds)) |
| MCP | `CLIENT` on the sender, `SERVER` on the receiver | `{mcp.method.name} {target}`, the target being the tool or prompt name, or the method alone ([mcp](../workloads/mcp.md)) |
| A2A | `CLIENT` on the caller, `SERVER` on the remote agent | `invoke_agent {remote agent}` on the caller, `{a2a method}` on the remote agent ([data model](../data-model.md#a2a-agent-to-agent-protocol)) |
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
| Application function | the function failed: it threw, or returned an error, other than one that maps to a 4xx response | UNSET, a client error included (an exception that maps to 4xx, such as an HTTP exception with a 4xx status; `error.type` optional) |
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
A framework HTTP exception for a client error (FastAPI or Starlette `HTTPException` 4xx, Express
`next(err)` with a 4xx status, Spring `ResponseStatusException` 4xx) is handled: the function
spans it passes through stay UNSET like the SERVER span. Only server failures (5xx or unhandled)
mark spans ERROR.
Inner layers mark their span and rethrow; only the boundary that handles the failure writes the
ERROR log.

### Propagation

- **Propagators** are fixed in code: W3C `tracecontext` and `baggage`.
- **Baggage is allow-listed.** The middleware puts allow-listed incoming keys on its entry span,
  and outbound calls send only allow-listed keys, at most 8 keys and 512 bytes; never secrets,
  authorisation values, request content, the AI trace context or, by default, ids. The
  allow-list is code, not configuration ([enrichment.md](../enrichment.md)). The baggage
  propagator applies it itself, on extract and inject, because libraries write baggage too:
  Langfuse's JS tracing puts `langfuse_trace_id` there and reads `langfuse_*` keys back as span
  attributes ([sdk-notes.md](../sdk-notes.md#genai-and-langfuse)).
- **Messaging** carries the context in message headers; a consumer that processes a batch links
  to each message's context.
- **Browsers:** a backend that serves browsers allows `traceparent`, `tracestate` and `baggage`
  in its CORS policy, so browser and API spans join one trace. The page (Grafana Faro) sends
  them only to the API origins listed in `rum.propagate_trace_origins`, and none for a session
  outside the sample ([rum.md](rum.md#trace-context)).
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
