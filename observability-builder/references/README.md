# The observability standard

What every service, frontend, gateway, MCP server or AI agent emits, how `observability.yml` is
shaped, where observability code lives in a project, how telemetry stays safe, and how
conformance is proven. The standard speaks OTLP, the Prometheus exposition format, the
Pyroscope protocol and OpenTelemetry semantic conventions and is backend-neutral; RUM and
session replay currently target OneUptime, and LLM observability targets Langfuse.

Read this index first, then only the rows the task needs. Rows marked *planned* are not written
yet: say so instead of inferring their content.

| Task | Read | Status |
|---|---|---|
| Understand the principles | [principles.md](principles.md) | ready |
| Know where each field belongs (resource, span, log, metric, native OTLP) | [data-model.md](data-model.md) | ready |
| Add business attributes per function | [enrichment.md](enrichment.md) | ready |
| Wrap functions with spans, profiling labels and step logs | [functions.md](functions.md) | ready |
| Capture telemetry safely and audit it | [safe-capture.md](safe-capture.md) | ready |
| Write or validate `observability.yml` | [config.md](config.md), [the JSON Schema](../assets/observability.schema.json) | ready |
| Lay out the observability module in a project | [layout.md](layout.md) | ready |
| Hook into a framework's middleware | [hooks.md](hooks.md) | ready |
| Check known SDK, runtime and framework behaviour before relying on it | [sdk-notes.md](sdk-notes.md) | ready |
| Instrument one signal: [access log](signals/access-log.md), [application logs](signals/app-logs.md), [traces](signals/traces.md), [metrics](signals/metrics.md), [errors](signals/errors.md), [profiling](signals/profiling.md) | `signals/` | ready |
| Instrument browser RUM and session replay (OneUptime) | [signals/rum.md](signals/rum.md) | ready |
| Instrument a workload type: [API service, WebSocket included](workloads/api.md), [scheduled job, batch job, cron job, CLI](workloads/batch.md) | `workloads/` | ready |
| Instrument a fullstack app or a single-page app | [workloads/fullstack.md](workloads/fullstack.md), [workloads/spa.md](workloads/spa.md) | ready |
| Instrument LLM calls, agents, tools, RAG and other AI features; prompts, completions and token usage; Langfuse | [signals/genai.md](signals/genai.md), [workloads/ai-agent.md](workloads/ai-agent.md), [backends/langfuse.md](backends/langfuse.md) | ready |
| Instrument a worker, gateway or MCP server | `workloads/` | planned |
| Point telemetry at a backend: [overview](backends/README.md), [OneUptime](backends/oneuptime.md), [Grafana stack](backends/grafana.md), [Langfuse](backends/langfuse.md) (AI traces) | `backends/` | ready |
| Run the collector | [collector/README.md](collector/README.md) | ready |
| Import ready-made Grafana Alloy modules for the collector | — | planned |
| Wire a Helm chart | [charts.md](charts.md) | ready |
| Prove conformance | [acceptance.md](acceptance.md) | ready |
| Check an app's real exports with a local OTLP, Pyroscope, session-replay and Langfuse receiver, and a model-provider stub | [acceptance.md](acceptance.md#local-receiver), [`scripts/receive.py`](../scripts/receive.py), [`scripts/llm_stub.py`](../scripts/llm_stub.py) | ready |

The workflow pages that use the standard: [instrument.md](instrument.md) and [audit.md](audit.md).
