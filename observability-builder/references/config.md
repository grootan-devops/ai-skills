# Configuration: observability.yml

One file per service holds its observability settings: who the service is, where each signal
goes, and which signals and options are on. The JSON Schema is
[`assets/observability.schema.json`](../assets/observability.schema.json).

## Purpose

`observability.yml` is a thin wrapper over OpenTelemetry, not a new layer. At start-up a small
loader in the service's language, part of the observability module ([layout.md](layout.md)):

1. reads the file and resolves its `${...}` references;
2. writes the standard OpenTelemetry settings (`OTEL_*` environment variables, or SDK options
   where a runtime has no variable) before the SDK, agent or framework starts;
3. steps aside: OpenTelemetry does the rest unchanged.

Only the features OpenTelemetry has no setting for read their own sections: log format, level
and slow-step threshold, the access log, path exclusions, the `/metrics` server, profiling,
RUM, and GenAI's content policy and Langfuse destination. Agents that read the environment
before any application code runs (the Java agent, `opentelemetry-instrument`) get their values
from the loader running in the container entrypoint first, which then starts the process with
the variables it wrote.

The file holds only connection details, static attributes, toggles and the access-log and
exclusion lists. `resource` is shared; each signal section (`tracing`, `metrics`, `logging`,
`profiling`, `rum`) is self-contained, with its own switch, exporter and options. `genai` adds
LLM and agent telemetry on top of `tracing` and `metrics`.

## File location

| Context | Where the loader looks |
| --- | --- |
| `OBSERVABILITY_CONFIG` is set | that path |
| Local run | `observability.yml` in the working directory, the repository root |
| Kubernetes | a ConfigMap mounted at `/etc/observability/observability.yml`, with `OBSERVABILITY_CONFIG` pointing at it; secrets are files from a mounted Secret, read through `${file:...}` ([charts.md](charts.md)) |

## Annotated example

Values shown are the defaults, except where a comment names the default or marks a key
required. `resource` has no defaults.

```yaml
version: 1                                   # file format version, a literal

resource:                                    # who is sending; shared by every signal
  service:
    name: "${OBS_SERVICE_NAME}"              # required; stable across releases and replicas
    namespace: "${OBS_SERVICE_NAMESPACE:-}"  # the application group: the deployment's partOf name
    version: "${OBS_SERVICE_VERSION}"        # required; from the build, never a default
  environment: "${OBS_ENVIRONMENT}"          # required; becomes deployment.environment.name
  attributes:                                # the pod identity from the downward API, plus the
                                             #   cluster's name where it is used; nothing else
    k8s.namespace.name: "${K8S_NAMESPACE_NAME:-}"   # empty values are dropped (local runs)
    k8s.pod.name: "${K8S_POD_NAME:-}"
    k8s.pod.uid: "${K8S_POD_UID:-}"
    k8s.node.name: "${K8S_NODE_NAME:-}"
    k8s.cluster.name: "${K8S_CLUSTER_NAME:-}"       # optional: several clusters, one backend;
                                                    #   set by the chart per cluster

tracing:
  enabled: true
  exporter:                                  # OTLP exporter block, same shape for every signal
    endpoint: "${OBS_TRACES_ENDPOINT:-http://alloy:4317}"   # default http://localhost:4317
    protocol: grpc                           # grpc | http/protobuf
    compression: gzip                        # gzip | none
    timeout: 10s
    insecure: false
    tls: {}                                  # ca_file, cert_file, key_file: mounted PEM paths
    headers: {}                              # backend auth when sending direct
  sampler:
    name: parentbased_traceidratio           # default parentbased_always_on; any standard or
    arg: "1.0"                               #   SDK-registered sampler; arg: a string, no default
  exclude_paths: [/health, /ready, /live, /metrics, /status]   # successful responses only;
                                             #   add the API docs paths, e.g. /api-docs, /api-docs/*
  trace_failed_probes: false                 # true keeps the traces of failed probes

metrics:
  enabled: true
  prometheus:                                # pull: the ops metrics on their own port, from the
    enabled: true                            #   stack's Prometheus client library
    host: 0.0.0.0
    port: 9464
    path: /metrics
    exclude_paths: [/health, /ready, /live, /metrics, /status]   # failures are still counted;
                                             #   add the API docs paths
  otlp:                                      # push: off; a team may turn it on for app metrics
    enabled: false
    interval: 60s
    temporality: cumulative                  # cumulative | delta | lowmemory
    exporter:
      endpoint: "${OBS_METRICS_ENDPOINT:-http://alloy:4317}"  # default http://localhost:4317
      protocol: grpc
      compression: gzip
      timeout: 10s
      insecure: false
      headers: {}

logging:
  format: json                               # json | plain
  output: stdout                             # stdout | otlp | [stdout, otlp]
  exporter:                                  # used when the output includes otlp
    endpoint: "${OBS_LOGS_ENDPOINT:-http://alloy:4317}"     # default http://localhost:4317
    protocol: grpc
    compression: gzip
    timeout: 10s
    insecure: false
    headers: {}
  application:
    enabled: true
    level: info                              # trace | debug | info | warn | error | fatal
    slow_threshold: 2s                       # default: none; a wrapped function slower than
                                             #   this writes a WARN step log
  access_log:
    enabled: true
    exclude_paths: [/health, /ready, /live, /metrics, /status]   # failures still logged;
                                             #   add the API docs paths
    request:
      headers: []                            # lowercase names, e.g. [content-type, accept-language]
      cookies: []                            # cookie names; never credential cookies
      body:
        fields: []                           # selected JSON fields, e.g. [amount, currency]
        full: false                          # last resort: bounded, temporary, explicit
        max_bytes: 4096
    response:
      headers: []                            # e.g. [content-type]
      body:
        fields: []
        full: false
        max_bytes: 4096

profiling:
  enabled: false
  exporter:
    endpoint: "${OBS_PROFILES_ENDPOINT:-http://alloy:4040}" # default http://localhost:4040
    protocol: pyroscope                      # pyroscope | otlp
    headers: {}
  sample_rate: 100                           # samples per second

rum:                                         # browser RUM and session replay (signals/rum.md)
  enabled: false
  exporter:
    endpoint: "${OBS_RUM_ENDPOINT}"          # backend OTLP base the same-origin proxy forwards to, e.g. https://oneuptime.example.com/otlp
    headers: {}                              # added by the proxy, never sent to the browser
  app_identifier: "${OBS_SERVICE_NAME}"     # required when RUM is on; the browser's service.name
  sample_ratio: 1.0                          # the browser's root sampling ratio
  session_replay:
    enabled: false                           # starts only with input masking in place
    host: "${OBS_RUM_HOST}"                  # OneUptime origin the replay proxy forwards to (an origin, not a path)
    headers: {}                              # added by the replay proxy; default rum.exporter.headers. Set it when
                                             #   OTLP goes to a collector that holds the key, e.g.
                                             #   x-oneuptime-token: "${file:/var/run/secrets/observability/oneuptime-token}"

genai:                                       # LLM and agent telemetry (signals/genai.md)
  enabled: false
  content:                                   # prompts, completions, tool arguments and results,
                                             #   retrieved documents: sent to Langfuse only
    capture: "${OBS_GENAI_CONTENT:-off}"     # off | redacted | full: the most this environment
                                             #   allows; full only where the data is synthetic
    sample_ratio: 1.0                        # share of traces with content, decided by trace id
    max_chars: 20000                         # per content attribute, after redaction
    media: reference                         # reference | upload (upload only with full)
  metrics:
    enabled: true                            # gen_ai_client_* on /metrics
  langfuse:                                  # backends/langfuse.md
    enabled: false
    mode: "${OBS_LANGFUSE_MODE:-hybrid}"     # hybrid | sdk | otel: who makes and sends the AI spans
    export: app                              # app | collector (collector not with sdk)
    scope: ai_context                        # ai_context | ai_only; every span is a billed observation
    base_url: "${OBS_LANGFUSE_URL}"          # required when on; the origin, e.g. https://cloud.langfuse.com
    public_key: "${file:/var/run/secrets/observability/langfuse-public-key}"   # required for the app
    secret_key: "${file:/var/run/secrets/observability/langfuse-secret-key}"   #   route or scores
    timeout: 10s
    flush_interval: 5s
    scores:
      enabled: false                         # feedback, approval and evaluation scores (hybrid, sdk)
```

Each section is described on its signal page: [traces](signals/traces.md),
[metrics](signals/metrics.md), [application logs](signals/app-logs.md),
[access log](signals/access-log.md), [errors](signals/errors.md),
[profiling](signals/profiling.md), [browser RUM](signals/rum.md) and [GenAI](signals/genai.md).

### Exporter blocks

`tracing.exporter`, `metrics.otlp.exporter` and `logging.exporter` share one shape:

| Key | Default | Meaning |
| --- | --- | --- |
| `endpoint` | `http://localhost:4317` for `grpc`, `http://localhost:4318` for `http/protobuf` | Collector or backend. gRPC takes `scheme://host:port`; `http/protobuf` takes the base URL and the loader appends `/v1/traces`, `/v1/metrics` or `/v1/logs`. |
| `protocol` | `grpc` | `grpc` or `http/protobuf`; some backends accept only one (e.g. Dynatrace takes `http/protobuf`). |
| `compression` | `gzip` | `gzip` or `none`. |
| `timeout` | `10s` | Per export request; units `ms`, `s`, `m`, `h`. |
| `insecure` | `false` | Plaintext gRPC for an endpoint without a scheme. The scheme decides otherwise: an `http://` endpoint is plaintext and an `https://` one uses TLS (see [Conversions](#what-the-loader-writes) for how the loader keeps that true). |
| `tls` | none | `ca_file` (the CA that signs the endpoint's certificate), and `cert_file` with `key_file` for mTLS: paths to mounted PEM files, never inline PEM. |
| `headers` | `{}` | Added to every export request, e.g. backend auth when sending direct. |

`profiling.exporter` has `endpoint` (default `http://localhost:4040`), `protocol` (`pyroscope`
or `otlp`, default `pyroscope`) and `headers`. `rum.exporter` has `endpoint` and `headers`; the
same-origin proxy adds those headers server-side, so they never reach the browser. The replay
proxy adds `rum.session_replay.headers`, or `rum.exporter.headers` when it has none: with OTLP
going to a collector that holds the backend key, `rum.exporter.headers` is empty and the replay
key goes in `rum.session_replay.headers`.

Apps send to the collector by default, and the collector adapts to each backend. Sending
directly to a backend changes only the exporter blocks and the log output; the ops metrics stay
on `/metrics` for Prometheus to scrape. Repeated blocks may share YAML anchors, which the parser
resolves before validation:

```yaml
tracing:
  exporter: &backend
    endpoint: "${OBS_OTLP_ENDPOINT}"         # backend base URL; /v1/<signal> is appended
    protocol: http/protobuf
    compression: gzip
    timeout: 10s
    headers:
      x-oneuptime-token: "${file:/var/run/secrets/observability/token}"
logging:
  output: [stdout, otlp]                     # no log-shipping collector: stdout and OTLP
  exporter: *backend
```

`metrics.otlp.exporter` takes the same anchor when the team pushes its app metrics.

Other backends differ in the auth header and the accepted protocol, for example Dynatrace
(`Authorization: "Api-Token ${file:...}"`, `http/protobuf` only) or New Relic
(`api-key: "${file:...}"`). Check the backend's ingest docs ([backends](backends/README.md));
some also need delta temporality, set with `metrics.otlp.temporality: delta`
([metrics](signals/metrics.md)).

### Path exclusions

`tracing.exclude_paths`, `metrics.prometheus.exclude_paths` and
`logging.access_log.exclude_paths` are separate lists with the same matching:

| Entry | Matches |
| --- | --- |
| `/health` | that exact path, query string ignored; `/health/live` and `/api/healthcare` are other paths |
| `/api-docs/*` | every path below `/api-docs/`; list `/api-docs` as well for the page itself |

The lists suppress successful responses (status 200–399) only; a failed response to a listed
path, other than a 404, is reported as a failed probe ([hooks.md](hooks.md)). The service's API
docs paths (the Swagger UI and the OpenAPI document) go in all three lists, like the probes.
Profiling samples the whole process and can't exclude paths.

### GenAI

`genai.enabled` switches on the GenAI client metrics, the AI feature context and, when
configured, Langfuse ([signals/genai.md](signals/genai.md)). Everything else about AI telemetry
is code: the feature registry (names, tags, a feature's content opt-in and its redactor), the
helpers and the stamping processor.

- **`langfuse.mode`** says who makes and sends the AI spans ([genai](signals/genai.md#modes)):
  `hybrid` (the OpenTelemetry GenAI instrumentation and the module's helpers; the Langfuse SDK
  sends them and the scores), `sdk` (the Langfuse SDK makes the AI observations; Python and
  TypeScript) or `otel` (no Langfuse SDK for telemetry, no scores). App code is the same in all
  three, so interpolating it from a variable lets each environment pick.
- **`content.capture`** is a ceiling, like the access log's `body.full`: the most this
  environment allows. Each feature gets the lower of the ceiling and its own setting, and
  `redacted` needs the feature's redactor, so a feature without one records nothing under a
  `redacted` ceiling. The chart sets `full` only where the data is synthetic; production takes
  `off`, or `redacted` when features with a redactor have the owner's sign-off. Content goes to
  Langfuse only, never to logs, metrics or the ops backend.
- **`langfuse.export`** picks who sends the spans. `app`: the SDK's span processor (`hybrid`,
  `sdk`) or the module's OTLP exporter (`otel`) on the module's own tracer provider, so both
  backends see the same trace ids. `collector`: the collector's Langfuse branch sends the AI spans
  ([collector](collector/README.md)), and the app holds keys only for scores or for app code
  that uses the module's client.
- **Prompts and datasets** are the app's own Langfuse features, not settings here: app code
  fetches them through the module's client and hands the model-call helper the prompt it used.
- **Keys** are files from a mounted Secret, passed to the SDK or REST client as constructor
  options; never `LANGFUSE_*` variables and never `headers`. The module builds the Basic auth
  and the ingestion header itself.
- **Experiments** add no keys: a runner reads its dataset, run name and version from its
  command line, uses the section's Langfuse settings (with `scores` on for evaluator scores)
  and needs `content.capture: full` where it runs ([signals/genai.md](signals/genai.md#experiments)).

Not in the section, on purpose: the environment and release, which Langfuse takes from the
resource; a Langfuse sample rate (the trace sampler decides, and the feature registry keeps
AI entry points); headers; any switch that puts AI context in baggage.

## Interpolation

- **Grammar.** `${VAR}` and `${env:VAR}` read an environment variable. `${VAR:-default}` uses
  `default` when `VAR` is unset or empty. `${file:/path}` reads a mounted file. `$$` is a
  literal `$`.
- **Scalar-only.** References work inside scalar values, list items included, never in keys,
  and never produce a list or a map. The result is a string, which the loader then converts to
  the key's type: `"true"` to a boolean, `"9464"` to an integer, `"10s"` to a duration. That is
  why the schema accepts strings wherever a boolean or a number is expected.
- **Resolved once,** at start-up. A resolved value that itself contains `${...}` is used as-is,
  and a rotated secret file is read again on the next restart.
- **`${file:/path}` trims one trailing newline,** so files mounted from a Kubernetes Secret
  work as they are.
- **Empty means not set.** An unset variable without a default resolves to an empty string. A
  key that resolves empty takes its default, and an empty resource attribute is dropped.
- **Secrets** enter only through `${file:...}` (preferred) or `${env:...}`. Never write a
  literal secret into the file.
- **Variable names** are the project's own; the examples use `OBS_*`. Never reference `OTEL_*`
  variables, which the loader overwrites.

## Precedence

The yml is the single source. Environment variables enter only through `${...}` references.
The loader owns every `OTEL_*` setting: it writes the settings below from the yml and the fixed
ones from code, removes any other `OTEL_*` variable it finds, and logs one WARN naming the
variables it overwrote or removed, never their values. It owns the `LANGFUSE_*`, `TRACELOOP_*`
and `OPENINFERENCE_*` variables the same way, whether GenAI is on or not. A stray variable can
therefore neither redirect telemetry nor switch on capture in an auto-instrumentation. The one
exception is `OTEL_SDK_DISABLED=true`, which stays the standard off switch (the overhead
baseline in [acceptance.md](acceptance.md) uses it).

## What the loader writes

| yml | Standard setting |
| --- | --- |
| `resource.service.name` | `OTEL_SERVICE_NAME` |
| `resource.service.namespace`, `resource.service.version`, `resource.environment`, `resource.attributes` | `OTEL_RESOURCE_ATTRIBUTES` with `service.namespace`, `service.version`, `deployment.environment.name` and each attribute; values percent-encoded, empty ones dropped |
| `tracing.enabled` | `OTEL_TRACES_EXPORTER`: `otlp` or `none` |
| `tracing.exporter.*` | `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `_PROTOCOL`, `_COMPRESSION`, `_TIMEOUT`, `_HEADERS`, and `_INSECURE` only for an endpoint without a scheme; `tls.ca_file`, `tls.cert_file`, `tls.key_file` to `_CERTIFICATE`, `_CLIENT_CERTIFICATE`, `_CLIENT_KEY`. Headers go as exporter options wherever the SDK takes them (see below); with Langfuse on the app route, the Langfuse exporter reads none of these variables ([genai](signals/genai.md#required-behaviour)) |
| `tracing.sampler.name`, `tracing.sampler.arg` | `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, passed through unchanged |
| `tracing.exclude_paths`, `tracing.trace_failed_probes` | No standard variable. A path rule in the sampler, or the runtime's URL exclusion, drops the spans at start. With `trace_failed_probes: true` the spans are recorded and an export filter (an SDK exporter wrapper or a collector `filter`) drops only the successful ones. Where a tool can't filter by path in code (e.g. the Java agent), a rule-based sampler in its declarative configuration, with a collector `filter` as the fallback. |
| `metrics.enabled`, `metrics.otlp.enabled` | `OTEL_METRICS_EXPORTER`: `otlp` when both are on, otherwise `none` |
| `metrics.prometheus.*` | No standard variable. The module's `/metrics` server, built on the stack's Prometheus client library, listens on `host` and `port` and serves `path` ([signals/metrics.md](signals/metrics.md)). |
| `metrics.otlp.interval` | `OTEL_METRIC_EXPORT_INTERVAL` |
| `metrics.otlp.temporality` | `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` (default `cumulative`) |
| `metrics.otlp.exporter.*` | `OTEL_EXPORTER_OTLP_METRICS_*`, the same keys as `tracing.exporter.*` |
| `metrics.prometheus.exclude_paths`, `logging.access_log.exclude_paths` | No standard variable. The middleware skips successful responses and emits the `health.probe.failed` log and the failure counter for failed ones ([hooks.md](hooks.md)). |
| `logging.output` | `OTEL_LOGS_EXPORTER`: `otlp` when the output includes `otlp`, otherwise `none`; stdout lines come from the module's own log handler |
| `logging.exporter.*` | `OTEL_EXPORTER_OTLP_LOGS_*`, the same keys as `tracing.exporter.*` |
| `logging.format`, `logging.application.*`, `logging.access_log.*` | read by the module's logger, function wrappers (`slow_threshold`) and access-log middleware |
| `profiling.*` | the profiler SDK's server address, headers and sample rate, or the OTLP profiles exporter |
| `rum.*` | browser configuration served at runtime; exporter headers stay in the proxy |
| `genai.content.capture`, `genai.enabled` | The content switches of every GenAI instrumentation, written even with GenAI off: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (`SPAN_ONLY` with `full`, otherwise `NO_CONTENT`), `OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT=false`, `TRACELOOP_TRACE_CONTENT`, the `OPENINFERENCE_HIDE_*` set (embedding vectors always hidden) and `LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED`, all on only with `full`. Under `redacted`, only the module's content helper records content. Where a library has no variable, the module passes the option in code (for example `recordInputs`/`recordOutputs`, or a callback handler's capture flags) |
| `genai.metrics.enabled` | No standard variable. The module records `gen_ai_client_*` at each model call and serves them on `/metrics` ([signals/metrics.md](signals/metrics.md)) |
| `genai.langfuse.*` | No standard variable. The module's Langfuse span processor or exporter (app route) and its client (scores, and the app's prompts and datasets) take the base URL, keys, timeout, flush interval and scope as constructor options; the JS score client reads its flush settings from `LANGFUSE_FLUSH_AT` and `LANGFUSE_FLUSH_INTERVAL` only ([sdk-notes.md](sdk-notes.md#genai-and-langfuse)) |
| fixed in code, not in the yml | `OTEL_PROPAGATORS=tracecontext,baggage`, `OTEL_SEMCONV_STABILITY_OPT_IN` (e.g. `http,database`, plus `gen_ai_latest_experimental` when GenAI is on), an SDK resource without the default detectors where the SDK allows it ([sdk-notes.md](sdk-notes.md)), SDK batching, retry and queue defaults, attribute limits, the cardinality limit when OTLP metric push is on, and Langfuse's OTLP path and ingestion header |

Conversions:

- Durations become milliseconds (`10s` becomes `10000`).
- `http/protobuf` endpoints get the `/v1/<signal>` path appended; gRPC endpoints are used as
  given.
- Only the per-signal `OTEL_EXPORTER_OTLP_{TRACES,METRICS,LOGS}_*` variables are written, so a
  generic `OTEL_EXPORTER_OTLP_*` value can't apply.
- Header values are percent-encoded as the OTLP header variables require. Where the SDK takes
  headers as an option, the loader passes them that way and keeps secrets out of the process
  environment. Every OTLP exporter in the process reads the header variables, and Python's and
  JS's merge them into their own headers, so a header written as a variable would also reach
  Langfuse or any other second exporter.
- The endpoint's scheme decides TLS. The loader writes `_INSECURE` only for an endpoint without
  a scheme. Some SDKs let an explicit `_INSECURE=false` force TLS onto an `http://` endpoint;
  the export to a plaintext collector then fails on every request.

### SDK differences

The variables above follow the OpenTelemetry specification, but SDKs don't all read them the
same way. Check each setting against the SDK's own exporter code or docs. Where a variable is
read differently, pass the value as an exporter option in the SDK's unit and leave the
variable unset.

| SDK | Difference | What the loader does |
| --- | --- | --- |
| Python OTLP exporters (gRPC and HTTP) | read `_TIMEOUT` in seconds, while the specification says milliseconds | passes `timeout` in seconds as an exporter option |
| Python OTLP gRPC exporter | an explicit `_INSECURE=false` makes an `http://` endpoint use TLS | leaves `_INSECURE` unset for endpoints with a scheme |

A test that exports to an in-memory exporter never opens a connection, so it can't catch any
of these. Verify against a real OTLP receiver at least once
([acceptance.md](acceptance.md#local-receiver)).

## Validation and failure behaviour

`validate` runs in CI and checks the file in two passes, never printing resolved values:

1. the raw file against the schema, where any scalar may still be a `${...}` string;
2. the resolved file, with values converted to their types and empty values removed, against
   the schema again. It fails when a required key doesn't resolve: `resource.service.name`,
   `resource.service.version`, `resource.environment`; when RUM is on,
   `rum.exporter.endpoint` and `rum.app_identifier` (plus `rum.session_replay.host` with replay
   on); and when Langfuse is on, `genai.langfuse.base_url`, plus both keys on the app route or
   with scores on.

With GenAI on it also checks the settings against each other: literal Langfuse keys (an error
for the secret key, a warning for the public key); the collector route with mode `sdk`, with
tracing off or with exporter headers (direct export); scores on with mode `otel`, which sends
none; an environment name Langfuse rejects; `full`
content in a production-named environment; `media: upload` without `full`; AI-looking fields
or full bodies in the access log; and a `base_url` with a path or plain http to a remote host.

At runtime a bad configuration never stops the service. If the file is missing, unreadable or
invalid, or a required key doesn't resolve, the loader logs one ERROR on stderr naming the
file, the key and the reason (never a value) and starts the service with telemetry degraded;
logs still reach stdout with the defaults:

- **No usable file** (missing, unreadable or not valid YAML): nothing is exported, and no
  `/metrics` server starts, so no port is bound. There is no configuration to say which port,
  and binding a default port can collide with another process.
- **An invalid section:** that signal doesn't export. An invalid `metrics` section starts no
  `/metrics` server either.
- **An unreadable `${file:...}`** leaves its key empty and is reported the same way.
- **Langfuse unusable** (keys missing, the host unreachable or slow): the Langfuse destination
  stays off or drops its batches with one rate-limited WARN; the ops telemetry and the requests
  are unaffected.

## Not configurable here

These are code, written safe by construction and checked by the audit, so the file has no
section for them:

| Concern | Where it lives |
| --- | --- |
| Span and log attributes, request context, business enrichment | code, [enrichment.md](enrichment.md) |
| Function wrappers, step logs, profiling labels | code, [functions.md](functions.md); only the slow-step threshold is in the yml |
| Metric names, labels and buckets | fixed by the standard, [signals/metrics.md](signals/metrics.md) |
| Masking and what is safe to capture | code, [safe-capture.md](safe-capture.md); there is no `privacy` or redaction section |
| Error reporting | ERROR log records, [signals/errors.md](signals/errors.md); there is no `error_reporting` section |
| Trusted proxies for `client.address` | the framework's own proxy settings, [hooks.md](hooks.md) |
| Correlation id header and validity rules | fixed, [hooks.md](hooks.md) |
| Propagators, semconv opt-ins, batching, retries, limits | fixed in code (table above) |
| `service.instance.id`, the SDK detectors' attributes | not set: the resource holds only the standard's eight attributes, plus `k8s.cluster.name` where several clusters send to one backend ([data-model.md](data-model.md#resource)) |
| RUM masking marks and selectors | code |
| AI features: names, tags, metadata, content opt-in and redactors, which AI entry points are always sampled | code, the feature registry ([signals/genai.md](signals/genai.md)) |
| Prompts, score configs, model prices, evaluators, annotation queues | Langfuse itself, as a platform handoff ([backends/langfuse.md](backends/langfuse.md)) |
| The content key families, the ops allow-list for AI spans | fixed by the standard, [signals/genai.md](signals/genai.md) |

[Documentation index](README.md)
