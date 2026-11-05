# Acceptance

How a service proves it meets the standard: language-neutral, black-box checks against the running
service, with a local OTLP receiver, real requests, a `/metrics` scrape, stdout capture and
fault injection between the service, the collector and the backend. The
instrument workflow runs them in its verify step ([instrument.md](instrument.md), step 7).

A service conforms when every check that applies to its workload passes through the collector,
and through direct export where the service uses it. A batch job has no access log or probes;
its root span, job log and, when it pushes them, its job metrics are checked instead
([workloads/batch.md](workloads/batch.md)).

## Local receiver

Check the real exports with the skill's receiver, `scripts/receive.py`: it runs OTLP/gRPC,
OTLP/HTTP and Pyroscope sinks on free local ports and writes what arrives, decoded, plus a
`summary.md` (resources, one tree per trace, metric names and units, log records, profile labels,
rejected requests). With `--canaries <file>` (JSON `{label: value}`) it also reports which test
values reached which stream, by label only.

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
the backend (for the Grafana stack, its single-binary components with Alloy in front), and check the stored data:

| Check | Passes when |
| --- | --- |
| Metric types | duration histograms are stored as histograms with the standard bounds, and counters as monotonic sums, not as `_bucket`, `_sum` and `_count` gauges ([collector](collector/README.md#scraped-metrics)) |
| Scraped identity | scraped metrics carry the service's resource, without `service.instance.id` or the scrape's address attributes |
| Shipped logs | stdout records arrive with native severity, body, trace and span ids that point at stored spans, no unparsed JSON bodies and no collector attributes |
| Exceptions | each failure is one exception, linked to its trace, with release and environment set |
| Span profiles | where a span-profile integration is on, profile samples link to stored traces |

## Identity

| Check | Passes when |
| --- | --- |
| Resource present | `service.name`, `service.namespace`, `service.version` and `deployment.environment.name` come from the yml and are never defaulted; the M and C rows of the detected platform are present ([platform resource](#platform-resource)): in Kubernetes `k8s.namespace.name`, `k8s.pod.name` and `k8s.node.name` from the downward API, and `k8s.cluster.name`, where several clusters send to one backend, from the chart's per-cluster value ([data-model.md](data-model.md#resource)) |
| SDK rows kept | `telemetry.sdk.language`, `telemetry.sdk.version`, `process.runtime.name`, `process.runtime.version` and `os.type` are present (R: a missing one is reported, not failed) |
| Nothing excluded in the resource | no `process.pid`, command line or arguments, `process.executable.*`, `vcs.*`, `service.instance.id`, `k8s.pod.uid`, `host.id` or `deployment.id`, and no platform row of another platform (no `host.name` in a Kubernetes pod, no `cloud.*` on a local run), except where the SDK can't drop them, which the report names ([sdk-notes.md](sdk-notes.md#resource-detectors)) |
| One resource across signals | traces, OTLP metrics, scraped metrics, OTLP logs, stdout-shipped logs and profiles carry an identical identity, with `k8s.cluster.name` from the app's environment on its own telemetry and added by each cluster's collector to what it collects itself ([collector](collector/README.md)); profiles carry the M and C rows as static labels (dots become underscores) and no R or O row |
| Workload name | in Kubernetes, telemetry that passed the collector carries the owning workload's name (`k8s.deployment.name`, `k8s.statefulset.name`, `k8s.daemonset.name`, `k8s.cronjob.name` or `k8s.job.name`), matched by namespace and pod name |
| Nothing per-request in the resource | no trace ID and no request, user or session ID in resource attributes or `OTEL_RESOURCE_ATTRIBUTES` |

### Platform resource

Each platform the service runs on has its own mandatory rows and instance identity
([data-model.md](data-model.md#instance-identity-per-platform)). Check the ones the service
deploys to: live where the platform can run locally, and with stubbed platform environment where
it can't. The report says which rows were stub-checked: a stub proves the detector is wired and
filtered, not that the platform's real metadata matches.

| Platform | How | Passes when |
| --- | --- | --- |
| Kubernetes | live, in a local cluster with the chart's downward-API env | `k8s.namespace.name`, `k8s.pod.name`, `k8s.node.name` on every signal; no `host.name`, `k8s.pod.uid` or `container.id` from the app |
| VM | live, the process run on the host | `host.name` present; no `k8s.*`, `container.id` or `cloud.*` (on a non-cloud host) |
| Plain Docker | live, the image run with `docker run` | `host.name` and `container.id` present, `container.id` matching the running container |
| ECS / Fargate | stub: `ECS_CONTAINER_METADATA_URI_V4` pointing at a small mock server that answers the container (`/`) and task (`/task`) metadata documents | `cloud.provider=aws`, `cloud.platform=aws_ecs`, `cloud.region`, `aws.ecs.cluster.arn`, `aws.ecs.task.arn` and `aws.ecs.launchtype` from the mock's values |
| Lambda | stub: `AWS_LAMBDA_FUNCTION_NAME`, `AWS_LAMBDA_FUNCTION_VERSION`, `AWS_REGION` and `AWS_LAMBDA_LOG_STREAM_NAME` set | `cloud.provider=aws`, `cloud.platform=aws_lambda`, `cloud.region`, `faas.name`, `faas.version`, `faas.instance` |
| Cloud Run service | stub: `K_SERVICE`, `K_REVISION`, `K_CONFIGURATION`, plus a metadata mock for region and instance id where the detector reads one (`GCE_METADATA_HOST` for the Google metadata clients; check that the SDK's detector honours it) | `cloud.provider=gcp`, `cloud.platform=gcp_cloud_run`, `cloud.region`, `faas.name`, `faas.version`, `faas.instance` |
| Cloud Run job | stub: `CLOUD_RUN_JOB`, `CLOUD_RUN_EXECUTION`, `CLOUD_RUN_TASK_INDEX`, plus the metadata mock | `cloud.*`, `faas.name`, `gcp.cloud_run.job.execution`, `gcp.cloud_run.job.task_index` |
| Off-platform | a local run with none of the variables above | no platform detector waited on a metadata endpoint at start-up, and no `cloud.*`, `aws.*`, `faas.*` or `gcp.*` row appears |

For each platform the profile labels carry the same M and C rows ([data-model.md](data-model.md#profile-labels)).

## Traces

| Check | Passes when |
| --- | --- |
| Entry span | each request has a SERVER entry span named `{method} {http.route}` with the HTTP attributes; `http.route` is a template, and a 404 has `unmatched` on its span, its metrics and its access log ([hooks.md](hooks.md)) |
| No middleware spans | the framework's middleware, router and handler layers produce no spans; the module's wrappers span the handlers |
| Status | spans are UNSET unless their own operation failed; no span has status OK ([signals/traces.md](signals/traces.md#status)) |
| Request attributes | the core context rows (`user.id`, `tenant.id`, `session.id`, `correlation.id`, `http.route`, `client.address`, user agent, client app and version) appear on each service's entry span and on its access log per their class, and child spans don't repeat them. Reference-catalogue rows (roles, user type, plan, organisation or account ids, `app.auth.*`) are checked only where the code has the value; no `request.id` anywhere ([data-model.md](data-model.md#request-and-identity-context)) |
| Client address | `client.address` is the real client behind trusted proxies; a spoofed `X-Forwarded-For` from an untrusted peer is ignored |
| Nested span tree | a sampled request produces SERVER → INTERNAL (handler, service, repository) → CLIENT with `code.*` attributes and status; the failing span and its callers are ERROR with `error.type`; the profile samples of labelled heavy code carry the `controller` label ([functions.md](functions.md)) |
| Unsampled requests | produce no spans but still their access log |
| Job runs | each run of a scheduled job is a root span `job {name}` with `app.job.name`, `app.job.trigger` and the item counts, one `job_run` record or one ERROR record, and the job metrics; work that continues after a response is a new root linked to the request ([workloads/batch.md](workloads/batch.md)) |
| WebSocket | the handshake is a root SERVER span `WS {path}` (for example `WS /ws`); each message other than a ping is a root SERVER span `WS {type}` linked to it; a rejected client leaves the span UNSET with a WARN record and isn't counted in the connection counters; connected and disconnected records at INFO ([workloads/api.md](workloads/api.md#websocket)) |
| Propagation | one trace ID runs from the edge through the API and downstream services to the database; only allow-listed baggage keys travel, and none appear as metric labels, profile labels or resource attributes |
| Baggage limits | an incoming baggage header with more than 8 keys or more than 512 bytes is cut to the allow-list within the limits; a secret, an `authorization` value or an AI context key in baggage never travels on |

## Identity scenarios

Run each scenario that applies to the service and check the entry span, the access log, the
audit log where there is one, and the metrics ([data-model.md](data-model.md#request-and-identity-context)).
Identity comes from the trusted auth or tenant middleware after authentication; a client header
that claims an identity changes nothing.

| Scenario | Passes when |
| --- | --- |
| Anonymous | a request without credentials has `correlation.id`, `http.route` and `client.address`, and no `user.id`, `tenant.id` or `session.id`: absent, never a placeholder such as `anonymous` or `0` |
| Authenticated | a signed-in request carries `user.id` (and `session.id` for session-based auth) on the entry span and in the access log; a request that sends `X-User-Id` or a similar header for another user still carries the authenticated user's id |
| Multi-tenant | each request carries its own `tenant.id`; the tenant comes from the verified token or the tenant resolver, never from a raw header or query parameter |
| Service-to-service | a call from another service carries the caller's trace and only the allow-listed baggage; the callee sets its own entry-span context from its own authentication (the service identity), not from the caller's baggage alone |
| Background job | a job run is a root span with its job attributes and no user or tenant context of the request that started it, unless the job's own code has them; a link points at the starting request where there is one |
| AI / MCP | the AI feature context reaches Langfuse from the module's stamps; MCP calls carry `params._meta` context, and no AI context travels in baggage ([workloads/mcp.md](workloads/mcp.md#verification)) |
| A2A | a call to a remote agent is a CLIENT `invoke_agent {agent}` span with `rpc.method` and `server.address`; the remote agent's SERVER span joins the same trace through `traceparent`; task ids and states appear as `app.a2a.*`; no message content on the ops path ([workloads/a2a.md](workloads/a2a.md)) |
| Concurrent-tenant leak | two concurrent streams of requests for tenant A and tenant B (or user A and user B), interleaved on the same instance, never put A's `user.id`, `tenant.id`, `session.id` or `correlation.id` on B's spans, logs, audit records or profile labels, including logs written from async callbacks, thread pools and job workers |

## Logs

| Check | Passes when |
| --- | --- |
| Native correlation | every record written inside a request, message or run carries native `traceId` and `spanId` — after collector mapping for stdout-shipped logs; a JSON `trace_id` field alone fails |
| Severity | native severity number and text follow the level policy; the access log uses ERROR for 5xx and WARN for 4xx ([signals/app-logs.md](signals/app-logs.md)) |
| Step logs | at `debug`, every wrapped call writes `started` and `finished` or `failed` with its duration; at `info` there are none; a call slower than `logging.application.slow_threshold` writes a WARN |
| One logger | application code writes nothing past the app's logger: no plain `console`, `print` or `fmt.Println` lines on stdout or stderr |
| Exception records | one ERROR record per failure with `exception.type`, `exception.message` and `exception.stacktrace`, arriving as one record, and no `exception` span event on the span; step logs carry span IDs at the policy's levels ([signals/errors.md](signals/errors.md)) |
| Access log | one record per request at response end with only the allow-listed headers, cookies and body fields; raw `Cookie` and `Set-Cookie` are never logged ([signals/access-log.md](signals/access-log.md)) |
| Responses | no stack trace reaches an HTTP response, and the existing error response format is unchanged |
| Audit log | where the service has audited actions, each writes one `audit.<action>` record with `app.audit.action`, `app.audit.outcome`, `app.audit.target.type`, `correlation.id`, `client.address` and the identity rows the code has ([data-model.md](data-model.md#logs-errors-and-trace-correlation)) |

## Metrics

| Check | Passes when |
| --- | --- |
| Endpoint | `/metrics` answers on the port the yml declares, never on the application port |
| Contract | the scrape has the default set with the contract's names, types and labels: `http_server_request_duration_seconds{method,route,status_code}`, `http_server_requests_total{method,route}`, `http_server_active_requests{method}`, the client, pool, WebSocket, probe and job metrics, and the library's process and runtime metrics; where they apply, the RPC, messaging, `auth_attempts_total{method,result}` (the service authenticates), MCP and A2A metrics ([signals/metrics.md](signals/metrics.md#the-default-set), [data-model.md](data-model.md#metrics)) |
| Buckets | duration histograms have the `le` bounds `0.005` to `60` of the standard list; `job_duration_seconds` adds `120`, `300`, `600` and `1800` |
| Labels | label values are bounded: methods, route templates or `unmatched`, dependency names, status codes; no user, session, tenant, organisation, account, customer, order, request, correlation, trace or span ids, raw URLs or paths, emails or free text |
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
| Visible fields | client IP (on the entry span and in the access and audit logs), user, tenant and session IDs and the selected body fields do appear; internal opaque IDs as they are |
| PII canaries | a test email, phone number, person name and postal address sent through the request appear only masked by the shared masking helper's rules (`j***@example.com`, `***0123`), never raw, in spans, logs, exception messages and profiles ([data-model.md](data-model.md#pii-masking)) |
| No bodies on spans | no span attribute contains a request or response body |
| Audit | the telemetry-safety audit reports nothing on the instrumented service; on a deliberately unsafe sample (a body as a span attribute, a user ID as a metric label, a token in baggage, raw SQL literals, header capture switched on in auto-instrumentation, a prompt logged, AI content set around the content helper, AI capture switched on in code, `user.id` read from a raw client header, a `request.id` attribute, `code.namespace` on a span, an email logged without the masking helper, a profile label that differs from the resource, a baggage allow-list over the limits) it reports every case with file, line and fix ([safe-capture.md](safe-capture.md)) |
| Content canaries | a canary in a prompt, a tool argument and a retrieved document reaches the Langfuse stream only in a run with `full` content, and appears nowhere in a production run ([GenAI](#genai)) |
| Fail closed | with a masking helper forced to fail, the value is omitted and the service keeps serving |

## Config wrapper

| Check | Passes when |
| --- | --- |
| Effective config | the loader turns `observability.yml` into the standard `OTEL_*` settings, checked by dumping the SDK's effective configuration ([config.md](config.md)) |
| Precedence | stray `OTEL_*` values in the environment are overwritten or removed, with one warning that names them but never their values; only `OTEL_SDK_DISABLED` is honoured, and operator headers follow the next row |
| Defaults | export is OTLP over gRPC with gzip, batching and retries |
| Operator headers | an `OTEL_EXPORTER_OTLP_HEADERS` value is mapped into the yml's headers or reported by name, never dropped silently ([config.md](config.md#precedence)) |
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
| Trace context | every Langfuse-bound observation carries the feature as trace name, the user, the conversation as session and the release; none of them in baggage; on the ops path no `langfuse.*` key |
| Who spent what | every Langfuse-bound span carries the user, team (where the app has one) and conversation the plan agreed, and every AI span on the ops path carries `gen_ai.conversation.id`; no other span carries a `gen_ai.*`, `llm.*` or `traceloop.*` key |
| Steps | retrieval steps carry `gen_ai.operation.name=retrieval`; a sub-agent call is one `invoke_agent` span; MCP calls follow [workloads/mcp.md](workloads/mcp.md#verification) |
| Model and usage | generations and embeddings carry the response model and usage, streams included; streamed generations have a time to first chunk |
| Status and level | the failing step is ERROR with `error.type`, and its status description on the ops path is that type, never the provider's message; degraded steps are WARNING with a constant status message; no span is OK; a streamed answer adds no ERROR record |
| Content | the `full` run has content on the Langfuse path only, within `max_chars`, which stays at or below the span attribute value limit (8192 and 8000 by default; both raised together only as a recorded exception); the production run has no content key anywhere and only redacted values for redacting features; the stray variables change nothing; the ops path, logs, metrics and span events never hold content, not even from a provider error that quotes the input (the stub's errors do) |
| Prompt links | generations that used a managed prompt (fetched by the app's own code through the module's client) link to its version; a fallback is WARNING and unlinked |
| Scores | in `hybrid` and `sdk`, feedback sent twice for one message is one score, sent by the server, with the resource's environment; in `otel`, no request reaches Langfuse's API |
| Modes | the same app, switched by `genai.langfuse.mode` alone, passes every row above in `hybrid`, `sdk` (generations from the Langfuse SDK, no GenAI instrumentation span) and `otel` (exports with the ingestion header, no Langfuse SDK loaded); each run has one span sender |
| Experiments | a runner over a two-item dataset leaves two traces with one root each, the experiment and item keys on their spans (none in the requests to the model provider), input, output and expected output on each root, the evaluator scores on the roots, environment `experiment`, and no span on the ops path; it refuses to start where content isn't `full` |
| Metrics | the GenAI metrics are on `/metrics` with bounded labels and the extended buckets; a 429 counts as `rate_limited` |
| Failure | with Langfuse down or slow, requests and the ops export are unaffected and one rate-limited WARN appears; shutdown flushes both within the grace period |

Optionally repeat through the real collector into a disposable Langfuse (its self-hosted
compose stack) and read the observations back through its v2 observations API
([backends/langfuse.md](backends/langfuse.md#self-hosting)).

## Browser RUM

For pages with Faro ([signals/rum.md](signals/rum.md)), driven by a browser (Playwright) against
the local receiver's `/collect`, or against Alloy, Loki and Tempo:

| Check | Pass when |
| --- | --- |
| Records | exceptions, error logs (`console.error`), events (session start, performance navigation and resources, `view_changed`, `faro.navigation`, `faro.user.action`, rage, dead and error clicks, the CSP violation, custom events) and measurements (LCP, CLS, INP, FCP, TTFB with attribution, custom) all arrive |
| Metas | `app.name`, `app.version`, `app.environment` equal the service rows; `sdk.*`; a session id on every record; `user.id` only when signed in |
| Views | `view.name` is the route template (`/orders/:id`) after each route change, never `default` past the first page |
| Errors | each failure is one exception: a handler error, an unhandled rejection, a render error reported by the route's error element; `console.error` is an error log, not a second exception. Through Alloy, stacks are de-minified |
| Traces | the page's fetch span and the API's `SERVER` span share one trace; only the listed API origins receive `traceparent`; an unsampled session sends none |
| Scrubbing | no canary (a URL query value, typed input, an email set on the user, a token in a message) in any batch, span or Loki line; emails in messages appear only masked; no `user.email` or `user.name` anywhere |
| Labels | Loki labels are `app_name`, `app_environment`, `kind` (and `service_name`); no id label |
| Transport | `faro`: the receiver answers 202 and CORS allows only the app origins. `proxy`: the page posts to its own origin, the proxy adds the key and strips cookies, `Referer` and `Authorization`. `otlp`: logs and traces arrive at the collector |
| Off | with `rum.enabled: false`, or a missing or non-JSON `observability.json`, the page sends nothing and works |
| Overhead | `scripts/rum_overhead.mjs` within budget ([overhead-and-security.md](overhead-and-security.md#browser-rum)) |

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

## Backend swap

One service is re-pointed from one backend to another — for example from Dynatrace to Tempo,
Loki, Prometheus and Pyroscope — by changing only the yml and the collector configuration. The
same checks pass, and the change contains no application code ([backends](backends/README.md),
[collector](collector/README.md)).

## Overhead and load

Required. Run the same load with telemetry on and with `OTEL_SDK_DISABLED=true` as the baseline,
at the configured sample ratio, with `scripts/overhead.py`, which exits non-zero over budget.
Export to the local receiver, never a real collector. The budget and the test are on
[overhead-and-security.md](overhead-and-security.md#budget).

| Check | Passes when |
| --- | --- |
| Latency | p50 rises by at most 5% or 1 ms, p99 by at most 10% or 5 ms, whichever is larger |
| CPU and memory | CPU rises by at most 10% (at least 2 points of a core) or by at most 1 ms per request, whichever rule passes; RSS by at most 50 MiB |
| Errors | the run with telemetry adds no errors |
| Drops | export queues drop nothing at nominal load |
| Report | the numbers of both runs and the budget used are in the report, with the reason for any limit the project changed |

Spans per request, log bytes per request and export-queue depth are recorded alongside, so a
change in volume shows before it breaks the budget.

- **Stubs answer with realistic latency:** the model provider, downstream APIs and the database
  at their production p50 when known, else tens of milliseconds for an API and hundreds for a
  model. Instant stubs make every relative number overstate the cost.
- **Over the CPU budget,** cut spans first: no handler span where the SERVER span already
  covers it, and one span for a tool call and a retrieval only when they are the same operation
  ([overhead-and-security.md](overhead-and-security.md#over-the-cpu-budget)).
- **The budget is never raised** to make a run pass unless the user agrees, and the report says
  why.

## Security of the instrumentation

The rules are on [overhead-and-security.md](overhead-and-security.md#security).

| Check | Passes when |
| --- | --- |
| Metrics bind | `/metrics` and any profiling or debug endpoint listen on loopback when the scraper is in the same pod, task or instance, and on `0.0.0.0` only when a remote scraper needs it, with a yml comment saying which; the port is not routed through a load balancer, ingress or public service |
| TLS | every exporter endpoint off a private hop uses TLS; a plaintext endpoint has a yml comment naming its private hop (loopback, a sidecar, or an in-cluster collector behind a NetworkPolicy or mesh mTLS) |
| Credentials | no auth header, API key or Langfuse key appears as a literal in code or the yml; each comes through `${file:...}` or `${env:...}`; the loader's warnings name variables, never values |
| Operator headers | an `OTEL_EXPORTER_OTLP_HEADERS` set by the operator reaches the exporter through the yml's `headers`, or the loader reports it; it is never dropped silently |
| Packaged config | the built image contains `observability.yml`, or the deployment manifest mounts it; otherwise the report lists a blocking handoff |
| No debug surfaces | no exporter zPages or debug endpoint is on in a production configuration |

[Documentation index](README.md)
