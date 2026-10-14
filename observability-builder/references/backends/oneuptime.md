# OneUptime

OneUptime ingests OTLP traces, metrics and logs and Pyroscope-protocol profiles, and it is the
standard's target for RUM and session replay. Everything on this page except the browser
integration is configuration: endpoints and keys live in `observability.yml` or the collector.
Check OneUptime's current docs at run time — paths, limits and policy options change.

## Ingestion

| Signal | Endpoint | Protocol | Auth |
| --- | --- | --- | --- |
| Traces, metrics, logs | `<host>/otlp` | OTLP over HTTP, or OTLP/gRPC to the installation's gRPC listener | header `x-oneuptime-token: <ingestion key>` |
| Profiles | `<host>/pyroscope`; clients append `/ingest` or `/push.v1.PusherService/Push` | Pyroscope protocol | `x-oneuptime-token`, `Authorization: Bearer <key>`, or basic auth with the key as password |
| Browser traces, metrics, logs | `<host>/otlp/v1/<signal>`, usually through a same-origin proxy | OTLP over HTTP | added server-side by the proxy, or a browser key limited to allowed origins |
| Session replay | `<host>/telemetry/session-replay/v1/…` | OneUptime's replay protocol, not OTLP | as for browser telemetry |

## Direct or through the collector

| Route | How it works |
| --- | --- |
| Through the collector (default) | Apps send OTLP over gRPC to the collector. Its OneUptime exporter (`otelcol.exporter.otlphttp` with base endpoint `<host>/otlp`, or gRPC) adds `x-oneuptime-token` through `otelcol.auth.headers`, reading the key from a mounted file. The key never reaches the app. |
| Direct | Each signal's yml exporter points at OneUptime and carries the header from a mounted secret file. |

**Ops metrics in direct mode.** They stay on the service's `/metrics` port either way, and
OneUptime gets them only through something that scrapes and forwards them: the collector's
scrape, or a Prometheus that remote-writes. Without one, the platform handoff lists the
scraper, and OneUptime has traces and logs only. Its AI views don't depend on it: they read
spans.

In direct mode with `http/protobuf`, the endpoint is the base URL `<host>/otlp` and the loader
appends `/v1/<signal>` ([config.md](../config.md#exporter-blocks)):

```yaml
tracing:
  exporter:
    endpoint: "${OBS_TRACES_ENDPOINT}"           # https://<host>/otlp
    protocol: http/protobuf
    compression: gzip
    timeout: 10s
    headers:
      x-oneuptime-token: "${file:/var/run/secrets/observability/oneuptime-token}"
```

The collector side, with the key read from a file:

```alloy
local.file "oneuptime_token" {
  filename  = "/var/run/secrets/observability/oneuptime-token"
  is_secret = true
}

otelcol.auth.headers "oneuptime" {
  header {
    key   = "x-oneuptime-token"
    value = local.file.oneuptime_token.content
  }
}

otelcol.exporter.otlphttp "oneuptime" {
  client {
    endpoint = "https://<host>/otlp"
    auth     = otelcol.auth.headers.oneuptime.handler
  }
}

pyroscope.write "oneuptime" {
  endpoint {
    url               = "https://<host>/pyroscope"
    bearer_token_file = "/var/run/secrets/observability/oneuptime-token"
  }
}
```

**Logs path.** With `logging.output: stdout`, logs reach OneUptime only if a collector ships pod
stdout. Where none does — direct mode, or a collector without the log pipeline — include `otlp`
in `logging.output` (`otlp`, or `[stdout, otlp]` to keep local stdout) with a OneUptime
exporter, so the logs and the exceptions built from them still arrive. Never ship the same
records both ways: a pod that sends logs over OTLP is left out of stdout shipping, or each line
arrives twice.

## Services and labels

- The Services catalog is keyed by `service.name`. Keep it stable across releases and replicas:
  a renamed service is a new service, and its history doesn't follow.
- OneUptime turns `oneuptime.label.<key>` resource attributes into project labels; the standard
  doesn't add them, and a team that wants them adds them to its own `observability.yml`. The yml's
  resource reaches only what the SDK sends (traces, OTLP logs and metrics, profiles): stdout logs
  and scraped metrics get their resource from the collector, so the chart also renders each label
  as a `resource.opentelemetry.io/oneuptime.label.<key>` pod annotation, which the collector's
  `k8sattributes` (`otel_annotations = true`) reads ([charts](../charts.md)). Labels common to every
  service can be added once in the collector's OneUptime branch instead (a `resource` processor).
- `deployment.environment.name` and `service.version` separate environments and releases; they
  are never defaulted.

```yaml
resource:
  service:
    name: "${OBS_SERVICE_NAME}"
    namespace: "${OBS_SERVICE_NAMESPACE:-}"
    version: "${OBS_SERVICE_VERSION}"
  environment: "${OBS_ENVIRONMENT}"
  attributes:
    k8s.namespace.name: "${K8S_NAMESPACE_NAME:-}"
    k8s.pod.name: "${K8S_POD_NAME:-}"
    k8s.pod.uid: "${K8S_POD_UID:-}"
    k8s.node.name: "${K8S_NODE_NAME:-}"
```

## Exceptions

OneUptime builds exceptions from three sources:

1. log records carrying `exception.type`, `exception.message` or `exception.stacktrace`, at any
   severity — the standard's ERROR exception record ([signals/errors.md](../signals/errors.md)).
   A warning that carries one of these keys becomes an exception too, so warnings use
   `error.type` and `app.error.message` instead;
2. exception span events with the same attributes;
3. ERROR log records without `exception.*`, whose body it scans for a stack trace — so a
   multi-line stack trace must arrive as one record (the collector's multiline rule).

Report each failure once, through the ERROR log where it is handled; OpenTelemetry is
deprecating span events, so the log record is the primary source. When an instrumentation
library also records a span event for the same exception, check in OneUptime that it
shows one exception, not two.

An exception's environment comes from the older `deployment.environment` resource attribute
only (OneUptime 14), not from `deployment.environment.name`. The applications keep sending the
current name. In direct mode the yml adds the older one beside it,
`deployment.environment: "${OBS_ENVIRONMENT}"` under `resource.attributes`, the one backend
exception to the resource rule ([data-model.md](../data-model.md#resource)). Through the
collector, its OneUptime branch copies it for this destination:

```alloy
otelcol.processor.transform "oneuptime" {
  error_mode = "ignore"
  trace_statements {
    context    = "resource"
    statements = [`set(resource.attributes["deployment.environment"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil and resource.attributes["deployment.environment"] == nil`]
  }
  log_statements {
    context    = "resource"
    statements = [`set(resource.attributes["deployment.environment"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil and resource.attributes["deployment.environment"] == nil`]
  }
  output {
    traces = [otelcol.exporter.otlphttp.oneuptime.input]
    logs   = [otelcol.exporter.otlphttp.oneuptime.input]
  }
}
```

## AI / LLM monitoring

OneUptime's AI / LLM product (Conversations, Calls, Usage, Pricing, and an LLM tab on each
trace) is built from the OpenTelemetry GenAI spans the standard already sends
([signals/genai.md](../signals/genai.md)); the app installs nothing for it. Checked with
OneUptime 14.0.50; 14.0.11 has neither Conversations nor the AI alert types.

**Ingestion.** AI spans arrive with all other traces, on the same endpoint, key and route, and
the GenAI metrics come with the scrape. At ingest OneUptime copies a few values off each AI span
into columns:

| Column | Read from (first match) | What the standard sends |
| --- | --- | --- |
| AI call or not | a provider, an operation, a model, an agent or tool name, or a total token count; as a last resort, any key starting with `gen_ai.`, `llm.` or `traceloop.` | those keys on AI spans only: the ops path removes them from every other span |
| Provider, operation | `gen_ai.provider.name` or `gen_ai.system`; `gen_ai.operation.name`, `llm.request.type` or `openinference.span.kind` | both, on every AI span |
| Models | `gen_ai.request.model`; `gen_ai.response.model` | both |
| Agent, tool | `gen_ai.agent.name`; `gen_ai.tool.name` | on agent and tool steps |
| Tokens | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens` | as the provider reports them |
| Cost | `gen_ai.usage.cost` when present (a 0 included), else an estimate from its price list | the provider's own cost only |
| Conversation | `gen_ai.conversation.id`, then `session.id`, then `langfuse.session.id` | `gen_ai.conversation.id`; AI spans never carry `session.id` |
| User, team | `user.id` or `enduser.id`; `user.email`; `team.id`, `team`, `cost_center` or `department`; on AI spans only | `user.id` and `team.id` on AI spans ([who spent what](../signals/genai.md#who-spent-what)), never an email |

- **Call kinds,** by operation:
  - answer: `chat`, `text_completion`, `generate_content`;
  - agent: `invoke_agent`, `create_agent`, `invoke_workflow`, `plan`;
  - tool: `execute_tool`, MCP `tools/call` included;
  - embedding: `embeddings`;
  - retrieval: `retrieval`, `search_memory`.

  Anything else is "other", a retrieval step without its operation name included.
- **Problems,** found at ingest from the call alone:
  - failed: an ERROR status or `error.type`;
  - refused: a refusal or safety finish reason, or, with content, a stock refusal answer;
  - cut off: `length`, `max_tokens`;
  - empty: no output tokens and no tool call.

  Flagged needs `gen_ai.evaluation.result` events, which the ops path doesn't carry.
- **Usage:**
  - Employee lists `user.id` values. OneUptime built it for staff using AI tools; for a
    customer-facing app it lists the app's users.
  - Team lists `team.id`.
  - Model, Provider and App (the service) need nothing more.

  Spend counts only the spans OneUptime received, so every entry point that can reach a model is
  always sampled ([genai.md](../signals/genai.md#required-behaviour)).

**Conversations and content.** A conversation groups AI spans by `gen_ai.conversation.id`,
across traces. Its title, search and replay text come from content: the newest user message in
`gen_ai.input.messages`, the answer in `gen_ai.output.messages`, tool arguments and results.
These arrive only with the `ops` content destination
([genai.md](../signals/genai.md#destination)). With `langfuse`, OneUptime lists conversations
with timing, models, tokens, cost and problems, but no text. With `ops`:

- **Scrub rules:** OneUptime runs the project's scrub rules and pipelines before it stores a span
  or builds a preview. They are a second layer after the module's redaction, never a replacement
  for it.
- **Access and retention:** content is readable by everyone with access to the project, and kept
  for the project's trace retention. Give AI services that send content a project of their own,
  or restrict access, and set retention there.
- **Size:** large prompts make large spans. `genai.content.max_chars` bounds each attribute, and
  the ingress and collector limits must allow the result.

**Pricing.** The price list covers the large providers' public models, with cache rates.

- **Unpriced models:** a self-hosted, fine-tuned or aliased model (a gateway alias, a deployment
  name) costs 0 until the project adds a custom price on the Pricing tab:
  - a model-id prefix, with input and output prices per million tokens;
  - no cache rate;
  - it applies to calls that arrive after it is saved.
- **Handoff:** the report lists such models for the platform team. Where Langfuse prices the same
  models, its model definitions need them too.

**Correlation.** Each AI call opens the trace it belongs to, with an LLM tab for the call; logs,
metrics and profiles join as everywhere.

## RUM

Browser telemetry follows the RUM rules in [data-model.md](../data-model.md#rum-and-profiling);
OneUptime reads it like this:

- **Classification:** a resource with `browser.language`, `browser.platform` or `browser.mobile`
  is RUM; without them, the browser appears as a backend service. Set them alongside
  `service.name`, `service.version`, `deployment.environment.name`, `browser.brands` and
  `user_agent.original`.
- **RUM application:** OneUptime creates one RUM application per browser `service.name`, and
  session replay looks its policy up by `rum.app_identifier`. Set `rum.app_identifier` to the
  browser's `service.name`: a different identifier creates a second RUM application, and the
  replays land apart from the page loads, fetches and web vitals they belong to.
- **Transport:** OTLP over HTTP. Browsers post to a same-origin proxy (`/telemetry/v1/<signal>`)
  that forwards to `<host>/otlp/v1/<signal>` and adds the key server-side, or straight to
  OneUptime with a browser ingestion key restricted to allowed origins. The proxy accepts only
  the fixed signal routes, from its own origin, with capped body sizes. A server ingestion key
  never ships in a bundle.
- **Spans and logs** carry `app.route` (the route template), `session.id` (the replay session ID)
  and `correlation.id`; fetch and XHR spans carry HTTP client attributes; browser exceptions
  carry `exception.*`.
- **Web vitals:** histograms `web_vital.lcp`, `web_vital.inp`, `web_vital.fcp` and
  `web_vital.ttfb` in milliseconds and `web_vital.cls` as a unitless score, with explicit
  buckets and the dimensions `web_vital.rating` and `app.route` — the route pattern, never the
  URL.
- **Propagation:** `traceparent` goes to same-origin and allow-listed origins only, never
  `/.*/`; backend CORS allows `traceparent`, `tracestate` and `baggage`.

## Session replay

| Piece | Rule |
| --- | --- |
| Loader | `<origin>/telemetry/session-replay/v1/recorder.js` — a small loader that fetches the application's Replay Policy, then loads the recorder |
| Configuration | `window.__ONEUPTIME_SESSION_REPLAY__`, set before the loader runs (or `data-oneuptime-*` attributes on the script tag): `host`, `token`, `appIdentifier`, and options such as `respectDoNotTrack` and `offlineStorage` |
| Command queue | `window.OneUptimeReplayQueue` takes commands before the recorder loads: `onSessionChange` keeps `session.id` on spans and logs in step with the recorder's session; `grantConsent` and `revokeConsent` come from the consent tool |
| Load order | after the OpenTelemetry fetch and XHR instrumentation, so the recorder's session `tracestate` entry is added to same-origin requests and links sessions to backend traces |
| Proxy | a same-origin proxy forwards only the loader, config and chunk routes, adds the key server-side (the browser config then holds a placeholder token), caps body sizes and passes the recorder's bytes through unchanged; CSP then needs only `'self'`, while direct loading needs the OneUptime origin in `script-src` and `connect-src` |
| Masking mode | Replay Policy: sensitive inputs only (default), all inputs, or all text; passwords, card fields, one-time codes and file inputs are always masked |
| Marking | classes `oneuptime-mask` (text masked), `oneuptime-block` (element not recorded) and `oneuptime-ignore` (input events dropped), or the equivalent policy selectors, on PII fields |
| Consent | the consent tool, Global Privacy Control and Do Not Track are honoured |

- Replay starts only when its masking is in place. If the marks or the policy can't be applied,
  replay stays off — fail closed ([safe-capture.md](../safe-capture.md)).
- The skill scans forms for sensitive fields, proposes the selector list for confirmation, marks
  the elements in code, and applies or reports the matching Replay Policy settings. The policy
  lives in OneUptime, not in the yml.

## Profiles

- **Endpoint:** `<host>/pyroscope`, the Pyroscope protocol. Through the collector:
  `pyroscope.receive_http` → `pyroscope.write`. OneUptime 14.0.17 also accepts OTLP profiles
  (the `v1development` schema) at `<host>/otlp/v1/profiles` and over gRPC, keeping the resource
  attributes, but Alloy 1.20 can neither receive nor forward OTLP profiles, and the Pyroscope SDKs
  push their own protocol, so the Pyroscope path stays the default.
- **Formats:** pprof (Go, Node.js, Python and .NET SDKs, the collector's eBPF profiler) and folded
  text. JFR is not accepted, so a Java service converts its JFR snapshots to pprof (below).
- **One sample type per pprof upload:** a combined upload (heap with several sample types, mutex
  plus block) keeps only one. Split multi-type profiles into one upload per type, keeping each
  type's units.
- **Body size:** OneUptime accepts uploads up to 16 MB. Every proxy, ingress or gateway in front
  of `/pyroscope` must accept request bodies of at least 16 MB, or uploads fail with 413.
- **What OneUptime keeps:** only `service_name` (or the application name) from the series
  labels; the other static labels are dropped on both the `/ingest` and the push path, so in
  OneUptime a profile is tied to its service by name alone. Sample labels (`controller`) are kept,
  and `trace_id` and `span_id` sample labels become span links, indexed apart from the labels, so
  a span-profile integration doesn't grow label cardinality here
  ([signals/profiling.md](../signals/profiling.md)).
- **Services and filtering:** a OneUptime service is keyed by project and `service.name` alone, so
  profiles share the service with the app's traces, logs and metrics, and trace → profile links
  work. Its entity key also includes `service.namespace`, which profiles lack, so entity-scoped
  views don't join them. The profile views filter by service, profile type, time and trace only,
  never by cluster, namespace or environment, and two clusters sending the same `service.name`
  into one project share one service. Where clusters or environments must stay apart in profiles,
  give each its own OneUptime project (each collector its own ingestion key); traces, logs and
  metrics can still be filtered by their `k8s.*` and environment attributes within a project.
- **Java:** OneUptime rejects JFR, and `pyroscope-java` 2.x's other format (OTLP profiles) records
  a single event without the service name or labels, posts to a path OneUptime doesn't serve and
  can't pass through Alloy 1.20. Keep the agent on JFR and convert each snapshot to pprof in the
  service (the skill's `assets/java/PprofExporter.java`, set as the agent's exporter): one upload
  per event type (`cpu`, `alloc_space`, `delay`), with the `controller` labels and span ids kept.
  Verified end to end with Spring Boot and Quarkus.
- **Labels:** the application name is `service.name`; the other resource attributes are static
  labels in Pyroscope syntax (dots become underscores: `service_namespace`, `service_version`,
  `deployment_environment_name`, `k8s_namespace_name`, `k8s_pod_name`, `k8s_pod_uid`,
  `k8s_node_name`, and `k8s_cluster_name` when set); `trace_id` and `span_id` sample labels
  come only from a supported span-profile integration and link spans to profiles. Other labels
  stay bounded ([signals/profiling.md](../signals/profiling.md)).

## Databases

Database-engine telemetry — an exporter's `/metrics` or a collector receiver — attaches to a
OneUptime database entry:

- by `oneuptime.database.server.id`, the entry's ID, stamped by the database-engine pipeline; or
  by endpoint, when `server.address` and `server.port` match an endpoint the entry owns;
- that pipeline drops the scraper's `service.name` and `service.instance.id` — with a
  `service.name`, the data is routed to a service instead — and sets `db.system.name`,
  `server.address` and `server.port` ([collector](../collector/README.md#database-engines));
- it runs apart from application pipelines: a database ID is never stamped onto application
  telemetry;
- application CLIENT spans with `db.system.name`, `server.address` and `server.port` attach to
  the database that owns that endpoint automatically.

OneUptime's built-in database views expect the collector receivers' metric names; an exporter's
metrics may need custom charts.

## Queues

- OneUptime discovers queues from messaging spans (`messaging.system` and
  `messaging.destination.name` on PRODUCER, CONSUMER, CLIENT and INTERNAL spans) and from metric
  **datapoint** attributes: `messaging.system` plus `messaging.destination.name`, or the
  broker's own dimension (Kafka `topic`, RabbitMQ `rabbitmq.queue.name`).
- Keep these as datapoint attributes — never move them to the resource — and keep the broker's
  dimensions when converting broker metrics.

[Documentation index](../README.md)
