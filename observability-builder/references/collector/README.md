# Collector (Grafana Alloy)

Grafana Alloy is the standard's collector. Applications hand their telemetry to it and stay
backend-neutral; the collector matches telemetry to its pod, ships stdout logs, scrapes
`/metrics`, relays profiles, covers uninstrumented workloads with eBPF, and adapts every signal
to each backend's auth, protocol and temporality ([backends](../backends/README.md)).

Importable modules for `import.git`, pinned to a library tag, will be published in `alloy/`
(planned). Until then, build the pipelines from this page.

## The application contract

| The application provides | How | Collector side |
| --- | --- | --- |
| OTLP traces, metrics and logs | gRPC on 4317 (HTTP on 4318) | `otelcol.receiver.otlp` |
| Prometheus `/metrics` | a named metrics port, for example `http-metrics` on 9464 | `prometheus.scrape` through a ServiceMonitor, PodMonitor or pod discovery, unless the cluster's Prometheus scrapes it |
| Logs on stdout | one `json` or `plain` record per line; stack traces may span lines | pod log tailing and parsing |
| Profiles | Pyroscope protocol to the profiles port the yml names (4040 by convention) | `pyroscope.receive_http` |
| Identity | `resource.opentelemetry.io/*` pod annotations and downward-API env, plus a cluster-name env where several clusters share a backend ([charts.md](../charts.md)) | `k8sattributes` with OpenTelemetry annotations |

Backend credentials live in the collector, not in the application — direct mode aside
([config.md](../config.md)).

## Pipelines

```text
OTLP from apps         ─► otelcol.receiver.otlp
/metrics (Prometheus)  ─► prometheus.scrape ─► otelcol.receiver.prometheus
pod stdout             ─► loki.source.kubernetes ─► loki.process ─► otelcol.receiver.loki
uninstrumented pods    ─► beyla.ebpf
          │
          ▼  all four join one pipeline
memory_limiter ─► k8sattributes ─► transform ─► batch ─► exporters + auth

database engines       ─► scrape or receiver ─► transform (database identity) ─► batch ─► exporters

SDK profiles           ─► pyroscope.receive_http ─┐
node CPU (eBPF)        ─► pyroscope.ebpf ─────────┴─► pyroscope.write, one per destination
```

### OTLP pipeline

| Stage | Component | Rule |
| --- | --- | --- |
| Receive | `otelcol.receiver.otlp` | gRPC on 4317 and HTTP on 4318, reachable inside the cluster only |
| Limit memory | `otelcol.processor.memory_limiter` | the first processor; `limit_percentage` and `spike_limit_percentage` of the container's memory, so it triggers below the container limit; over the limit it refuses data, and senders retry or drop — applications never block |
| Kubernetes identity | `otelcol.processor.k8sattributes` | `otel_annotations = true` in `extract`, so `resource.opentelemetry.io/*` annotations set `service.*`, `deployment.environment.name` and any extra attribute the chart annotates, such as a backend's label attributes (without it, a well-known label such as `app.kubernetes.io/instance` can win for `service.name`); extracts only `k8s.namespace.name`, `k8s.pod.name`, `k8s.pod.uid` and `k8s.node.name`; pods matched by `k8s.pod.uid`, then by the connection's IP. It can't provide `k8s.cluster.name`: where several clusters send to one backend, that comes from the app's env, set by the chart per cluster, and each cluster's collector adds it to what it collects itself (scraped metrics, file logs) with a `resource` processor (`action: upsert`) set from the same per-cluster value. Browser RUM bypasses it: a frontend's same-origin proxy forwards browser OTLP, so the connection's IP is the proxy pod's and would stamp that pod onto every browser; route resources with `browser.*` attributes around this processor |
| Host and cloud identity | none | application telemetry carries only the standard's resource attributes, the eight plus `k8s.cluster.name` where it is used ([data-model.md](../data-model.md#resource)), so its pipelines run no `resourcedetection` |
| Normalise | `otelcol.processor.transform` | maps legacy names and native fields; never masks or redacts — safe capture happens in code ([safe-capture.md](../safe-capture.md)) |
| Batch | `otelcol.processor.batch` | the last processor before the exporters |
| Export | `otelcol.exporter.otlp` or `otelcol.exporter.otlphttp`, one per destination | auth through `otelcol.auth.headers`, `otelcol.auth.basic`, `otelcol.auth.bearer` or `otelcol.auth.sigv4`, or mTLS; secrets from mounted files or env; a bounded sending queue with retries; several destinations per signal allowed; `otelcol.processor.cumulativetodelta` only in a delta backend's branch |

Two settings that are easy to get wrong:

```alloy
otelcol.processor.memory_limiter "default" {
  check_interval         = "1s"
  limit_percentage       = 80   // of the memory available to the container
  spike_limit_percentage = 20
  output {
    traces  = [otelcol.processor.k8sattributes.default.input]
    metrics = [otelcol.processor.k8sattributes.default.input]
    logs    = [otelcol.processor.k8sattributes.default.input]
  }
}

otelcol.processor.k8sattributes "default" {
  extract {
    metadata = [
      "k8s.namespace.name", "k8s.pod.name", "k8s.pod.uid", "k8s.node.name",
    ]
    otel_annotations = true
  }
  pod_association {
    source {
      from = "resource_attribute"
      name = "k8s.pod.uid"
    }
  }
  pod_association {
    source {
      from = "connection"
    }
  }
  output {
    traces  = [otelcol.processor.transform.default.input]
    metrics = [otelcol.processor.transform.default.input]
    logs    = [otelcol.processor.transform.default.input]
  }
}
```

### Scraped metrics

- Targets come from ServiceMonitors and PodMonitors (`prometheus.operator.servicemonitors`,
  `prometheus.operator.podmonitors`) or from `discovery.kubernetes`, limited to the named
  metrics port. One scraper per target: where the cluster's Prometheus already scrapes the
  ServiceMonitor, the collector doesn't.
- Scrape the Prometheus text format the client libraries serve, then convert with
  `otelcol.receiver.prometheus` into the OTLP pipeline. Set `honor_metadata = true` on
  `prometheus.scrape` so each metric's `TYPE` reaches the bridge. Without it every series
  arrives as a gauge: a histogram becomes separate `_bucket`, `_sum` and `_count` gauges and a
  counter loses its monotonic sum, so backend percentiles and rates are wrong. In Alloy 1.20 the
  argument is experimental and needs `--stability.level=experimental` on `alloy run`.
- The bridge names the resource after the scrape job and target (`service.name` from the job,
  `service.instance.id` from the address, plus `net.host.name`, `net.host.port` and `http.scheme`,
  or `server.address`, `server.port` and `url.scheme` in newer versions); replace those with the
  pod's annotated identity, so scraped metrics carry the same resource as the SDK's telemetry
  ([data-model.md](../data-model.md)).
- Names, labels, types and buckets pass through unchanged
  ([signals/metrics.md](../signals/metrics.md)).

### Pod logs

| Step | Rule |
| --- | --- |
| Source | container stdout and stderr: `loki.source.kubernetes` through the Kubernetes API, or node log files (`loki.source.file`, `otelcol.receiver.filelog`) from an instance on every node |
| Exclude | pods that send their logs over OTLP, and the collector's own pods |
| Parse | `json`: decode the line; `plain`: the standard's fixed prefix pattern ([line formats](../signals/app-logs.md#line-formats)); strip any colour codes |
| Multiline | `plain`: a record starts at a line that begins with a timestamp, and the lines after it (a stack trace) join it; `json` records are single lines |
| Native fields | `time` → timestamp, `level` → severity number and text, `trace_id`, `span_id` and `trace_flags` → the native fields (left as attributes, they don't correlate), `message` → body, `logger` → scope name, `event_name` → event name, other keys → attributes |
| Collector noise | `otelcol.receiver.loki` adds `log.file.name`, `log.file.path` (a node path) and `loki.attribute.labels` to every record; delete them with the source labels once they have been mapped |
| Identity | the pod's resource, from the same annotations as SDK telemetry, extra attributes such as backend labels included |
| Export | into the OTLP pipeline; `otelcol.receiver.loki` bridges the `loki.*` stages |

The `json` format, mapped in Alloy (tested with Alloy 1.20 against OneUptime). The source's
labels (the pod's identity) become record attributes first; an `otelcol.processor.groupbyattrs`
on the identity keys then moves them to the resource:

```alloy
loki.process "lines" {
  stage.json {
    expressions = { time = "time" }
  }
  stage.timestamp {
    source            = "time"
    format            = "RFC3339Nano"
    action_on_failure = "skip"
  }
  forward_to = [otelcol.receiver.loki.lines.receiver]
}

otelcol.processor.transform "lines" {
  error_mode = "ignore"
  log_statements {
    context    = "log"
    statements = [
      `merge_maps(log.cache, ParseJSON(log.body), "upsert") where IsMatch(log.body, "^\\s*[{]") and IsMatch(log.body, "\"level\"") and IsMatch(log.body, "\"message\"")`,
      `set(log.severity_text, ToUpperCase(log.cache["level"])) where IsString(log.cache["level"])`,
      `set(log.severity_number, SEVERITY_NUMBER_DEBUG) where log.severity_text == "DEBUG"`,
      `set(log.severity_number, SEVERITY_NUMBER_INFO) where log.severity_text == "INFO"`,
      `set(log.severity_number, SEVERITY_NUMBER_WARN) where log.severity_text == "WARN"`,
      `set(log.severity_number, SEVERITY_NUMBER_ERROR) where log.severity_text == "ERROR"`,
      `set(log.severity_number, SEVERITY_NUMBER_FATAL) where log.severity_text == "FATAL"`,
      `set(log.trace_id.string, log.cache["trace_id"]) where IsString(log.cache["trace_id"])`,
      `set(log.span_id.string, log.cache["span_id"]) where IsString(log.cache["span_id"])`,
      `set(log.flags, 1) where IsString(log.cache["trace_flags"]) and IsMatch(log.cache["trace_flags"], "^[0-9a-f][13579bdf]$")`,
      `set(log.event_name, log.cache["event_name"]) where IsString(log.cache["event_name"])`,
      `set(instrumentation_scope.name, log.cache["logger"]) where IsString(log.cache["logger"])`,
      `set(log.body, log.cache["message"]) where IsString(log.cache["message"])`,
      `delete_matching_keys(log.cache, "^(time|level|message|logger|trace_id|span_id|trace_flags|event_name)$")`,
      `merge_maps(log.attributes, log.cache, "upsert")`,
    ]
  }
  output { logs = [otelcol.processor.groupbyattrs.lines.input] }
}
```

The `plain` format (tested with Alloy 1.20 against OneUptime, Java stack traces included) needs
two additions. The multiline stage runs only on the plain sources, a separate `loki.process`
fed by the pods or files that write `plain`: on a JSON source it would join every line into one
record, because JSON lines don't start with a timestamp.

```alloy
loki.process "plain_lines" {
  stage.multiline {
    firstline     = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}"
    max_wait_time = "3s"
    max_lines     = 500
  }
  stage.regex {
    expression = "^(?P<time>\\d{4}-\\d{2}-\\d{2}T\\S+Z) "
  }
  stage.timestamp {
    source            = "time"
    format            = "RFC3339Nano"
    action_on_failure = "skip"
  }
  forward_to = [otelcol.receiver.loki.lines.receiver]
}
```

In the same transform, before the shared native-field statements, the plain line fills
`log.cache` with the same keys the JSON line does. Every statement is guarded by the line's own
prefix, so a JSON record whose attributes happen to use these key names is never touched.
Escaped `\\` and `\"` become placeholders before `ParseKeyValue` (which splits on quotes but
doesn't unescape) and are restored after; the shared statements then set severity, ids, flags,
event name, scope and body:

```alloy
`merge_maps(log.cache, ExtractPatterns(log.body, "^(?P<time>\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z) (?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL) (?P<trace_id>[0-9a-f]{32}|-) (?P<span_id>[0-9a-f]{16}|-) (?P<trace_flags>[0-9a-f]{2}|-) (?P<logger>\\S+) \"(?P<message>(?:[^\"\\\\\\n]|\\\\.)*)\"(?: (?P<plain_attributes>[^\\n]*))?(?:\\n(?P<plain_stack>(?s:.*)))?$"), "upsert") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`set(log.attributes["exception.stacktrace"], log.cache["plain_stack"]) where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ") and log.cache["plain_stack"] != ""`,
`replace_pattern(log.cache["message"], "\\\\\\\\", "\u0002") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_pattern(log.cache["message"], "\\\\\"", "\u0001") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_pattern(log.cache["plain_attributes"], "\\\\\\\\", "\u0002") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_pattern(log.cache["plain_attributes"], "\\\\\"", "\u0001") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`merge_maps(log.cache, ParseKeyValue(log.cache["plain_attributes"]), "insert") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ") and log.cache["plain_attributes"] != ""`,
`replace_all_patterns(log.cache, "value", "\\\\n", "\n") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_all_patterns(log.cache, "value", "\\\\r", "\r") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_all_patterns(log.cache, "value", "\u0001", "\"") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`replace_all_patterns(log.cache, "value", "\u0002", "\\") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
`delete_key(log.cache, "trace_id") where log.cache["trace_id"] == "-"`,
`delete_key(log.cache, "span_id") where log.cache["span_id"] == "-"`,
`delete_key(log.cache, "trace_flags") where log.cache["trace_flags"] == "-"`,
`delete_matching_keys(log.cache, "^plain_(attributes|stack)$") where IsMatch(log.body, "^\\d{4}-\\d{2}-\\d{2}T\\S+Z (TRACE|DEBUG|INFO|WARN|ERROR|FATAL) ")`,
```

`ParseKeyValue` runs in `insert` mode so the line's fixed fields win over an attribute of the
same name.

### Database engines

- Engine metrics — an exporter scrape or a receiver — run in their own pipeline and are never
  merged with application telemetry.
- Its `transform` deletes the scrape's `service.name` and `service.instance.id` and sets
  `db.system.name`, `server.address`, `server.port` and any backend-specific identity, such as
  OneUptime's `oneuptime.database.server.id` ([OneUptime](../backends/oneuptime.md#databases)).
- A database identity is never stamped onto application telemetry.

### Profiles

- `pyroscope.receive_http` listens on the profiles port and forwards SDK uploads to one
  `pyroscope.write` per destination, each with that destination's auth.
- `pyroscope.ebpf` (an instance on every node, with host PID and the eBPF privileges) profiles
  CPU for every pod. Its targets come from `discovery.kubernetes`, with `service_name` taken from
  the pod's `resource.opentelemetry.io/service.name` annotation, so profiles match the other
  signals ([signals/profiling.md](../signals/profiling.md)).
- Profiles travel over the Pyroscope protocol; check the collector's current support before
  routing OTLP profiles through it.

### eBPF instrumentation

- `beyla.ebpf` (OpenTelemetry eBPF Instrumentation, OBI) traces HTTP, gRPC and database calls
  and produces RED metrics for workloads without an SDK — third-party images and legacy
  services.
- Select workloads explicitly (namespace, labels or annotations) and exclude every
  SDK-instrumented workload, the collector itself and the telemetry ports; don't rely on
  automatic detection alone.
- Its traces join the OTLP pipeline at the memory limiter; its metrics are scraped from the
  targets it exports.
- It needs elevated capabilities and host PID, and it can't see non-standard HTTP stacks.

### Path-exclusion fallback

Where an SDK or agent can't drop successful probes itself, or `trace_failed_probes` is on and
every probe span is recorded, `otelcol.processor.filter` drops the successful ones, in the
pipelines of those workloads only. Match the service's `tracing.exclude_paths` exactly (an
anchored pattern; an entry ending in `/*` becomes a prefix such as `^/api-docs/`) and successes
only, so `/api/healthcare` and failed probes are kept:

```alloy
otelcol.processor.filter "probes" {
  error_mode = "ignore"
  traces {
    span = [
      `kind == SPAN_KIND_SERVER and IsMatch(attributes["url.path"], "^/(health|ready|live|metrics|status)$") and attributes["http.response.status_code"] < 400`,
    ]
  }
  output {
    traces = [otelcol.processor.batch.default.input]
  }
}
```

The filter sees one span at a time: a dropped probe's child spans (a dependency check) are left
as orphans unless they match too, so excluding at the source is preferred.

### GenAI routing

For services that send their AI spans to Langfuse through the collector
(`genai.langfuse.export: collector`, with mode `hybrid` or `otel`; [signals/genai.md](../signals/genai.md),
[backends/langfuse.md](../backends/langfuse.md)). The app exports as usual, content and the
feature stamps included; the collector splits each trace into an ops copy, with content only
for the services whose content destination is `ops`, and the AI part for Langfuse. Raise the
receiver's gRPC `max_recv_msg_size` (default 4 MiB) when the spans carry content. Content
crosses the cluster in clear between the app and the collector.

**Option A, stateless (the default).** The traces leaving the normalise step feed two branches.
The ops branch:

- drops Langfuse event spans, experiment runs and `gen_ai.*` span events;
- gives AI spans their user, team and conversation from the stamps, and their error type as the
  status description;
- keeps only the allow-list on AI spans, and removes the AI keys from every other span;
- deletes every `langfuse.*` key, and every content key except on the AI spans of the services
  whose content destination is `ops` ([genai](../signals/genai.md#destination)).

The Langfuse branch keeps the stamped spans of AI features and the spans of experiment items,
minus non-AI client and producer spans, and drops the content of the `ops` services. Both
branches list those services by `service.name`, in the same regex (`^$` while there are none):

```alloy
// Ops branch. The transform's traces output replaces the ops path's batch input.
otelcol.processor.filter "ops_genai" {
  error_mode = "ignore"
  traces {
    span = [
      `attributes["langfuse.observation.type"] == "event"`,
      `attributes["langfuse.experiment.id"] != nil`,   // experiment runs are evaluation data, not operations
    ]
    spanevent = [`IsMatch(name, "^gen_ai\\.")`]
  }
  output { traces = [otelcol.processor.transform.ops_genai.input] }
}

otelcol.processor.transform "ops_genai" {
  error_mode = "ignore"
  trace_statements {
    context    = "span"
    statements = [
      // Mark AI spans for the statements below; the mark goes again at the end.
      `set(span.attributes["observability.ai_span"], true) where span.attributes["gen_ai.operation.name"] != nil or span.attributes["langfuse.observation.type"] != nil or span.attributes["openinference.span.kind"] != nil or span.attributes["llm.request.type"] != nil or span.attributes["traceloop.span.kind"] != nil`,
      // AI spans: the error type replaces the status description, where instrumentations put the
      // provider's message (it can quote the input). Before the allow-list, which drops the type keys.
      `set(span.status.message, span.attributes["error.type"]) where span.status.message != "" and span.attributes["error.type"] != nil and span.attributes["observability.ai_span"] == true`,
      `set(span.status.message, "") where span.status.message != "" and span.attributes["error.type"] == nil and span.attributes["observability.ai_span"] == true`,
      // AI spans: the spender, team and conversation from the stamps, where LLM views read them.
      `set(span.attributes["user.id"], span.attributes["langfuse.user.id"]) where span.attributes["observability.ai_span"] == true and span.attributes["langfuse.user.id"] != nil`,
      `set(span.attributes["team.id"], span.attributes["langfuse.trace.metadata.team"]) where span.attributes["observability.ai_span"] == true and span.attributes["langfuse.trace.metadata.team"] != nil`,
      `set(span.attributes["gen_ai.conversation.id"], span.attributes["langfuse.session.id"]) where span.attributes["observability.ai_span"] == true and span.attributes["gen_ai.conversation.id"] == nil and span.attributes["langfuse.session.id"] != nil`,
      // AI spans keep only the allow-list and the GenAI conventions' content keys, so an unknown
      // content key can't pass; the content keys go again below unless the destination is ops.
      `keep_matching_keys(span.attributes, "^(gen_ai\\.(operation\\.name|provider\\.name|request\\.(model|temperature|top_p|top_k|max_tokens|frequency_penalty|presence_penalty|seed|choice\\.count|encoding_formats|stream)|response\\.(id|model|finish_reasons|time_to_first_chunk)|usage\\..+|output\\.type|agent\\.(id|name)|tool\\.(name|type|definitions|call\\.(id|arguments|result))|conversation\\.id|data_source\\.id|retrieval\\.(top_k|documents|query\\.text)|memory\\.(store\\.id|record\\.(id|count)|query\\.text|records)|input\\.messages|output\\.messages|system_instructions|workflow\\.name|prompt\\.name|evaluation\\.(name|score\\..+))|openinference\\.span\\.kind|llm\\.(model_name|provider|system|token_count\\..+|request\\.type|usage\\.total_tokens)|traceloop\\.span\\.kind|mcp\\.(method\\.name|protocol\\.version|session\\.id|resource\\.uri)|jsonrpc\\.(request\\.id|protocol\\.version)|rpc\\.response\\.status_code|network\\.(transport|protocol\\.(name|version))|client\\.(address|port)|user\\.id|team\\.id|error\\.type|server\\..+|service\\.peer\\.name|code\\..+|thread\\.name|app\\..+|observability\\.ai_span)$") where span.attributes["observability.ai_span"] == true`,
      // Other spans: no AI keys, which would make OneUptime count them as AI calls.
      `delete_matching_keys(span.attributes, "^(gen_ai|llm|traceloop)\\..*") where span.attributes["observability.ai_span"] == nil`,
      // Every span: no stamps, and no content except on the AI spans of the services whose
      // genai.content.destination is ops, matched by service.name ("^$" while there are none).
      `delete_matching_keys(span.attributes, "^langfuse\\..*")`,
      `delete_matching_keys(span.attributes, "^(gen_ai\\.(input\\.messages|output\\.messages|system_instructions|tool\\.definitions|tool\\.call\\.(arguments|result)|retrieval\\.(documents|query\\.text)|memory\\.(query\\.text|records)|evaluation\\.explanation|prompt|completion)|gen_ai\\.prompt\\.[0-9]+\\..*|gen_ai\\.prompt\\.variable.*|gen_ai\\.completion\\..*|traceloop\\.entity\\.(input|output)|input\\.value|output\\.value|llm\\.(input|output)_messages\\..*|llm\\.prompts\\..*|llm\\.prompt_template\\.(template|variables)|llm\\.tools\\..*|retrieval\\.documents\\..*|reranker\\..*|embedding\\.embeddings\\..*|ai\\.prompt.*|ai\\.response\\.(text|object|toolCalls)|ai\\.toolCall\\.(args|result))$") where not (span.attributes["observability.ai_span"] == true and IsMatch(resource.attributes["service.name"], "^$"))`,
      `delete_key(span.attributes, "observability.ai_span")`,
    ]
  }
  output { traces = [otelcol.processor.batch.default.input] }
}

// Langfuse branch: the stamped spans only. Services on the app route strip their stamps
// before export, so their spans never reach Langfuse twice.
otelcol.processor.filter "langfuse" {
  error_mode = "ignore"
  traces {
    span = [
      // Neither an AI feature's span nor an experiment item's (Langfuse's own runner sets no trace name).
      `attributes["langfuse.trace.name"] == nil and attributes["langfuse.experiment.id"] == nil`,
      // scope ai_context; for ai_only, drop every span without a GenAI operation or type instead.
      `(kind == SPAN_KIND_CLIENT or kind == SPAN_KIND_PRODUCER) and attributes["gen_ai.operation.name"] == nil`,
    ]
  }
  output { traces = [otelcol.processor.transform.langfuse_content.input] }
}

// The services whose content destination is ops (the same list as the ops branch's) send their
// AI steps to Langfuse without content.
otelcol.processor.transform "langfuse_content" {
  error_mode = "ignore"
  trace_statements {
    context    = "span"
    statements = [
      `delete_matching_keys(span.attributes, "^(gen_ai\\.(input\\.messages|output\\.messages|system_instructions|tool\\.definitions|tool\\.call\\.(arguments|result)|retrieval\\.(documents|query\\.text)|memory\\.(query\\.text|records)|evaluation\\.explanation|prompt|completion)|gen_ai\\.prompt\\.[0-9]+\\..*|gen_ai\\.prompt\\.variable.*|gen_ai\\.completion\\..*|traceloop\\.entity\\.(input|output)|input\\.value|output\\.value|llm\\.(input|output)_messages\\..*|llm\\.prompts\\..*|llm\\.prompt_template\\.(template|variables)|llm\\.tools\\..*|retrieval\\.documents\\..*|reranker\\..*|embedding\\.embeddings\\..*|ai\\.prompt.*|ai\\.response\\.(text|object|toolCalls)|ai\\.toolCall\\.(args|result))$") where IsMatch(resource.attributes["service.name"], "^$")`,
    ]
  }
  output { traces = [otelcol.processor.batch.langfuse.input] }
}

otelcol.processor.batch "langfuse" {
  output { traces = [otelcol.exporter.otlphttp.langfuse.input] }
}
```

The Langfuse exporter and its auth: [backends/langfuse.md](../backends/langfuse.md#collector-route).
Where content is off (production), put the same two status statements in a transform before the
Langfuse branch's batch: on this route the app can't replace an instrumentation's status
description.

**Option B, whole traces (opt in).** Option A sends only each service's own AI spans. To show
upstream services in Langfuse too (a gateway or BFF in front of the AI service), every agent
routes traces by trace id to a gateway that waits for whole traces and keeps those with a
stamped span:

```alloy
// Agent: the ops branch as in option A; the Langfuse side routes by trace id.
otelcol.processor.filter "langfuse_client_spans" {
  error_mode = "ignore"
  traces {
    span = [`(kind == SPAN_KIND_CLIENT or kind == SPAN_KIND_PRODUCER) and attributes["gen_ai.operation.name"] == nil`]
  }
  output { traces = [otelcol.exporter.loadbalancing.langfuse_gateway.input] }
}

otelcol.exporter.loadbalancing "langfuse_gateway" {
  routing_key = "traceID"
  resolver {
    kubernetes {
      service = "langfuse-gateway.observability"   // the gateway's headless service
      ports   = [4317]
    }
  }
  protocol {
    otlp {
      client {
        tls { insecure = true }   // in-cluster; use TLS where the cluster requires it
      }
    }
  }
}

// Gateway: whole traces that contain a stamped span, from every service.
otelcol.receiver.otlp "langfuse_gateway" {
  grpc {
    endpoint          = "0.0.0.0:4317"
    max_recv_msg_size = "16MiB"
  }
  output { traces = [otelcol.processor.tail_sampling.langfuse.input] }
}

otelcol.processor.tail_sampling "langfuse" {
  decision_wait = "60s"   // longer than the slowest AI request
  policy {
    name = "ai-features"
    type = "ottl_condition"
    ottl_condition {
      error_mode = "ignore"
      span       = [`attributes["langfuse.trace.name"] != nil`]
    }
  }
  output { traces = [otelcol.processor.batch.langfuse.input] }
}
```

The gateway runs at least two replicas with memory for every trace in flight during
`decision_wait`, content included; when replicas change, the agents reshard and traces in
flight split. The resolver needs read access to the service's endpoints. In a verification run
the upstream service's span arrived as the Langfuse trace's root, the AI service's spans kept
their user and session, and traces without an AI feature were dropped.

## Deduplication

| Overlap | Rule |
| --- | --- |
| SDK and eBPF traces | a workload is traced by its SDK or by `beyla.ebpf`, never both |
| SDK and eBPF profiles | pods that profile themselves are excluded from `pyroscope.ebpf`, or don't run SDK CPU profiling where eBPF covers it |
| Push and scrape | an instrument is either scraped from `/metrics` or pushed over OTLP, never both; one scraper per target |
| OTLP logs and tailed stdout | a pod whose `logging.output` includes `otlp` is excluded from log tailing, so each record arrives once |
| Langfuse from the app and from the collector | a service sends its AI spans to Langfuse one way: the app route strips the feature stamps before export, and the collector's Langfuse branch keeps only stamped spans |

## Running it

- **Memory:** size the limiter by percentage so it always triggers below the container's memory
  limit. An absolute limit above the container limit never triggers, and the collector is
  killed instead.
- **Placement:** pod logs, `pyroscope.ebpf` and `beyla.ebpf` need an instance on every node;
  OTLP reception can be node-local or central.
- **RBAC:** `k8sattributes`, pod discovery and ServiceMonitor discovery need read access to
  pods, namespaces, nodes and the monitor resources. Without it, identity enrichment and
  discovery fail.
- **Self-monitoring:** the collector's own metrics (refused, dropped and failed sends, queue
  size) are scraped like any other target, so drops are visible.
- **Failure behaviour:** the collector queues, retries and then drops; applications never wait
  on it ([acceptance.md](../acceptance.md)).

Later: tail sampling beyond the GenAI gateway, and span metrics.

[Documentation index](../README.md)
