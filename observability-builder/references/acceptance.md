# Acceptance

How a service proves it meets the standard: language-neutral, black-box checks against the running
service, with a local OTLP receiver, real requests, a `/metrics` scrape, stdout capture, a headless
browser for RUM, and fault injection between the service, the collector and the backend. The
instrument workflow runs them in its verify step ([instrument.md](instrument.md), step 7).

A service conforms when every check that applies to its workload passes through the collector,
and through direct export where the service uses it. A batch job has no access log or probes;
its root span, job log and, when it pushes them, its job metrics are checked instead
([workloads/batch.md](workloads/batch.md)).

## Local receiver

Check the real exports with the skill's receiver, `scripts/receive.py`: it runs OTLP/gRPC,
OTLP/HTTP and Pyroscope sinks on free local ports and writes what arrives, decoded, plus a
`summary.md` (resources, one tree per trace, metric names and units, log records, profile labels,
session replay, rejected requests). With `--canaries <file>` (JSON `{label: value}`) it also
reports which test values reached which stream, replay events included, by label only.

```bash
uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml \
  scripts/receive.py --out <scratch>/telemetry      # prints the endpoints as one JSON line
```

Point the yml's exporter endpoints at the printed `http://127.0.0.1:<port>` URLs (through the
project's own env overrides), start the app the way it really starts, send one request of each
kind, stop it with SIGTERM, then read `summary.md`. The OTLP/HTTP port also serves a Langfuse
stand-in (traces, scores, prompts from `--langfuse-prompts`, datasets for experiment runners from
`--langfuse-datasets`; auth recorded as booleans), and `scripts/llm_stub.py`
stands in for the model providers ([GenAI](#genai)). In-memory and console exporters never open a
connection, so they can't catch a TLS, protocol, endpoint-path or timeout mistake. Any of these
makes every export fail, and only a real receiver shows it.

## Through the collector into the backend

The receiver shows what the service exports; the collector and the backend can still change it
on the way. Run the same requests once through the real collector into a disposable instance of
the backend (for OneUptime, its community edition with Alloy in front), and check the stored data:

| Check | Passes when |
| --- | --- |
| Metric types | duration histograms are stored as histograms with the standard bounds, and counters as monotonic sums, not as `_bucket`, `_sum` and `_count` gauges ([collector](collector/README.md#scraped-metrics)) |
| Scraped identity | scraped metrics carry the service's resource, without `service.instance.id` or the scrape's address attributes |
| Shipped logs | stdout records arrive with native severity, body, trace and span ids that point at stored spans, no unparsed JSON bodies and no collector attributes |
| Exceptions | each failure is one exception, linked to its trace, with release and environment set |
| RUM application | the browser's spans, web vitals and session replays belong to one RUM application |
| Span profiles | where a span-profile integration is on, profile samples link to stored traces |

## Identity

| Check | Passes when |
| --- | --- |
| Resource present | `service.name`, `service.namespace`, `service.version` and `deployment.environment.name` come from the yml and are never defaulted; in Kubernetes `k8s.namespace.name`, `k8s.pod.name`, `k8s.pod.uid` and `k8s.node.name` come from the downward API, and `k8s.cluster.name`, where several clusters send to one backend, from the chart's per-cluster value ([data-model.md](data-model.md#resource)) |
| Nothing else in the resource | no `telemetry.sdk.*`, `process.*`, `host.*`, `os.*`, `vcs.*` or `service.instance.id`, except where the SDK can't drop them, which the report names ([sdk-notes.md](sdk-notes.md)) |
| One resource across signals | traces, OTLP metrics, scraped metrics, OTLP logs, stdout-shipped logs and profiles (as static labels) carry an identical identity, with `k8s.cluster.name` from the app's environment on its own telemetry and added by each cluster's collector to what it collects itself ([collector](collector/README.md)) |
| Nothing per-request in the resource | no trace ID and no request, user or session ID in resource attributes or `OTEL_RESOURCE_ATTRIBUTES` |

## Traces

| Check | Passes when |
| --- | --- |
| Entry span | each request has a SERVER entry span named `{method} {http.route}` with the HTTP attributes; `http.route` is a template, and a 404 has `unmatched` on its span, its metrics and its access log ([hooks.md](hooks.md)) |
| No middleware spans | the framework's middleware, router and handler layers produce no spans; the module's wrappers span the handlers |
| Status | spans are UNSET unless their own operation failed; no span has status OK ([signals/traces.md](signals/traces.md#status)) |
| Request attributes | the configured request attributes (user, tenant, session, client app and version, correlation ID) appear as-is on each service's entry span and on its access log, and child spans don't repeat them |
| Client address | `client.address` is the real client behind trusted proxies; a spoofed `X-Forwarded-For` from an untrusted peer is ignored |
| Nested span tree | a sampled request produces SERVER → INTERNAL (handler, service, repository) → CLIENT with `code.*` attributes and status; the failing span and its callers are ERROR with `error.type`; the profile samples of labelled heavy code carry the `controller` label ([functions.md](functions.md)) |
| Unsampled requests | produce no spans but still their access log |
| Job runs | each run of a scheduled job is a root span `job {name}` with `app.job.name`, `app.job.trigger` and the item counts, one `job_run` record or one ERROR record, and the job metrics; work that continues after a response is a new root linked to the request ([workloads/batch.md](workloads/batch.md)) |
| WebSocket | the handshake is a root SERVER span `WS {path}` (for example `WS /ws`); each message other than a ping is a root SERVER span `WS {type}` linked to it; a rejected client leaves the span UNSET with a WARN record and isn't counted in the connection counters; connected and disconnected records at INFO ([workloads/api.md](workloads/api.md#websocket)) |
| Propagation | one trace ID runs from the browser through the API and downstream services to the database; only allow-listed baggage keys travel, and none appear as metric labels, profile labels or resource attributes |

## Logs

| Check | Passes when |
| --- | --- |
| Native correlation | every record written inside a request, message or run carries native `traceId` and `spanId` — after collector mapping for stdout-shipped logs; a JSON `trace_id` field alone fails |
| Severity | native severity number and text follow the level policy; the access log uses ERROR for 5xx and WARN for 4xx ([signals/app-logs.md](signals/app-logs.md)) |
| Step logs | at `debug`, every wrapped call writes `started` and `finished` or `failed` with its duration; at `info` there are none; a call slower than `logging.application.slow_threshold` writes a WARN |
| One logger | application code writes nothing past the app's logger: no plain `console`, `print` or `fmt.Println` lines on stdout or stderr |
| Exception records | one ERROR record per failure with `exception.type`, `exception.message` and `exception.stacktrace`, arriving as one record; step logs carry span IDs at the policy's levels ([signals/errors.md](signals/errors.md)) |
| Access log | one record per request at response end with only the allow-listed headers, cookies and body fields; raw `Cookie` and `Set-Cookie` are never logged ([signals/access-log.md](signals/access-log.md)) |
| Responses | no stack trace reaches an HTTP response, and the existing error response format is unchanged |

## Metrics

| Check | Passes when |
| --- | --- |
| Endpoint | `/metrics` answers on the port the yml declares, never on the application port |
| Contract | the scrape has the default set with the contract's names, types and labels: `http_server_request_duration_seconds{method,route,status_code}`, `http_server_requests_total{method,route}`, `http_server_active_requests{method}`, the client, pool, WebSocket, probe and job metrics, and the library's process and runtime metrics ([signals/metrics.md](signals/metrics.md#the-default-set)) |
| Buckets | duration histograms have the `le` bounds `0.005` to `60` of the standard list; `job_duration_seconds` adds `120`, `300`, `600` and `1800` |
| Labels | label values are bounded: methods, route templates or `unmatched`, dependency names, status codes; no user, session, tenant, order, request or trace ids, raw URLs or paths |
| Client calls | outbound calls made inside a request, job or WebSocket trace are measured; start-up calls and the SDK's own export calls are not, including exports that run from a timer that inherited a request's context ([signals/metrics.md](signals/metrics.md)) |
| One path per series | with OTLP push on, the pushed app metrics don't repeat an ops metric from the scrape |

## Exclusions

| Check | Passes when |
| --- | --- |
| Successful probes | successful calls to `/health`, `/ready`, `/live`, `/metrics` and `/status` produce no spans, metrics or access-log records, honouring each signal's own list |
| API docs | the docs page and its assets (for example `/api-docs` and everything under `/api-docs/*`) produce no spans, metrics or access-log records |
| Failed probes | `/ready` returning 503 produces an ERROR `health.probe.failed` record, in place of its access-log record, and increments `app_health_probe_failures_total{probe="/ready"}`; the record and the counter name the failing check as the readiness response names it, or `unknown`; its trace is kept only when `trace_failed_probes` is on ([hooks.md](hooks.md)) |
| 404 on an excluded path | an ordinary request: `/metrics` asked on the application port gets its access-log record, metrics and span, with the route `unmatched` ([hooks.md](hooks.md)) |
| Lookalikes | `/api/healthcare` and similar paths are recorded in every signal |

## Safe capture

| Check | Passes when |
| --- | --- |
| Secret canaries | a fake JWT, a bearer token, a test API key, a password field, a session cookie and a test card number — sent through headers, cookies, query strings, bodies, database parameters, exception messages, function arguments, messaging and GenAI attributes, and baggage — never appear in exported OTLP, stdout logs, profiles or the collector's output |
| Visible fields | client IP, user, tenant and session IDs and the selected body fields do appear, as-is |
| No bodies on spans | no span attribute contains a request or response body |
| Audit | the telemetry-safety audit reports nothing on the instrumented service; on a deliberately unsafe sample (a body as a span attribute, a user ID as a metric label, a token in baggage, an input value captured in RUM, raw SQL literals, header capture switched on in auto-instrumentation, a prompt logged, AI content set around the content helper, AI capture switched on in code) it reports every case with file, line and fix ([safe-capture.md](safe-capture.md)) |
| Content canaries | a canary in a prompt, a tool argument and a retrieved document reaches the Langfuse stream only in a run with `full` content, and appears nowhere in a production run ([GenAI](#genai)) |
| Fail closed | with a masking helper forced to fail, the value is omitted and the service keeps serving; with replay masking broken, session replay doesn't start |
| RUM masking | canary values typed into every sensitive field and URL query never appear in OTLP payloads, browser logs, URLs or replay chunks, while non-sensitive fields still record |

## Config wrapper

| Check | Passes when |
| --- | --- |
| Effective config | the loader turns `observability.yml` into the standard `OTEL_*` settings, checked by dumping the SDK's effective configuration ([config.md](config.md)) |
| Precedence | stray `OTEL_*` values in the environment are overwritten or removed, with one warning that names them but never their values; only `OTEL_SDK_DISABLED` is honoured |
| Defaults | export is OTLP over gRPC with gzip, batching and retries |
| Bad config | validation fails on unresolved keys without printing values; at runtime a bad config degrades telemetry with one ERROR naming the file, key and reason instead of crashing the service; with the file missing, unreadable or not valid YAML, nothing is exported, logs still reach stdout and no metrics port is bound ([config.md](config.md#validation-and-failure-behaviour)) |

## GenAI

For services that call a model ([signals/genai.md](signals/genai.md)), run twice against the
model stub and the Langfuse stand-in: a non-production environment with `full` content, and
production with `off` (or `redacted` for the features with a redactor), each with stray
`TRACELOOP_*`, `OPENINFERENCE_*` and `LANGFUSE_*` capture variables set to "on".

| Check | Passes when |
| --- | --- |
| Transport | Langfuse-bound spans use OTLP over HTTP to `/api/public/otel/v1/traces` with Basic auth, and any exporter other than a Langfuse SDK sends `x-langfuse-ingestion-version: 4` |
| Structure | each AI request is one trace in both destinations, with one root observation in Langfuse (the entry span); the steps have the names, kinds and Langfuse types of the [step kinds](signals/genai.md#step-kinds); with scope `ai_context`, no database, cache, HTTP or queue client span reaches Langfuse |
| Trace context | every Langfuse-bound observation carries the feature as trace name, the user, the conversation as session and the release; none of them in baggage or on the ops path |
| Model and usage | generations and embeddings carry the response model and usage, streams included; streamed generations have a time to first chunk |
| Status and level | the failing step is ERROR with `error.type`, and its status description on the ops path is that type, never the provider's message; degraded steps are WARNING with a constant status message; no span is OK; a streamed answer adds no ERROR record |
| Content | the `full` run has content on the Langfuse path only, within `max_chars`; the production run has no content key anywhere and only redacted values for redacting features; the stray variables change nothing; logs, metrics, span events and the ops stream never hold content, not even from a provider error that quotes the input (the stub's errors do) |
| Prompt links | generations that used a managed prompt (fetched by the app's own code through the module's client) link to its version; a fallback is WARNING and unlinked |
| Scores | in `hybrid` and `sdk`, feedback sent twice for one message is one score, sent by the server, with the resource's environment; in `otel`, no request reaches Langfuse's API |
| Modes | the same app, switched by `genai.langfuse.mode` alone, passes every row above in `hybrid`, `sdk` (generations from the Langfuse SDK, no GenAI instrumentation span) and `otel` (exports with the ingestion header, no Langfuse SDK loaded) |
| Experiments | a runner over a two-item dataset leaves two traces with one root each, the experiment and item keys on their spans (none in the requests to the model provider), input, output and expected output on each root, the evaluator scores on the roots, environment `experiment`, and no span on the ops path; it refuses to start where content isn't `full` |
| Metrics | the GenAI metrics are on `/metrics` with bounded labels and the extended buckets; a 429 counts as `rate_limited` |
| Failure | with Langfuse down or slow, requests and the ops export are unaffected and one rate-limited WARN appears; shutdown flushes both within the grace period |

Optionally repeat through the real collector into a disposable Langfuse (its self-hosted
compose stack) and read the observations back through its v2 observations API
([backends/langfuse.md](backends/langfuse.md#self-hosting)).

## Failure isolation

Inject each fault while normal traffic runs: collector down, connection
refused, a slow collector, a TLS failure, backend 429, backend 500, a full export queue, and
SIGTERM with telemetry still queued.

| Check | Passes when, for every fault |
| --- | --- |
| Service | keeps serving with an unchanged error rate; requests never wait on export |
| Memory | stays bounded — queues drop instead of growing |
| Drops | are visible, through SDK self-metrics or a rate-limited warning |
| Shutdown | finishes within the configured timeout, with the last spans, logs and profile interval delivered: a process that runs for less than one profile upload interval still sends a profile |

Telemetry must never become a production dependency.

## Browser RUM and session replay

For apps with web pages ([rum.md](signals/rum.md)), drive the pages in a headless browser against the
receiver started with `--replay-assets <dir>`; its session-replay stand-in speaks OneUptime's protocol:

| Check | Passes when |
| --- | --- |
| Transport | browser telemetry and replay reach the backend through the app's same-origin proxy, which adds the key; the page never holds it |
| Identity | the browser resource carries the server's service name, version and environment plus the `browser.*` attributes |
| Pages and routes | page loads and client-side route changes produce spans with `app.route` templates |
| Browser → API | the API's `SERVER` span continues the browser's fetch span |
| Session | `session.id` on browser spans and logs is the replay session, and API requests carry its `tracestate` |
| Errors | one sanitised exception record per browser error |
| Web vitals | LCP, FCP, TTFB, CLS and INP arrive once each for a page that is hidden, closed or left by a full navigation. A scripted click is too fast to count for INP (under 40 ms), so the test slows clicks with an injected capturing click listener that busies the main thread for about 250 ms |
| Replay masking | replay chunks arrive with a full snapshot and carry none of the values typed into sensitive fields |
| Safe capture | no typed value, query value or fragment in any browser payload |

## Backend swap

One service is re-pointed from one backend to another — for example from OneUptime to Tempo,
Loki, Prometheus and Pyroscope — by changing only the yml and the collector configuration. The
same checks pass, and the change contains no application code ([backends](backends/README.md),
[collector](collector/README.md)).

## Later: overhead and load

Overhead tests will run the same load with telemetry on and off (`OTEL_SDK_DISABLED` as the
baseline) at the configured sample ratio, and compare latency, CPU, memory, spans per request,
log bytes per request, export-queue depth, dropped telemetry and the collector's own usage.
Their thresholds will be adjustable per project. They are not part
of the current acceptance.

[Documentation index](README.md)
