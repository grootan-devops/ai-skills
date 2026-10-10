# Enrichment

Instrumentation libraries record *how* an operation ran. Enrichment adds *who* and *what* it was
about (the user, the tenant, the order, the outcome), chosen by what each function does. This page
says where those attributes go, how they are named, where their values come from, and which
attributes each kind of function gets.

## Placement

| What | Where | Examples |
|---|---|---|
| Per-process identity | **resource**: the standard's eight attributes, plus `k8s.cluster.name` where several clusters send to one backend ([data model](data-model.md#resource)); nothing else is added there | service name, namespace, version and environment; the pod's namespace, name, UID and node; the cluster's name |
| Per-request context | attributes on **each service's entry span** and its access log only; an allow-listed subset travels as **baggage**, so the next service can set it on its own entry span | user, tenant, session, client app and version, locale, experiment variant |
| Per-operation facts | attributes on **that operation's span** | order ID, cart size, payment method |
| AI feature context | stamped by the module on **every span of an AI feature**, for Langfuse only; stripped on the ops path, never baggage ([genai](signals/genai.md#trace-context)) | feature, user, conversation, release, tags |
| Notable moments | **event log records** with `eventName`, correlated to the span | retry, payment declined, cache miss |
| Aggregates | **metrics** with short, bounded labels only, never IDs; the ops metrics by default, business metrics when the team adds them | plan, payment method, outcome |

Request context stays off child spans because backends join them to the entry span by trace ID
([resource vs request attributes](data-model.md#resource-vs-request-attributes)). Notable moments
are log records rather than span events because OpenTelemetry is deprecating span events; the
record keeps its trace and span IDs, so it still shows up with its span.

## Naming and safety

- **Semantic conventions first:** `user.id`, `user.roles`, `session.id`, `feature_flag.*`,
  `file.*`, `geo.*`, `client.*`, and the operation groups in the
  [data model](data-model.md#operations).
- **One namespace for custom attributes:** `app.` by default, or a reverse domain such as
  `com.example.`, chosen once per project. The namespace keeps custom names from colliding with
  future semantic conventions.
- **Standard-defined names:** `tenant.id` and `correlation.id` belong to this standard and are used
  without a namespace in every service, so the same key works across services, browsers and logs.
- **Values as they are:** IDs and other values are recorded unchanged and respect the SDK's
  attribute length limit. Secrets are never captured ([safe capture](safe-capture.md)).
- **IDs stay out of aggregates:** IDs never become metric labels or profile labels, and they
  are not in the default baggage allow-list. The baggage allow-list is defined in code.
- **Metric names** are Prometheus names in the project's namespace (`app_` by default) with
  short label names, made with the stack's Prometheus client library like the ops metrics
  ([metrics](signals/metrics.md)). In the catalogue, `name{a,b}` lists a metric's bounded labels.

## Sources

All enrichment is code; nothing in `observability.yml` adds span attributes.

- **Request context.** The middleware sets it on the entry span after authentication, from the auth
  layer's verified principal and claims, headers and route parameters. The observability middleware
  runs outermost, before authentication, so it reads these at response end. It never parses an
  unverified token. `client.address` comes from the framework's trusted-proxy resolver, never from
  the raw `X-Forwarded-For` header, which any client can forge ([hooks](hooks.md)).
- **Function-level attributes.** Set inside the function or its wrapper with the OpenTelemetry
  API, for example `@WithSpan` with `@SpanAttribute` in Java, a decorator in Python or TypeScript,
  or a helper in Go ([functions](functions.md)).
- **Choosing attributes.** Read the main handlers and services, propose attributes per function
  from the catalogue below, and confirm them with the service owner before adding them. A function
  that matches no row gets attributes chosen the same way: semantic-convention names first, custom
  names in the namespace, bounded values for metrics. Business metrics are proposed the same
  way, but added only when the team asks for them: metrics are ops-only by default.

## Catalogue

Span attributes go on the function's own span; events are log records with `eventName`; companion
metrics, added only when the team asks for them, list their bounded labels in braces.

| Function | Span attributes | Events (log records) | Companion metric, on request (bounded labels) |
|---|---|---|---|
| Login or authentication | `user.id`, `user.roles`, `app.auth.method` (password, oauth, sso), `app.auth.result`, `app.auth.mfa` | `auth.failed` (reason) | `app_auth_attempts_total{method,result}` |
| Checkout or order | `app.order.id`, `app.cart.items_count`, `app.order.total`, `app.currency`, `app.payment.method`, `app.coupon.applied` | `payment.declined` (provider code) | `app_orders_placed_total{payment_method}`, an order value histogram |
| Payment provider call | `app.payment.provider`, `app.payment.status`, `app.idempotency_key`, `app.retry.attempt` | `retry.scheduled` | `app_payment_duration_seconds{provider,status}` |
| Search | `app.search.query_length`, `app.search.filters_count`, `app.search.results_count`, `app.search.page` | `search.zero_results` | a results-count histogram |
| File upload | `file.size`, `file.extension`, `app.upload.content_type`, `app.upload.storage`, `app.scan.result` | `upload.rejected` | `app_upload_bytes_total{storage}` |
| Multi-tenant request | `tenant.id`, `app.tenant.plan`, `app.tenant.region` | — | `app_requests_total{plan}` (never `tenant.id`) |
| Feature flag evaluation | `feature_flag.key`, `feature_flag.result.variant`, `feature_flag.provider.name` | `feature_flag.evaluation` | — |
| A/B experiment | `app.experiment.id`, `app.experiment.variant` | `experiment.exposed` | conversions by variant |
| Background job or consumer | `app.job.name`, `app.job.trigger`, `app.job.items.processed`, `app.job.items.failed`, plus `messaging.*` for a consumer | `job.retry_scheduled`, `job.dead_lettered` | the ops metrics `job_runs_total{job_name,outcome}` and `job_duration_seconds{job_name}` ([batch](workloads/batch.md)) |
| Cache lookup | `app.cache.name`, `app.cache.hit`, `app.cache.key_prefix` (never the full key) | — | `app_cache_requests_total{name,hit}` |
| BFF or downstream call | `app.downstream.name`, `app.circuit_breaker.state`, `app.rate_limit.remaining`, `app.retry.attempt` | `circuit_breaker.opened` | the ops client metrics by `service`; `app_downstream_errors_total{name}` |
| Notification | `app.notification.channel` (email, sms, push), `app.notification.template`, `app.notification.provider` | `notification.bounced` | `app_notifications_total{channel,outcome}` |
| Client-facing API | `app.client.name`, `app.client.version`, `app.client.platform`, `app.locale`, `app.api.version` | — | `app_requests_total{platform}` |
| Workflow or saga | `app.workflow.id`, `app.workflow.step`, `app.saga.compensating` | `step.compensated` | a step duration histogram `{workflow,step}` |
| GenAI or RAG step ([genai](signals/genai.md#step-kinds)) | `gen_ai.conversation.id`, `gen_ai.prompt.name`, `app.genai.prompt.version`, `app.rag.documents_retrieved`, `app.rag.top_score`, `app.genai.guardrail.result`; the module stamps the feature context | `guardrail.blocked`, `rag.no_documents`, `genai.feedback.received` | the GenAI metrics are part of the default set for a service that calls a model; on request `app_genai_feedback_total{feature,value}` |
| IoT or device command | `device.id`, `app.device.firmware`, `app.command.name`, `app.command.result` | `device.offline` | `app_commands_total{name,result}` |

[Documentation index](README.md)
