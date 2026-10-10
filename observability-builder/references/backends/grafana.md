# Grafana stack

Tempo for traces, Prometheus or Mimir for metrics, Loki for logs and Pyroscope for profiles,
self-hosted or on Grafana Cloud. Applications don't change: they keep sending OTLP over gRPC to
the collector, serving their ops metrics on `/metrics` and pushing profiles over the Pyroscope
protocol, and the collector or Prometheus delivers each signal to its component
([collector](../collector/README.md)). Check each component's current ingest docs at run time.

## Components

| Signal | Component | Ingest | Collector exporter |
|---|---|---|---|
| Traces | Tempo | OTLP over gRPC (4317) or HTTP (4318) | `otelcol.exporter.otlp` or `otelcol.exporter.otlphttp` |
| Metrics | Prometheus | its own scrape of `/metrics`; OTLP over HTTP at `/api/v1/otlp/v1/metrics` once its OTLP receiver is switched on; or remote write | `otelcol.exporter.otlphttp`, or `otelcol.exporter.prometheus` → `prometheus.remote_write` |
| Metrics | Mimir | OTLP over HTTP at `/otlp/v1/metrics`, or remote write | as for Prometheus |
| Logs | Loki | OTLP over HTTP at `/otlp`, through the collector | `otelcol.exporter.otlphttp` |
| Profiles | Pyroscope | Pyroscope protocol, JFR included | `pyroscope.write` |

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

## Authentication

| Installation | Auth | Collector setting |
|---|---|---|
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
