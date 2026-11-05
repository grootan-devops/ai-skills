# The observability standard

What every service, gateway, MCP server, A2A agent, AI agent or web page emits, how `observability.yml` is
shaped, where observability code lives in a project, how telemetry stays safe, and how
conformance is proven. The standard speaks OTLP, the Prometheus exposition format, the
Pyroscope protocol and OpenTelemetry semantic conventions and is backend-neutral; LLM
observability targets Langfuse, and browser RUM uses Grafana Faro with Alloy, Loki and Tempo.

Read this index first, then only the rows the task needs. Rows marked *planned* are not written
yet: say so instead of inferring their content.

| Task | Read | Status |
| --- | --- | --- |
| Understand the principles | [principles.md](principles.md) | ready |
| Know where each field belongs (resource, span, log, metric, native OTLP), its class (M, C, R, O, P) and its owner; resource rows per platform (Kubernetes, ECS, Lambda, Cloud Run, VM, Docker); identity context and PII masking | [data-model.md](data-model.md) | ready |
| Add business attributes per function | [enrichment.md](enrichment.md) | ready |
| Wrap functions with spans, profiling labels and step logs | [functions.md](functions.md) | ready |
| Capture telemetry safely and audit it | [safe-capture.md](safe-capture.md) | ready |
| Keep instrumentation within its performance budget and secure: overhead test, export, limits, transport, credentials, exposed ports, supply chain, packaging | [overhead-and-security.md](overhead-and-security.md) | ready |
| Write or validate `observability.yml` | [config.md](config.md), [the JSON Schema](../assets/observability.schema.json) | ready |
| Lay out the observability module in a project | [layout.md](layout.md) | ready |
| Hook into a framework's middleware | [hooks.md](hooks.md) | ready |
| Check known SDK, runtime and framework behaviour before relying on it, and which resource detectors to keep per SDK | [sdk-notes.md](sdk-notes.md) | ready |
| Instrument one signal: [access log](signals/access-log.md), [application logs](signals/app-logs.md), [traces](signals/traces.md), [metrics](signals/metrics.md), [errors](signals/errors.md), [profiling](signals/profiling.md) | `signals/` | ready |
| Instrument a workload type: [API service, WebSocket included](workloads/api.md), [scheduled job, batch job, cron job, CLI](workloads/batch.md) | `workloads/` | ready |
| Instrument the server side and the pages of a fullstack app | [workloads/fullstack.md](workloads/fullstack.md) | ready |
| Add browser RUM with Grafana Faro (errors, console, web vitals, performance, sessions, views, navigation, user actions, frustration, CSP, custom signals, browser-to-API traces): the features, the three transports (direct to `faro.receiver`, a same-origin proxy, OTLP), the scrubber and the budget | [signals/rum.md](signals/rum.md), [workloads/spa.md](workloads/spa.md), [the Faro templates](../assets/js/faro/README.md) | ready |
| Instrument LLM calls, agents, tools, RAG and other AI features; prompts, completions, token usage, cost and who spent it, in Langfuse | [signals/genai.md](signals/genai.md), [workloads/ai-agent.md](workloads/ai-agent.md), [backends/langfuse.md](backends/langfuse.md) | ready |
| Instrument an MCP server or client: spans, `_meta` context, errors, metrics | [workloads/mcp.md](workloads/mcp.md) | ready |
| Instrument an A2A (agent-to-agent) agent or client: `invoke_agent` spans, `traceparent`, task state, metrics | [workloads/a2a.md](workloads/a2a.md) | ready |
| Instrument a worker or gateway | `workloads/` | planned |
| Point telemetry at a backend: [overview](backends/README.md), [Grafana stack](backends/grafana.md) (RUM queries and a full Faro dashboard included), [Langfuse](backends/langfuse.md) (AI traces) | `backends/` | ready |
| Run the collector, `faro.receiver` for browser RUM included | [collector/README.md](collector/README.md) | ready |
| Import ready-made Grafana Alloy modules for the collector | — | planned |
| Wire a Helm chart | [charts.md](charts.md) | ready |
| Prove conformance | [acceptance.md](acceptance.md), overhead tests with `scripts/overhead.py` (services) and `scripts/rum_overhead.mjs` (pages) | ready |
| Check an app's real exports with a local OTLP, Pyroscope, Faro and Langfuse receiver, and a model-provider stub | [acceptance.md](acceptance.md#local-receiver), [`scripts/receive.py`](../scripts/receive.py), [`scripts/llm_stub.py`](../scripts/llm_stub.py) | ready |

The workflow pages that use the standard: [instrument.md](instrument.md) and [audit.md](audit.md).
