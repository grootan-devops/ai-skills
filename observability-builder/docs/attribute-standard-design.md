# Attribute and label classification: design notes

Developer guide for maintainers of this skill. It records how the attribute and label
classification (M, C, R, O, P per location) was designed and rolled out: the tables as they
were approved, the reasons behind each choice, and the rework and verification plan.
The agent never reads this file.

[references/data-model.md](../references/data-model.md) is the source of truth. A few rows changed
there after these tables were approved:

- The seven existing `websocket_*` metrics were kept instead of the two new ones in the metrics table.
- AI rows follow the skill's existing behaviour: `app.rag.documents_retrieved` and
  `app.rag.top_score`; legacy `gen_ai.prompt*` and `gen_ai.completion*` count as content on the
  Langfuse path; MCP `gen_ai.prompt.name` stays off the ops path.
- In plain Docker, `container.id` identifies the instance and `host.name` is optional.
- In audit logs, `user.id` and `tenant.id` are required only for an authenticated actor.
- Baggage can carry hints such as `correlation.id`, never trusted identity.

## Context

The observability-builder skill's standard ([references/data-model.md](../references/data-model.md))
says *where* each attribute goes, but not *how strongly it is required*. You want every
attribute in every location classified as MANDATORY (M), CONDITIONAL MANDATORY (C), RECOMMENDED (R),
OPTIONAL (O) or PROHIBITED (P). For each one, the standard will name who sets it and when.
Step 1 (this file) drafts those tables. Step 2 reworks the skill to match them.

The pasted brief was checked against the current standard and OpenTelemetry semantic conventions (semconv).
Where a row here differs from the brief, the note column says why. The main ones:

- **HTTP client 4xx stays ERROR.** OTel HTTP semconv makes a CLIENT span ERROR on 4xx and 5xx, and a
  SERVER span ERROR on 5xx only. The brief's "4xx not an error" applies to SERVER spans.
- **`code.namespace` is deprecated.** It is folded into a fully-qualified `code.function.name`
  (semconv ≥ 1.30).
- **`request.id` is not added.** `correlation.id` already covers it, and two IDs for one request split searches.
- **Tracing scope stays as agreed.** Spans cover the request's own work and its calls to other systems:
  - the entry span;
  - one INTERNAL span per request-path function: handlers, and service/repository functions that do I/O or heavy work. Each one nests under the function that called it;
  - CLIENT spans for third-party, database, cache and messaging calls;
  - root spans for jobs and WebSocket traffic.

  Start-up code, helpers/utilities and framework middleware layers get no spans. Function spans are
  created only inside an existing trace.

Columns: **Loc** = placement (Res = resource, Root = entry span (SERVER/CONSUMER local root), Child = child span,
Log, Metric label, Profile label). **Owner** = who sets it. **When** = the moment it is set.

---

## T1. Resource (one per process; identical on traces, logs, metrics, profiles)

Every attribute below that the platform makes available, with its class. Attributes not in this table are not added
(e.g. process ids and command lines, VCS data, release IDs, instance/UID duplicates).
**Pyroscope** = the static profile label written at profiler start (dots → underscores); only M and C rows
become profile labels. **E2E** = how the end-to-end run checks it: *live* on the local stack, *stub* with faked
platform env/metadata (no real cloud), *—* not asserted (R/O).

### Service & deployment

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `service.name` | string | `checkout-api` | M | observability.yml → SDK, process start | app name | live |
| `service.namespace` | string | `commerce` | M | observability.yml | `service_namespace` | live |
| `service.version` | string | `1.8.2` | M | CI/CD image tag → yml, build | `service_version` | live |
| `deployment.environment.name` | string | `production` | M | yml, deploy | `deployment_environment_name` | live |

### Kubernetes (`k8s.*`) — when running on Kubernetes

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `k8s.namespace.name` | string | `commerce-prod` | C (K8s) | downward API env | `k8s_namespace_name` | live |
| `k8s.pod.name` | string | `checkout-api-7d9c6b5f4-x2k8p` | C (K8s) | downward API env | `k8s_pod_name` | live |
| `k8s.node.name` | string | `worker-a1` | C (K8s) | downward API env | `k8s_node_name` | live |
| `k8s.cluster.name` | string | `prod-eu-1` | C (several clusters → one backend) | chart value per cluster | `k8s_cluster_name` | live |
| `k8s.deployment.name` / `k8s.statefulset.name` / `k8s.daemonset.name` | string | `checkout-api` | R (whichever owns the pod) | collector k8sattributes | — | — |
| `k8s.cronjob.name`, `k8s.job.name` | string | `nightly-report` | R (job pods) | collector k8sattributes | — | — |
| `k8s.replicaset.name` | string | `checkout-api-7d9c6b5f4` | O | collector k8sattributes | — | — |
| `k8s.container.name` | string | `app` | O (multi-container pods) | collector k8sattributes | — | — |

### Cloud (`cloud.*`) — when running on a cloud provider (ECS, Fargate, Lambda, Cloud Run, cloud VM, managed K8s)

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `cloud.provider` | string | `aws`, `gcp`, `azure` | C (cloud) | cloud detector | `cloud_provider` | stub |
| `cloud.platform` | string | `aws_ecs`, `aws_lambda`, `gcp_cloud_run` | C (cloud) | cloud detector | `cloud_platform` | stub |
| `cloud.region` | string | `eu-west-1` | C (cloud) | cloud detector | `cloud_region` | stub |
| `cloud.availability_zone` | string | `eu-west-1a` | R | cloud detector | — | — |
| `cloud.account.id` | string | `123456789012` | O | cloud detector | — | — |
| `cloud.resource_id` | string | function / task ARN | O | cloud detector | — | — |

### AWS (`aws.*`) — ECS / Fargate / EKS / Lambda

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `aws.ecs.cluster.arn` | string | ARN | C (ECS/Fargate) | ECS detector | `aws_ecs_cluster_arn` | stub |
| `aws.ecs.task.arn` | string | ARN | C (ECS/Fargate) — instance identity | ECS detector | `aws_ecs_task_arn` | stub |
| `aws.ecs.launchtype` | string | `fargate`, `ec2` | C (ECS/Fargate) | ECS detector | `aws_ecs_launchtype` | stub |
| `aws.ecs.task.family`, `aws.ecs.task.revision` | string | `checkout`, `42` | R | ECS detector | — | — |
| `aws.ecs.task.id`, `aws.ecs.container.arn` | string | | O | ECS detector | — | — |
| `aws.eks.cluster.arn` | string | ARN | O (EKS; `k8s.cluster.name` is the identity) | EKS detector | — | — |
| `aws.log.group.names`, `aws.log.stream.names` | string[] | `/ecs/checkout` | O (ECS/Lambda) | detector | — | — |

### FaaS (`faas.*`) — Lambda, Cloud Run, Cloud Functions, Azure Functions

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `faas.name` | string | `resize-img` | C (FaaS / Cloud Run service) | Lambda / GCP detector | `faas_name` | stub |
| `faas.version` | string | `$LATEST`, `checkout-00042` | C (FaaS) | detector | `faas_version` | stub |
| `faas.instance` | string | log stream / instance id | C (FaaS) — instance identity | detector | `faas_instance` | stub |

### GCP (`gcp.*`)

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `gcp.cloud_run.job.execution` | string | `report-abc12` | C (Cloud Run jobs) | GCP detector | `gcp_cloud_run_job_execution` | stub |
| `gcp.cloud_run.job.task_index` | int | `0` | C (Cloud Run jobs) | GCP detector | `gcp_cloud_run_job_task_index` | stub |
| `gcp.gce.instance.name` | string | `vm-12` | R (GCE VM) | GCP detector | — | — |

### Host & container — VM and plain Docker

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `host.name` | string | `vm-12` | C (VM, plain Docker host) — instance identity on VM | host detector | `host_name` | live |
| `container.id` | string | `3f1c…` | C (plain Docker) — instance identity in Docker | container detector | `container_id` | live |
| `host.type` | string | `n2-standard-4` | O (cloud VM) | cloud detector | — | — |
| `host.arch` | string | `amd64` | O | host detector | — | — |
| `container.name` | string | `checkout-api-1` | O (plain Docker) | container detector | — | — |
| `container.image.name`, `container.image.tags` | string / string[] | `checkout-api`, `[1.8.2]` | O | detector / chart | — | — |

### Telemetry, process, OS

| Attribute | Type | Example | Class | Owner / when | Pyroscope | E2E |
| --- | --- | --- | --- | --- | --- | --- |
| `telemetry.sdk.language` | string | `python` | R | SDK default | — | live (present) |
| `telemetry.sdk.version` | string | `1.39.0` | R | SDK default | — | live (present) |
| `process.runtime.name` | string | `cpython`, `nodejs` | R | runtime detector | — | live (present) |
| `process.runtime.version` | string | `3.12.4` | R | runtime detector | — | live (present) |
| `os.type` | string | `linux` | R | os detector | — | live (present) |
| `os.description`, `os.name`, `os.version` | string | | O | os detector | — | — |

Rule: **any per-request value is never a resource attribute** — for example `user.id`, `tenant.id`, `session.id`,
`correlation.id`, trace or span IDs, routes, and every other value that differs between requests.

### Instance identity per platform

| Platform | Mandatory rows beyond the four service rows | Instance identity |
| --- | --- | --- |
| Kubernetes | `k8s.namespace.name`, `k8s.pod.name`, `k8s.node.name` | `k8s.pod.name` (+ namespace) |
| ECS / Fargate | `cloud.provider/platform/region`, `aws.ecs.cluster.arn`, `aws.ecs.task.arn`, `aws.ecs.launchtype` | `aws.ecs.task.arn` |
| Lambda | `cloud.*` (3), `faas.name`, `faas.version`, `faas.instance` | `faas.instance` |
| Cloud Run service | `cloud.*` (3), `faas.name`, `faas.version`, `faas.instance` | `faas.instance` |
| Cloud Run job | `cloud.*` (3), `faas.name`, `gcp.cloud_run.job.execution`, `gcp.cloud_run.job.task_index` | execution + task index |
| VM | `host.name`, plus `cloud.*` (3) on a cloud VM | `host.name` |
| Plain Docker | `host.name`, `container.id` | `container.id` |

## T2. Trace context (native fields, not attributes)

| Field | Class | Owner | Rule |
| --- | --- | --- | --- |
| `trace_id`, `span_id`, kind, name, start/end, status | M | SDK | name low-cardinality, never IDs or raw URLs |
| `parent_span_id` | C (continuing a trace) | SDK, from `traceparent` | |
| `trace_flags` | M | SDK | sampled bit drives log correlation |
| `tracestate` | O | propagator | pass through untouched |
| Links | C (batch consume, fan-in, job started by a request, WS message → connection) | instrumentation | |
| Events | C (`exception` on a failure) | SDK `recordException` | |
| Baggage | O, allow-list only | middleware | max 8 keys / 512 B; never secrets, never authorisation, never AI context |

## T3. Request & identity context — per location

Set by trusted auth/tenant middleware after authentication, never from raw client headers.
Root = entry span only; child spans don't repeat it.

**Core vs reference set.** The core rows are `user.id`, `tenant.id`, `session.id`, `correlation.id`, `http.route`,
`client.address`, UA and client app. They are classified as shown and checked. Every other identity or business row is a
**reference catalogue**: the names and placements to use *when the code actually has that data*. The
audit adds such a row only if the service's auth/session/tenant code exposes the value. The classes shown for
those rows (C/O) apply only once the code has the value. Nothing is fabricated or forced to match
across services, and the e2e checks don't require them.

| Attribute | Type | Res | Root | Child | App log | Access log | Audit log | Metric | Profile | Required when / note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| *core* | | | | | | | | | | |
| `user.id` | string | P | C | P | O | C | M | P | P | authenticated request; anonymous → absent, never fabricated |
| `tenant.id` | string | P | C | P | O | C | M (multi-tenant) | P | P | multi-tenant request; custom name |
| `session.id` | string | P | C | P | O | C | C | P | P | session-based request; semconv (dev) |
| `correlation.id` | string | P | M | P | M | M | M | P | P | from valid `x-correlation-id` or generated; custom |
| *reference catalogue below — only when the code has the value* | | | | | | | | | | |
| `user.roles` | string[] | P | O | P | O | O | C | P | P | semconv name (dev); brief's `user.role` → `user.roles` |
| `app.user.type` | string (enum) | P | O | P | O | O | R | O (bounded) | P | `human`/`service`/`system`; custom |
| `app.tenant.tier` / `app.subscription.plan` | string (enum) | P | O | P | O | O | O | O (bounded) | P | billing/plan-scoped ops; custom |
| `tenant.name`, `app.organization.name` | string | P | O | P | P | P | O | P | P | display names: PII-ish, prefer IDs |
| `app.organization.id`, `app.account.id`, `app.customer.id` | string | P | C | P | O | C | C | P | P | only when the request is scoped by it and it differs from tenant.id |
| `request.id` | — | P | P | P | P | P | P | P | P | not used: correlation.id covers it |
| `app.auth.method`, `app.auth.provider` | string (enum) | P | C | P | O | C | M | O (bounded) | P | auth operation (login, token exchange); custom |
| `app.auth.result` | string (enum) | P | C | P | O | C | M | C (on `auth_attempts_total`) | P | `success`/`denied`/`error`; custom |
| `http.route` | string | P | M (HTTP) | P | O | M | O | M (`route`) | P | template or `unmatched` |
| `client.address` | string | P | C (HTTP/WS) | P | O | M | M | P | P | via trusted-proxy resolver |
| `user_agent.original` | string | P | R | P | P | R | R | P | P | |
| `app.client.name` / `app.client.version` | string | P | R | P | O | R | O | O (bounded list) | P | custom |
| `langfuse.*` stamps | string | P | C (AI feature, Langfuse path) | C (every span in the feature) | P | P | P | P | P | the one scoped exception |

## T4. Operation spans (Mandatory / Conditional / Recommended / Optional / Prohibited)

Placement is Child unless the kind is SERVER/CONSUMER, in which case it is Root. Owner is auto-instrumentation,
unless "module" (the skill's generated client/GenAI/MCP module) or "code" (the function wrapper) is given.

| Operation | Kind / name | M | C | R | O | P | Result & status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HTTP server | SERVER `{method} {http.route}` | `http.request.method`, `http.route`, `url.scheme`, `url.path` (sanitised) | `http.response.status_code` (response sent), `server.address`/`server.port` (known), `client.address` | `user_agent.original`, `network.protocol.version` | `url.query` (allow-listed values), `http.request.header.<n>` (allow-list), body sizes | raw query, `Authorization`, `Cookie`, bodies | `error.type`; ERROR on 5xx / escaped exception |
| HTTP client | CLIENT `{method} {url.template}` | `http.request.method`, `server.address`, `server.port`, `url.full` (sanitised) | `http.response.status_code`, `url.template` (known), `http.request.resend_count` (retry ≥1) | `service.peer.name` | `network.protocol.version` | credentials in URL, token query params | `error.type`; ERROR on 4xx/5xx |
| Call to another service (module) | CLIENT `{service.peer.name} {operation}` | `service.peer.name`, `server.address` | `server.port`, `network.transport` (non-HTTP) | safe operation attrs | | args/payloads | `error.type`; HTTP client nests under it |
| Database | CLIENT `{db.query.summary}` / `{op} {collection}` | `db.system.name`, `db.operation.name` (known) | `db.collection.name`, `db.namespace`, `server.address`, `server.port`, `db.response.status_code` (error) | `db.query.summary` | `db.query.text` (parameterised) | parameter values, credentials | `db.response.returned_rows`, `error.type` |
| Cache | CLIENT `{op} {db.namespace}` | `db.system.name` (`redis`…), `db.operation.name` | `db.namespace`, `server.address`, `server.port` | `app.cache.hit` | | keys holding PII | `error.type` |
| Messaging producer | PRODUCER `send {destination}` | `messaging.system`, `messaging.destination.name`, `messaging.operation.type`, `messaging.operation.name` | `messaging.batch.message_count` (batch), `messaging.destination.partition.id` | `messaging.message.id`, `server.address` | `messaging.message.conversation_id` | payload | `error.type`; context injected into headers |
| Messaging consumer | CONSUMER `process {destination}` (Root) | same as producer | `messaging.consumer.group.name` (groups), links (batch) | `messaging.message.id`, partition | `app.messaging.attempt` | payload | `error.type`; DLQ outcome |
| RPC / gRPC | SERVER/CLIENT `{rpc.method}` | `rpc.system.name`, `rpc.method` | `rpc.response.status_code` (response), `server.address`/`port` (client) | `network.peer.*` | | metadata values | `error.type`; gRPC status mapping per semconv |
| GraphQL operation | SERVER/INTERNAL `{type} {name}` | `graphql.operation.type` | `graphql.operation.name` (named op) | | `graphql.document` (sanitised, off by default) | variables | `error.type` from errors list |
| GraphQL resolver | — | — | — | — | — | **P (no spans)** | resolver spans off: tracing scope keeps API + client calls only |
| WebSocket connection | SERVER root `WS {route}` | `url.path`/route, `client.address` | `user.id`, `tenant.id` (after auth), `app.websocket.close_code` (rejected) | | | tokens in query | UNSET on client rejection; ERROR on server failure |
| WebSocket message | SERVER root `WS {type}`, link → connection | `app.websocket.message.type` (bounded) | `user.id`, `tenant.id` (authenticated) | | | payload; pings get no span | `error.type` |
| GenAI model call | CLIENT `{op} {model}` | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` | `gen_ai.response.model`, `gen_ai.usage.input_tokens`/`output_tokens` (returned), `gen_ai.conversation.id` (conversation) | `gen_ai.response.finish_reasons`, `gen_ai.response.id`, request params | `gen_ai.prompt.name`, `app.genai.prompt.version`, TTFC | prompts/completions on ops path | `error.type`; WARNING level for degraded success |
| GenAI agent / workflow | INTERNAL `invoke_agent {agent}` | `gen_ai.operation.name`, `gen_ai.agent.name` | `gen_ai.agent.id`, `gen_ai.conversation.id` | `gen_ai.workflow.name` | `app.genai.*` | content | `error.type` |
| GenAI tool | INTERNAL/CLIENT `execute_tool {tool}` | `gen_ai.operation.name`, `gen_ai.tool.name` | `gen_ai.tool.call.id` (model issued one) | `gen_ai.tool.type` | | arguments/results (Langfuse path only, by policy) | `error.type` |
| Retrieval / RAG | INTERNAL/CLIENT `retrieval {source}` | `gen_ai.operation.name=retrieval`, `gen_ai.data_source.id` | `server.address` (remote store) | `gen_ai.retrieval.top_k`, result count | `app.rag.*` | query text, documents | `error.type` |
| MCP client / server | CLIENT/SERVER `{mcp.method.name} {target}` | `mcp.method.name` | `gen_ai.tool.name` (tools/call), `gen_ai.prompt.name` (prompts/get), `mcp.resource.uri` sanitised (resources/read), `mcp.session.id` (session), `server.*` (client), `client.*` (server) | `jsonrpc.request.id`, `mcp.protocol.version`, `network.transport` | | tool args/results | `rpc.response.status_code`, `error.type`; server leaves caller errors UNSET |
| Cloud SDK / object storage | CLIENT | `rpc.system.name`, `rpc.method` | `cloud.region`, `aws.s3.bucket` etc. (applicable) | | object keys (if not sensitive) | credentials, signed URLs | `http.response.status_code`, `error.type` |
| Serverless invocation | SERVER/CONSUMER (Root) | `faas.trigger` | `faas.invocation_id`, `faas.coldstart` (first call) | | | event payload | `error.type` |
| Job run | INTERNAL root `job {name}` | `app.job.name`, `app.job.trigger` | link to the starting request (request-started) | `app.job.items.processed`/`failed` | `app.job.run.id` | | ERROR when the run failed |
| Application function (code) | INTERNAL `{Class}.{method}` | `code.function.name` (fully qualified) | | `code.file.path`, `code.line.number` | `thread.name`, allow-listed arg summaries, enrichment attrs | raw args, `code.namespace` (deprecated) | result summary, `error.type` |

## T5. Logs

| Group | Field | App log | Access log | Error log | Audit log | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| Native | `timeUnixNano`, `severityNumber`, `severityText`, `body` | M | M | M | M | logger / bridge |
| Native | `observedTimeUnixNano` | R | R | R | R | bridge / collector |
| Native | `traceId`, `spanId`, `flags` | C (inside a span) | M | C | C | log bridge, or collector for stdout |
| Native | `eventName` | O | M (`access_log`) | M (`exception`) | M (`audit.<action>`) | code / hooks |
| Resource | T1 set | M | M | M | M | SDK or collector |
| Scope | logger name/version | M | M | M | M | bridge |
| Attr | `correlation.id` | M | M | M | M | hooks |
| Attr | identity context: the T3 rows the code has, for example `user.id`, `tenant.id`, `session.id`, `user.roles`, `app.organization.id` | O | C (T3) | O | M (those the code has) | middleware |
| Attr | `exception.type`, `exception.message` (sanitised) | — | — | M | C | error hook |
| Attr | `exception.stacktrace` | — | — | R | O | error hook |
| Attr | `code.function.name`, `code.file.path`, `code.line.number` | R | O | R | O | bridge |
| Attr | `thread.id`, `thread.name` | O | O | O | O | bridge |
| Attr | HTTP span attrs (method, route, status, sizes, client, UA) | P | M | O | C | access-log middleware |
| Attr | allow-listed `http.request.header.<n>` / `http.response.header.<n>` / `http.request.cookie.<n>` | P | O | P | P | middleware |
| Attr | `app.audit.action`, `app.audit.outcome`, `app.audit.target.type` | P | P | P | M | code |
| Attr | `app.auth.method`, `app.auth.result` | P | C | P | C (auth events) | auth middleware |
| Secrets | passwords, tokens, API keys, session cookies, raw `Cookie`/`Set-Cookie`, `Authorization`, private keys, card numbers | P | P | P | P | never logged, not even masked |
| PII | any personal value logged in any field or message | C: masked (T5a) | C: masked | C: masked | C: masked | the logging helper, before the record is written |

### T5a. PII masking (applies to logs, span attributes and exception messages)

PII may appear only in masked form, through the shared masking helper. If masking fails, the value is dropped,
never written raw. The audit flags any raw PII write.

| Data | Rule | Example |
| --- | --- | --- |
| Email | first character + `***` + domain | `jane.doe@example.com` → `j***@example.com` |
| Phone | last 4 digits | `+44 7700 900123` → `***0123` |
| Person name | first character of each part | `Jane Doe` → `J*** D***` |
| Postal address | city + country only | `12 High St, Leeds, UK` → `Leeds, UK` |
| Government ID, passport, tax ID | last 4 | `AB123456C` → `*****456C` |
| Bank account / IBAN | last 4 | `GB29NWBK60161331926819` → `****6819` |
| Date of birth | year only | `1990-04-12` → `1990` |
| IP address (`client.address`) | kept in access/audit logs and on the entry span; last octet masked elsewhere | `203.0.113.27` → `203.0.113.0` |
| Internal opaque IDs (`user.id`, `tenant.id`, order IDs) | not masked (pseudonymous) | `u-123` |
| Free text, request/response bodies | not logged; allow-listed fields only, each masked by the rules above | — |
| Card numbers, CVV, passwords, tokens | **P**: never logged, not even masked | — |

## T6. Metrics (Prometheus client; scraped `/metrics`)

Names are this standard's Prometheus names. The OTel semconv instrument is given for mapping.

| Metric (type, unit) | OTel equivalent | Required labels | Optional labels |
| --- | --- | --- | --- |
| `http_server_request_duration_seconds` (histogram, s) | `http.server.request.duration` | `method`, `route`, `status_code` | — |
| `http_server_requests_total` (counter) | — | `method`, `route` | — |
| `http_server_active_requests` (gauge) | `http.server.active_requests` | `method` | — |
| `http_client_request_duration_seconds` (histogram, s) | `http.client.request.duration` | `service`, `method`, `route`, `status_code` | — |
| `http_client_active_requests` (gauge) | — | `service`, `method`, `route` | — |
| `rpc_server_duration_seconds` / `rpc_client_duration_seconds` (histogram) — **new** | `rpc.server.duration` | `method`, `status_code` | `service` |
| `messaging_process_duration_seconds` (histogram), `messaging_messages_total` (counter) — **new** | `messaging.process.duration` | `system`, `destination` (bounded), `operation` | `outcome` |
| `db_pool_connections*`, `db_pool_wait_duration_seconds` | `db.client.connection.*` | — | `pool` (several pools) |
| `websocket_connections_active` (gauge), `websocket_messages_total` (counter) | — | — | `type` (bounded) |
| `job_runs_total` / `job_duration_seconds` | — | `job_name` (+`outcome` on counter) | — |
| `gen_ai_client_operation_duration_seconds` | `gen_ai.client.operation.duration` | `provider`, `model`, `operation`, `outcome` | — |
| `gen_ai_client_tokens_total` | `gen_ai.client.token.usage` | `provider`, `model`, `token_type` | — |
| `gen_ai_client_time_to_first_chunk_seconds` | — | `provider`, `model` | — |
| `mcp_server_operation_duration_seconds` / `mcp_client_operation_duration_seconds` (histogram) — existing | `mcp.server.operation.duration` / `mcp.client.operation.duration` | `method`, `target` (fixed list), `outcome`; client adds `server` | — |
| `a2a_client_request_duration_seconds` / `a2a_server_request_duration_seconds` (histogram) — **new**, see T9 | — | `agent`, `method`, `outcome` | — |
| `auth_attempts_total` (counter) — **new, C (service authenticates)** | — | `method`, `result` | `provider` |
| `app_health_probe_failures_total` | — | `probe`, `dependency` | — |
| process/runtime defaults | runtime semconv | library defaults | — |
| business metrics | — | team-defined | bounded values (`plan`, `tier`, `user_type`) after cardinality review |

The following are **PROHIBITED as labels**: user, tenant, org, account, customer, session, request, correlation, trace and
span IDs; raw URLs and paths; SQL; error messages; emails; free text. Exemplars are optional and only used
where both the client library and the backend support them.

## T7. Pyroscope profiling

Profiles carry no span-style attributes. They carry **labels**: static identity labels set once at profiler start,
plus a few bounded dynamic labels around hot code. Each distinct label set is a separate profile series.

### T7a. Profile types (what is collected)

| Profile type | Class | Runtimes | Note |
| --- | --- | --- | --- |
| CPU | M (profiling enabled) | all | |
| Allocation / heap (alloc objects/space, inuse) | R where the SDK supports it | Go, Java, .NET, Node (heap) | Python SDK: CPU only |
| Lock / mutex / block contention | O | Go, Java, .NET | |
| Goroutines / threads | O | Go | |
| Wall-clock | O | Node, Java | useful for I/O-bound services |

### T7b. Labels

| Label | Kind | Class | Owner / when |
| --- | --- | --- | --- |
| app name = `service.name` | static | M | profiler start, from the same yml values as the resource |
| `service_namespace`, `service_version`, `deployment_environment_name` | static | M | profiler start |
| platform rows of T1 (`k8s_*`, `cloud_*`, `aws_ecs_*`, `faas_*`, `gcp_cloud_run_job_*`, `host_name`, `container_id`) | static | C (same condition as in T1) | profiler start, from the same env |
| R/O resource rows (`telemetry_*`, `process_runtime_*`, `os_type`, …) | static | P | not profile labels: identity is enough |
| `controller` | dynamic | C (code wrapped as a profiled hot path) | code, labels API |
| `operation` | dynamic | O (bounded name inside a controller) | code, labels API |
| `span_id` (`pyroscope.profile.id` on the span) | correlation | C (runtime has a span-profile integration: Go, Java, Python, .NET; none for Node.js) | integration only |
| `trace_id`/`span_id` set by hand; any user, tenant, session, request or correlation ID; route with IDs; free text | — | P | — |

### T7c. Which operations get a `controller` label

| Operation (T4) | Profiling label | Why |
| --- | --- | --- |
| HTTP server entry | O: `controller` = route template | only when the service is CPU-bound per route |
| Application function, CPU/memory/lock heavy (parsing, conversion, serialisation, compression, crypto, image/report generation, PII redaction, tokenisation, big loops) | C: `controller` = `{Class}.{method}` | attributes CPU/memory to the code |
| Job run | R: `controller` = `job {name}` | batch work is usually CPU-heavy |
| GenAI prompt assembly / stream parsing | C (when heavy) | |
| HTTP/DB/cache/messaging/RPC/MCP client calls, I/O-bound functions | P (no label) | the samples are idle time; spans already time them |
| Start-up, helpers, middleware | P | out of scope |

Runtime note: in Node.js a label covers synchronous work only (it doesn't cross `await`).

## T8. Cross-signal correlation & propagation

| Pair / transport | Mechanism | Class |
| --- | --- | --- |
| trace ↔ log | native `traceId`/`spanId` on records | M |
| trace ↔ metric | `service.name` + `route` + time; exemplars | M / O |
| trace ↔ profile | span-profile integration `span_id` | C (supported runtime) |
| log ↔ profile | resource identity + time | M |
| user/tenant ↔ trace | entry-span attrs (T3) | C |
| HTTP, GraphQL-over-HTTP | `traceparent`, `tracestate`, allow-listed `baggage` headers | M |
| gRPC | metadata, same keys | M |
| Messaging | message headers; links for batches | M |
| WebSocket | handshake headers; app messages carry `traceparent` field if the client sends one | C |
| MCP | `params._meta.traceparent` / `tracestate` | M |
| A2A | HTTP `traceparent` / `tracestate` headers on the JSON-RPC call | M |
| GenAI agent ↔ tool / MCP / A2A / model calls | parent-child spans in one trace; same Langfuse trace via the feature stamps | M |

## T9. AI: GenAI, LLM, agents, RAG, MCP, A2A

There are two paths. The **ops path** (OneUptime/Grafana etc.) gets operational attributes only. The **Langfuse path** also gets
content, but only through the module's content helper, under `genai.content.capture` (`off` / `redacted` / `full`).
Columns: **Ops** / **LF** = class on each path. Names are from OTel GenAI semconv (development status, emitted under
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`); `app.*` are custom; `langfuse.*` are Langfuse's.

### T9a. AI feature context (set by `genai.feature()` from what the app's code has)

| Attribute | Type | Example | Ops | LF | Where | Note |
| --- | --- | --- | --- | --- | --- | --- |
| `langfuse.trace.name` | string | `support-chat` | P | M | every span in the feature | the feature name |
| `langfuse.user.id` | string | `u-123` | P | C (code has a user) | every span in the feature | |
| `langfuse.session.id` | string | `conv-88` | P | C (conversation) | every span in the feature | the conversation, not the login session |
| `gen_ai.conversation.id` | string | `conv-88` | C (conversation) | C | AI spans | same value as above |
| `langfuse.trace.metadata.team` | string | `support` | P | O | every span in the feature | |
| `langfuse.release` / `langfuse.environment` | string | `1.8.2` / `production` | P | M | every span | from `service.version` / `deployment.environment.name` |
| `langfuse.trace.tags`, `langfuse.trace.metadata.<key>` | string[] / string | `["beta"]` | P | O | every span | bounded values |
| any AI context in baggage | — | — | P | P | — | baggage reaches the model provider |

### T9b. Model call: chat, completion, embeddings (kind CLIENT, `{gen_ai.operation.name} {gen_ai.request.model}`)

| Attribute | Type | Example | Ops | LF | Owner |
| --- | --- | --- | --- | --- | --- |
| `gen_ai.operation.name` | string | `chat`, `text_completion`, `embeddings`, `generate_content` | M | M | instrumentation / module |
| `gen_ai.provider.name` | string | `openai`, `anthropic`, `aws.bedrock`, `azure.ai.openai`, `gcp.vertex_ai` | M | M | instrumentation |
| `gen_ai.request.model` | string | `gpt-4o-mini` | M | M | instrumentation |
| `gen_ai.response.model` | string | `gpt-4o-mini-2024-07-18` | C (returned) | C | instrumentation |
| `gen_ai.response.id` | string | `chatcmpl-…` | R | R | instrumentation |
| `gen_ai.response.finish_reasons` | string[] | `["stop"]` | R | R | instrumentation |
| `gen_ai.output.type` | string | `text`, `json`, `image` | O | O | instrumentation |
| `gen_ai.request.max_tokens`, `.temperature`, `.top_p`, `.top_k`, `.frequency_penalty`, `.presence_penalty`, `.seed`, `.stop_sequences`, `.choice.count` | number / string[] | `0.2` | O (when set by the code) | O | instrumentation |
| `gen_ai.request.encoding_formats`, `gen_ai.embeddings.dimension.count` | string[] / int | `["float"]`, `1536` | O (embeddings) | O | instrumentation |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | int | `812`, `154` | C (provider returns usage) | C | instrumentation |
| `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.reasoning.output_tokens` | int | `600` | C (returned) | C | instrumentation |
| `gen_ai.usage.cost` | double (USD) | `0.0031` | P | C (provider reports it) | module; never estimated |
| `gen_ai.response.time_to_first_chunk` | double (s) | `0.42` | C (streaming) | C (`langfuse.observation.completion_start_time`) | module |
| `gen_ai.prompt.name`, `app.genai.prompt.version` | string | `triage`, `7` | C (managed prompt) | C (`langfuse.observation.prompt.name/version`) | module |
| `server.address`, `server.port` | string / int | `api.openai.com`, `443` | R | R | instrumentation |
| `error.type` | string | `rate_limit`, `timeout` | C (failed) | C | instrumentation |
| `langfuse.observation.level` | string | `WARNING` | P | C (degraded success: fallback, truncated, guardrail block) | module |
| `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages` | JSON | — | **P** | C (capture mode allows; redacted/sampled/capped) | content helper only |
| legacy `gen_ai.prompt*`, `gen_ai.completion*` | — | — | P | P | dropped by the content guard |

### T9c. Agent, tool, retrieval, memory, workflow, guardrail, evaluation

| Step | Kind / name | Mandatory | Conditional | Recommended / optional | Content (LF only, by policy) | Langfuse type |
| --- | --- | --- | --- | --- | --- | --- |
| Agent | INTERNAL (CLIENT for a remote agent) `invoke_agent {gen_ai.agent.name}` | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` | `gen_ai.agent.id` (has one), `gen_ai.conversation.id` | R `gen_ai.agent.version`; O `gen_ai.agent.description` | input/output messages | agent |
| Agent creation | INTERNAL `create_agent {name}` | `gen_ai.operation.name=create_agent`, `gen_ai.agent.name` | `gen_ai.agent.id` | | | — |
| Planning step | INTERNAL `plan {agent}` | `gen_ai.operation.name` | | | plan text | chain |
| Tool call | INTERNAL (CLIENT if remote) `execute_tool {gen_ai.tool.name}` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name` | `gen_ai.tool.call.id` (model issued it) | R `gen_ai.tool.type` (`function`, `extension`, `datastore`); O `gen_ai.tool.description` | `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `gen_ai.tool.definitions` | tool |
| Retrieval / RAG | INTERNAL or CLIENT `retrieval {gen_ai.data_source.id}` | `gen_ai.operation.name=retrieval`, `gen_ai.data_source.id` | `server.address` (remote store), `db.system.name` (vector DB) | R `gen_ai.retrieval.top_k`, `app.rag.documents.count`; O `app.rag.strategy`, `app.rag.score.min` | `gen_ai.retrieval.query.text`, `gen_ai.retrieval.documents` | retriever |
| Memory | INTERNAL/CLIENT `{memory operation} {store}` | `gen_ai.operation.name`, `gen_ai.memory.store.id` | `gen_ai.memory.record.count` (read/write) | O `gen_ai.memory.record.id` | `gen_ai.memory.query.text`, `gen_ai.memory.records` | retriever |
| Workflow | INTERNAL `invoke_workflow {gen_ai.workflow.name}` | `gen_ai.operation.name`, `gen_ai.workflow.name` | | | | chain |
| Guardrail | INTERNAL `guardrail {name}` | `app.genai.guardrail.name`, `app.genai.guardrail.result` (`pass`/`block`/`flag`) | WARNING level on block | | blocked text | guardrail |
| Evaluation | INTERNAL `evaluate {gen_ai.evaluation.name}` | `gen_ai.evaluation.name` | `gen_ai.evaluation.score.value` or `.score.label` | | `gen_ai.evaluation.explanation` | evaluator |
| Experiment run | — | — | `langfuse.experiment.*` (only when mode is `full`) | | expected output | — |

Prohibited everywhere: prompt or user text in span names, content in logs/metrics/span events/baggage, and
any content on the ops path.

### T9d. MCP (client and server; context via `params._meta`)

| Attribute | Type | Example | Class | Applies to |
| --- | --- | --- | --- | --- |
| `mcp.method.name` | string | `tools/call`, `resources/read`, `prompts/get`, `initialize` | M | all |
| `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name` | string | `search_orders` | C (`tools/call`) | client + server |
| `gen_ai.prompt.name` | string | `summarise` | C (`prompts/get`) | client + server |
| `mcp.resource.uri` | string | `file:///{path}` (sanitised or template) | C (`resources/*`) | client + server |
| `mcp.session.id` | string | `sess-…` | C (session transport) | client + server |
| `mcp.protocol.version` | string | `2025-06-18` | R | client + server |
| `jsonrpc.request.id` | string | `17` | R | client + server |
| `jsonrpc.protocol.version` | string | `2.0` | O | client + server |
| `network.transport` | string | `pipe` (stdio), `tcp` | R | client + server |
| `network.protocol.name` / `.version` | string | `http` / `2` | O (HTTP transports) | client + server |
| `server.address`, `server.port` | string / int | `mcp.internal`, `443` | C (network transport) | client |
| `client.address`, `client.port` | string / int | | C (network transport) | server |
| `rpc.response.status_code` | string | `-32602` | C (JSON-RPC error) | client + server |
| `error.type` | string | `tool_error`, `-32601` | C (failure; `tool_error` for `isError` results) | client + server |
| tool arguments / results, resource contents, prompt text | — | — | P on ops; LF by content policy | — |

Status: the client marks every JSON-RPC error ERROR. The server leaves the caller's errors (-32700, -32600, -32601,
-32602, -32002) UNSET.

### T9e. A2A (agent-to-agent protocol; JSON-RPC over HTTP) — **new, custom attributes**

OTel has no `a2a.*` conventions yet. The calling agent's span is an agent invocation (T9c), and the protocol fields are custom `app.a2a.*`.

| Attribute | Type | Example | Class | Applies to |
| --- | --- | --- | --- | --- |
| span kind / name | — | CLIENT `invoke_agent {remote agent}` on the caller; SERVER `{a2a method}` on the remote agent | M | both |
| `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` (remote agent's card name) | string | `billing-agent` | M | client |
| `rpc.system.name=jsonrpc`, `rpc.method` | string | `message/send`, `message/stream`, `tasks/get`, `tasks/cancel` | M | both |
| `app.a2a.task.id` | string | `task-…` | C (task created) | both |
| `app.a2a.context.id` | string | `ctx-…` | C (multi-turn) | both |
| `app.a2a.task.state` | string | `submitted`, `working`, `input-required`, `completed`, `failed`, `canceled` | C (task returned) | both |
| `app.a2a.protocol.version` | string | `0.3.0` | R | both |
| `server.address`, `server.port`, `url.template` | | | M (client) | client |
| `jsonrpc.request.id` | string | | R | both |
| `error.type`, `rpc.response.status_code` | string | `-32001` (task not found) | C (failure) | both |
| message parts / artifacts content | — | — | P on ops; LF by content policy | — |

Propagation: W3C `traceparent` in the HTTP headers. Streaming (`message/stream`, SSE) stays on one span until the stream ends.

### T9f. AI metrics, logs and profiling

| Signal | Item | Class | Labels / fields |
| --- | --- | --- | --- |
| Metric | `gen_ai_client_operation_duration_seconds` | M (GenAI used) | `provider`, `model` (fixed list, else `other`), `operation`, `outcome` |
| Metric | `gen_ai_client_tokens_total` | C (usage returned) | `provider`, `model`, `token_type` (`input`, `output`, `cache_read`, `cache_creation`, `reasoning`) |
| Metric | `gen_ai_client_time_to_first_chunk_seconds` | C (streaming) | `provider`, `model` |
| Metric | `mcp_server_/mcp_client_operation_duration_seconds` | C (MCP) | T6 |
| Metric | `a2a_client_/a2a_server_request_duration_seconds` | C (A2A) — new | `agent`, `method`, `outcome` |
| Metric | cost | P as a metric label or metric on the ops path | cost lives in Langfuse |
| Log events | `genai.feedback.received`, `guardrail.blocked`, `rag.no_documents`, AI errors | C (event occurs) | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `error.type`; never content |
| Profiling | prompt assembly, tokenisation, stream parsing, PII redaction of AI content | C (heavy) | `controller` label (T7c) |
| Profiling | model / MCP / A2A calls | P (no label) | I/O-bound |

## Ownership timeline (who sets what, when)

| Time | Attributes |
| --- | --- |
| build (CI) | `service.version` |
| deploy (chart) | `deployment.environment.name`, `k8s.*`, `k8s.cluster.name` |
| process start (SDK/loader) | resource, profiler static labels, semconv opt-ins |
| request entry (hooks) | trace context, `correlation.id`, `http.route`, `client.address`, UA, client app |
| authentication / tenant resolution | `user.id`, `user.roles`, `tenant.id`, `session.id`, `app.auth.*` |
| operation (auto-instr / modules / code) | T4 |
| error handling | `error.type`, status, `exception.*` |
| log emission | T5 |
| collector | stdout log parsing, resource for scraped/stdout signals, k8s association, redaction backstop |

---

## Step 2 — rework the skill (after you approve the tables)

Done via the skill-creator loop: draft, then evals, then viewer, then iterate. All changes stay generic and are not committed.

1. **`references/data-model.md`**: replace the current tables with T1–T9 plus the timeline (T9 also updates `signals/genai.md`, `workloads/mcp.md`, `backends/langfuse.md`). Add a
   "Classification" section that defines M/C/R/O/P and requires every M and C row to name an owner.
2. **Ripple edits.** Each file below states its rules per class:
   `signals/traces.md`, `signals/app-logs.md`, `signals/access-log.md`, `signals/errors.md`,
   `signals/metrics.md` (new rpc/messaging/mcp/auth metrics; `auth_attempts_total` only when the code
   authenticates), `signals/profiling.md`, `hooks.md` (identity from auth middleware, baggage limits,
   reference-catalogue rows only when the code has the value), `config.md` (resource per platform),
   `sdk-notes.md` (per-SDK: keep sdk/runtime/os detectors, drop process.pid/command_line/vcs;
   enable ECS/Lambda/GCP/host/container detectors per platform), `audit.md` (detect the platform),
   `functions.md`, `workloads/*.md`, `safe-capture.md` (adds the T5a masking helper; it replaces "emails only in paths"),
   `collector/README.md` (pod association by namespace + pod name; drop `k8s.pod.uid`; add workload-name extraction).
3. **Enforcement.** The aim is that each M/C row can be checked.
   - `scripts/audit.py`: add static rules, e.g. identity read from raw headers, `request.id`, ID-like metric labels, and `code.namespace`.
   - `scripts/receiver` / e2e checks: assert the M rows per operation, P rows absent, metric label allow-list, and profile labels = resource.
   - `references/acceptance.md`: add the test cases: anonymous, authenticated, multi-tenant, service-to-service, job, AI/MCP, A2A, and concurrent-tenant leak.
   - A2A is new to the skill: add `workloads/a2a.md` (client and server instrumentation, custom `app.a2a.*`), the receiver checks, and an eval.
4. **Version bump.** Update CHANGELOG/MIGRATION per the ai-skills release checklist.

## Verification

- `python3 scripts/validate.py` and the existing `scripts/tests`, run with pytest.
- Re-run the e2e stack on two reference apps (one Python/FastAPI, one Node) and check every new assertion.
- Platform resource checks:
  - Kubernetes: run on the existing local stack.
  - Plain Docker and VM: run the same app under `docker run` and directly on the host.
  - ECS/Fargate, Lambda and Cloud Run: there is no live cloud, so check these by setting the detector env/metadata
    stubs (`ECS_CONTAINER_METADATA_URI_V4` mock, `AWS_LAMBDA_FUNCTION_NAME` and related variables, `K_SERVICE`/`K_REVISION`)
    and asserting the resource rows in the receiver. The report will say these were stub-checked, not run on real cloud.
- Run the skill-creator evals against the previous iteration's baseline, and compare the scores in the review viewer.
