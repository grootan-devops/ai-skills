# Grafana stack

Tempo for traces, Prometheus or Mimir for metrics, Loki for logs and Pyroscope for profiles,
self-hosted or on Grafana Cloud. Applications don't change: they keep sending OTLP over gRPC to
the collector, serving their ops metrics on `/metrics` and pushing profiles over the Pyroscope
protocol, and the collector or Prometheus delivers each signal to its component
([collector](../collector/README.md)). Check each component's current ingest docs at run time.

## Components

| Signal | Component | Ingest | Collector exporter |
| --- | --- | --- | --- |
| Traces | Tempo | OTLP over gRPC (4317) or HTTP (4318) | `otelcol.exporter.otlp` or `otelcol.exporter.otlphttp` |
| Metrics | Prometheus | its own scrape of `/metrics`; OTLP over HTTP at `/api/v1/otlp/v1/metrics` once its OTLP receiver is switched on; or remote write | `otelcol.exporter.otlphttp`, or `otelcol.exporter.prometheus` → `prometheus.remote_write` |
| Metrics | Mimir | OTLP over HTTP at `/otlp/v1/metrics`, or remote write | as for Prometheus |
| Logs | Loki | OTLP over HTTP at `/otlp`, through the collector | `otelcol.exporter.otlphttp` |
| Profiles | Pyroscope | Pyroscope protocol, JFR included | `pyroscope.write` |
| Browser RUM | Loki (records) and Tempo (spans) | Faro batches to Alloy's `faro.receiver` | `loki.write`, `otelcol.exporter.otlp` |

## Traces

- Tempo takes OTLP as it is; the collector's OTLP pipeline (identity, batching) runs first.
- Multi-tenant installations need the tenant header `X-Scope-OrgID` on every request; the same
  holds for Mimir, Loki and Pyroscope.
- Links between traces, logs, metrics and profiles rely on shared identity and native IDs: the
  same resource on every signal, native trace and span IDs on logs, and span-profile labels on
  profiles where the runtime has them. Metrics join the others by service, route and time.

## Metrics

- **One scraper per target.** Either Prometheus scrapes the app's `/metrics` through its
  ServiceMonitor or PodMonitor, or the collector scrapes and forwards — never both, or every
  series is duplicated.
- **The ops metrics keep their names:** they are Prometheus names already
  ([signals/metrics.md](../signals/metrics.md)), and queries and dashboards use them as they
  are. Scraped directly, they get the target's labels (namespace, pod) from the monitor.
- **OTLP-pushed app metrics** (off by default): Prometheus' OTLP receiver expects cumulative
  data, so delta from push clients is converted in the collector
  (`otelcol.processor.deltatocumulative`). Prometheus translates OpenTelemetry names (dots to
  underscores, unit and `_total` suffixes) unless UTF-8 names are enabled. Their resource
  attributes land on the `target_info` series: promote the bounded identity ones
  (`service.name`, `service.namespace`, `deployment.environment.name`, `k8s.namespace.name`) to
  labels, never IDs.
- **Out-of-order samples** arrive when several collector replicas push the same series: allow an
  out-of-order window on the receiving side.

## Logs

- Logs go through the collector: pod stdout is tailed, parsed and mapped to native fields, then
  exported over OTLP to Loki's `/otlp` endpoint, which takes HTTP only. Apps whose
  `logging.output` includes `otlp` go through the same pipeline, and their stdout isn't shipped
  as well.
- Loki turns a fixed set of resource attributes into index labels (service, namespace,
  Kubernetes workload, environment, cluster) and keeps the rest — log attributes, native trace
  and span IDs, severity — as structured metadata. Index labels stay bounded: never promote an
  ID.

## Profiles

- Pyroscope takes pprof, folded text and JFR, so the Java agent's profiles need no special route.
- The application name is `service.name`; static labels use Pyroscope syntax (dots become
  underscores: `service_version`, `deployment_environment_name`); span-profile integrations add the
  span labels that link Tempo spans to profiles ([signals/profiling.md](../signals/profiling.md)).
- Through the collector: `pyroscope.receive_http` and `pyroscope.ebpf` forward to
  `pyroscope.write`.

## Browser RUM

Faro records arrive in Loki as logfmt lines labelled `app_name`, `app_environment` and `kind`
(`exception`, `log`, `event`, `measurement`), and browser spans arrive in Tempo
([signals/rum.md](../signals/rum.md)). Grafana OSS has no frontend app, so the skill ships its own
dashboard, [`assets/grafana/rum-dashboard.json`](../../assets/grafana/rum-dashboard.json), built
from the fields Faro really sends (not a copy of a public dashboard). It has 68 panels in eight
rows, and every one of its 97 queries was checked to return data from a live Faro run (Faro
2.13, Alloy 1.21, Loki 3.7, Tempo 3.1, Grafana 13.2):

| Row | What it shows |
| --- | --- |
| Overview | sessions, page loads, views, exceptions, exceptions per 100 page loads, signed-in users, activity over time, records by kind |
| Core Web Vitals | LCP, INP, CLS, FCP and TTFB p75 with good and poor thresholds, trends, ratings per vital, vitals per view |
| Web vital attribution | LCP phases (TTFB, resource load delay and duration, render delay), LCP elements and resources, INP phases and long-animation-frame script, style and layout time, slowest interactions and their scripts, TTFB phases, FCP after first byte, layout-shift sources |
| Page loads and resources | page-load and network timings, resources by type, size and cache, slowest and render-blocking resources, failed resources |
| Errors and console | exceptions by type, browser and release, top messages, console logs by level, the de-minified stacks |
| User journeys and frustration | user actions and their duration, views and transitions, soft-navigation time, rage, dead and error clicks by view and element, sessions by browser and device, session lifecycle, releases |
| API calls from the browser | fetch duration per API host, calls by status, failures, the slowest calls with their trace ids, the browser → API traces from Tempo |
| CSP violations and custom signals | violations by directive and blocked source, the app's own events and measurements |

Filters: app, environment, view (regex on the route template) and browser (regex). Import it
with the Loki and Tempo data sources selected; nothing in it names an app or an environment.

| View | Query |
| --- | --- |
| LCP p75 per environment | `quantile_over_time(0.75, {app_name="shop", kind="measurement"} \| logfmt \| type="web-vitals" \| lcp!="" \| unwrap lcp [5m]) by (app_environment)` (also `inp`, `cls`, `fcp`, `ttfb`) |
| Vital ratings | `sum by (context_rating) (count_over_time({app_name="shop", kind="measurement"} \| logfmt \| type="web-vitals" \| inp!="" [5m]))` |
| Exceptions | `sum by (app_environment) (count_over_time({app_name="shop", kind="exception"} [5m]))`, and the lines for the de-minified stacks |
| Frustration | `sum by (event_name) (count_over_time({app_name="shop", kind="event"} \| logfmt \| event_name=~"faro.frustration.*" [5m]))` |
| Slow views | `quantile_over_time(0.75, … \| unwrap lcp [5m]) by (view_name)` — `view_name` is the route template, so it is bounded |
| One session | `{app_name="shop"} \| logfmt \| session_id="<id>"` |
| Browser → API traces | TraceQL `{resource.service.name="shop" && kind=client} >> {kind=server}`; or the `traceID` on a `faro.tracing.fetch` line |

- **Recording rules.** For dashboards and alerts over long ranges, turn the vital and error
  queries into Loki recording rules that write to Prometheus or Mimir. The labels are the
  `by (...)` labels above, never `page_url`, `session_id` or `user_id`.
- **Transport `otlp`.** Records arrive as OTLP logs: the labels come from the resource
  (`service_name`, `deployment_environment_name`), and fields such as `event_name`,
  `measurement_name` and `measurement_type` are structured metadata. Use them as label filters
  instead of `| logfmt`.
- **Retention.** RUM is high-volume. Give the RUM tenant or streams their own retention and
  query limits.

## AI telemetry

Grafana is an ops backend, so it gets the Ops column of the AI rows
([data-model.md](../data-model.md#ai)):

- **Tempo** gets the AI spans after the ops guard: model calls, agents, tools, retrieval, MCP and
  A2A calls with their operational attributes and `gen_ai.conversation.id`. Never content,
  `gen_ai.usage.cost` or `langfuse.*` keys ([genai](../signals/genai.md#the-ops-allow-list)).
- **Prometheus or Mimir** gets the AI metrics by their Prometheus names:
  `gen_ai_client_*`, `mcp_server_*` and `mcp_client_*`, `a2a_server_*` and `a2a_client_*`
  ([data-model.md](../data-model.md#ai-metrics-logs-and-profiling)). Cost is never a metric.
- **Loki** gets AI log events with ids and bounded values only.
- **Pyroscope** gets a `controller` label only for heavy AI work in the process, such as prompt
  assembly or tokenisation; model, MCP and A2A calls get none.

Content, cost and the trace context per user live in Langfuse
([langfuse.md](langfuse.md)).

## Authentication

| Installation | Auth | Collector setting |
| --- | --- | --- |
| Self-hosted, in-cluster | none, or mTLS | the exporter's `tls` block |
| Self-hosted, behind a gateway | basic auth or a bearer token | `otelcol.auth.basic` or `otelcol.auth.bearer`; `basic_auth` or `bearer_token_file` in `pyroscope.write` |
| Multi-tenant | the `X-Scope-OrgID` header, with any of the above | `otelcol.auth.headers`; `headers` in `pyroscope.write` |
| Grafana Cloud | basic auth: the instance or user ID shown for the endpoint as username, an access-policy token as password | `otelcol.auth.basic` against the OTLP gateway (OTLP over HTTP) or each component's endpoint; `basic_auth` in `pyroscope.write` |

A Grafana Cloud destination, with the token read from a mounted file:

```alloy
local.file "grafana_cloud_token" {
  filename  = "/var/run/secrets/observability/grafana-cloud-token"
  is_secret = true
}

otelcol.auth.basic "grafana_cloud" {
  username = "<instance-id>"
  password = local.file.grafana_cloud_token.content
}

otelcol.exporter.otlphttp "grafana_cloud" {
  client {
    endpoint = "https://otlp-gateway-<region>.grafana.net/otlp"
    auth     = otelcol.auth.basic.grafana_cloud.handler
  }
}

pyroscope.write "grafana_cloud" {
  endpoint {
    url = "https://<pyroscope-endpoint>"
    basic_auth {
      username      = "<pyroscope-user-id>"
      password_file = "/var/run/secrets/observability/grafana-cloud-token"
    }
  }
}
```

[Documentation index](../README.md)
