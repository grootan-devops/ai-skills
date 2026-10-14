# Backends

Applications never depend on a backend. Every service exports OTLP over gRPC — batched, retried
and gzip-compressed — to a collector, or straight to a backend in direct mode; serves its ops
metrics in the Prometheus format on `/metrics`; and sends profiles over the Pyroscope protocol
(or OTLP profiles where the path supports them). Backend specifics are configuration only, in
`observability.yml` for direct mode ([config.md](../config.md)) or in the collector's exporters
([collector](../collector/README.md)):

| Setting | Direct mode (`observability.yml`) | Through the collector |
| --- | --- | --- |
| Endpoint | `<signal>.exporter.endpoint`: `scheme://host:port` for gRPC, the base URL for `http/protobuf` | each destination's exporter endpoint |
| Protocol | `<signal>.exporter.protocol`: `grpc` or `http/protobuf` | `otelcol.exporter.otlp` (gRPC) or `otelcol.exporter.otlphttp` |
| Headers and auth | `<signal>.exporter.headers`, values from `${file:…}` or `${env:…}` | `otelcol.auth.*` components, secrets from mounted files or env |
| TLS | `<signal>.exporter.insecure` and `<signal>.exporter.tls` (CA, client certificate and key files) | the exporter's `tls` block, mTLS |
| Temporality (OTLP metric push only) | `metrics.otlp.temporality`: cumulative, or `delta` for a delta-only backend ([signals/metrics.md](../signals/metrics.md)) | a cumulative-to-delta processor in that destination's branch |
| Profile format | the profiler's protocol and format (pprof, folded, JFR) | `pyroscope.write` per destination |
| Backend-specific attributes | none: the resource holds only the standard's attributes ([data-model.md](../data-model.md#resource)) | identity stamping in a dedicated pipeline, such as database-engine identity ([collector](../collector/README.md#database-engines)) |

## Backend table

| Backend | Auth | Protocol and temporality | Backend-specific attributes | Profiles | Details |
| --- | --- | --- | --- | --- | --- |
| OneUptime | header `x-oneuptime-token` | OTLP over HTTP at `<host>/otlp`, or gRPC; cumulative accepted | `oneuptime.label.<key>` resource attributes become project labels, which the standard doesn't add (a team adds them to its own yml if it wants them); database-engine telemetry carries `oneuptime.database.server.id`; queue dimensions are read from datapoints; its AI views read the GenAI keys, with `user.id` and `team.id`, on AI spans | Pyroscope protocol at `<host>/pyroscope`; pprof or folded, no JFR | [oneuptime.md](oneuptime.md) |
| Grafana stack: Tempo, Prometheus or Mimir, Loki, Pyroscope | self-hosted: none, basic auth or mTLS, plus `X-Scope-OrgID` when multi-tenant; Grafana Cloud: basic auth | OTLP per component; Prometheus scrape or remote write; logs through the collector | plain labels | Pyroscope protocol, JFR accepted | [grafana.md](grafana.md) |
| Dynatrace | `Authorization: Api-Token <token>` | **http/protobuf only; delta temporality only** | — | vendor agent (out of scope) | [notes](#dynatrace) |
| New Relic | header `api-key: <license key>` | OTLP over gRPC or HTTP at `otlp.nr-data.net` (US) or `otlp.eu01.nr-data.net` (EU); **delta** temporality | — | vendor agent (out of scope) | [notes](#new-relic) |
| AWS | SigV4, signed by the collector's auth extension | OTLP to X-Ray and CloudWatch through the collector | — | — | [notes](#aws) |
| Langfuse (AI traces, prompts, scores) | Basic auth from the project's public and secret keys, plus `x-langfuse-ingestion-version: 4` | OTLP over HTTP at `<base>/api/public/otel`, traces only; no gRPC | the `langfuse.*` trace-context and observation keys, on the Langfuse path only | — | [langfuse.md](langfuse.md) |

**Verify each backend's current ingest docs at run time.** Endpoints, accepted protocols, auth
styles, temporality, attribute limits and profile formats change; the rows above are examples
of what to look for, not values to copy.

## Choosing destinations

1. **Ask per signal.** Traces, metrics, logs and profiles may go to different backends, and one
   signal may fan out to several destinations in the collector.
2. **Collector by default.** Apps send OTLP over gRPC to the collector, which holds the backend
   credentials. Direct mode is for environments without a collector: the yml then carries the
   auth headers, and logs go over OTLP unless something else ships stdout.
3. **Secrets by reference.** Tokens enter only through `${file:…}` (a mounted secret) or
   `${env:…}` — in the yml and in the collector — never as literal values or in a ConfigMap.
4. **Never hardcode a row.** Read the backend's ingest docs, then write the values into the yml
   or the collector configuration.
5. **Check capabilities first:** accepted protocols, metric temporality, histogram types, auth
   style, attribute and payload limits, profile formats.
6. **Swap by configuration.** Moving a service to another backend changes only the yml and the
   collector configuration, never application code; the conformance checks prove it
   ([acceptance.md](../acceptance.md)).
7. **AI traces go to Langfuse as well,** never instead of the ops backend: the same traces, the
   AI part with content where the policy allows it, from the app or the collector
   ([signals/genai.md](../signals/genai.md)).

## Dynatrace

- The OTLP base URL is the environment's `/api/v2/otlp` endpoint; the exporter appends
  `/v1/traces`, `/v1/metrics` and `/v1/logs`.
- Auth: `Authorization: Api-Token <token>` with the ingest scope for each signal (platform tokens
  use `Bearer` instead).
- HTTP with binary protobuf only — no gRPC and no JSON. Apps keep sending gRPC to the collector;
  the collector's Dynatrace exporter is `otelcol.exporter.otlphttp`.
- Delta temporality only: cumulative sums and histograms are rejected. Add
  `otelcol.processor.cumulativetodelta` to the Dynatrace metrics branch of the collector; in
  direct mode, the SDK exporter's delta preference is set in code.
- Exponential histograms lose their buckets; use explicit-bucket histograms.
- Profiles come from Dynatrace's own agent, which is out of scope.

## New Relic

- OTLP endpoints: `otlp.nr-data.net` (US) and `otlp.eu01.nr-data.net` (EU), over gRPC or HTTP;
  OTLP over HTTP with protobuf travels the public internet more reliably than long-lived gRPC
  connections.
- Auth: header `api-key: <license key>`.
- Use delta temporality for counters and histograms (cumulative-to-delta in the collector).
- Attribute length, attribute count and payload size limits are strict: set the SDK's attribute
  limits and keep export batches below the payload limit.
- Profiles come from New Relic's own agent, which is out of scope.

## AWS

- Apps sign nothing. They keep sending OTLP over gRPC to the collector, which signs each request
  with SigV4 (`otelcol.auth.sigv4`) using the collector's IAM role.
- Traces go to the X-Ray OTLP endpoint, logs and metrics to the CloudWatch OTLP endpoints, per
  region; CloudWatch Logs also needs the target log group and stream.
- There is no profiles destination. These are notes only; there is no AWS page yet.

## Pages

- [OneUptime](oneuptime.md) — OTLP and profile ingestion, RUM and session replay, labels,
  exceptions, databases and queues.
- [Grafana stack](grafana.md) — Tempo, Prometheus or Mimir, Loki and Pyroscope, self-hosted or
  Grafana Cloud.
- [Langfuse](langfuse.md) — AI traces with content, prompts and scores, from the app or the
  collector.

[Documentation index](../README.md)
