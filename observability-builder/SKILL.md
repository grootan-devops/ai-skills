---
name: observability-builder
description: >-
  Add end-to-end observability to any service or frontend — access logs, structured
  application logs, OpenTelemetry traces with nested function spans, ops metrics on a
  Prometheus /metrics port, error logs, Pyroscope profiling, browser RUM and session replay,
  and LLM and agent telemetry (GenAI spans, token metrics, Langfuse) — driven by one
  observability.yml, for any language, framework or OTLP backend, and audit existing
  telemetry for unsafe capture. Use whenever someone asks to add, fix or standardise
  observability, OpenTelemetry, tracing, logging, metrics, profiling, RUM, APM or LLM
  observability in an application, to trace LLM calls, agents or RAG, to send
  telemetry to OneUptime, Grafana/Tempo, Dynatrace, New Relic or Langfuse, to replace vendor
  APM SDKs with OpenTelemetry, or to check telemetry for leaked secrets or prompts; not for
  Helm charts, Dockerfiles or CI pipelines (platform-builder), dashboards and alerts,
  Langfuse prompt, dataset or evaluator management, or installing a collector or backend.
---

# Observability Builder

This skill instruments applications against one observability standard and checks the result.
Everything it needs is in this folder:

| Folder | What it holds |
|---|---|
| `references/` | The standard (what every service emits and how it must behave) and the two workflows. [references/README.md](./references/README.md) is the index. |
| `assets/` | `observability.schema.json`, the JSON Schema for `observability.yml`; `java/PprofExporter.java`, a template that uploads the Java profiler's JFR as pprof ([sdk-notes](references/sdk-notes.md#java)) |
| `scripts/` | Stack detection, yml validation, the safety audit, API contract comparison, port holding, a local telemetry receiver (with a Langfuse stand-in) and a model-provider stub |

The skill ships no per-framework templates on purpose: frameworks and SDKs change faster than
any template, so research the project's language, framework and libraries at run time from
official docs and package registries, and make the *output* match the standard.

## Route the request

| Request | Action |
|---|---|
| `observability instrument [path]`: add or fix observability in a project | follow [the instrument steps](./references/instrument.md) |
| `observability audit [path]`: check existing telemetry and instrumentation | follow [the audit steps](./references/audit.md) |
| A question about the standard | answer from `references/`; change nothing |

## Read the standard

Read [references/README.md](./references/README.md) first, then only the pages the task needs.
Rows marked *planned* are not written yet: say so instead of inventing their content. Treat
versions and package names in the pages as examples, and look up current releases with the
project's package manager before adding anything.

## Working rules

- **The project's own rules come first.** Read its `AGENTS.md`, `CONTRIBUTING.md` and README
  before editing and follow them (env loading, logging helpers, error helpers, test commands).
  They encode lessons the standard can't know.
- **Stay in scope:** application code, `observability.yml` and the dependency manifest.
  Charts, Dockerfiles and CI belong to platform-builder — hand them over as an exact list
  ([references/charts.md](./references/charts.md)) instead of editing them.
- **Safe by construction.** Write instrumentation that never captures secrets, raw bodies in
  spans, IDs as metric labels, emails in URL paths, values in log messages or unapproved
  baggage ([references/safe-capture.md](./references/safe-capture.md)). There is no redactor to
  clean up afterwards; the audit will flag what slips through.
- **AI content is the riskiest telemetry.** Prompts, completions, tool arguments and
  retrieved documents go to Langfuse only, through the module's content policy: `full` only
  where the data is synthetic, `off` in production unless a feature's redactor is signed off;
  never in logs, metrics or the ops backend
  ([references/signals/genai.md](./references/signals/genai.md)). Never call a real model
  provider while verifying: point the client at `scripts/llm_stub.py` with a dummy key.
  Platform work in Langfuse (prompts, datasets, evaluators, score configs) follows Langfuse's
  own agent skill and live docs ([references/backends/langfuse.md](./references/backends/langfuse.md)).
- **Preserve contracts.** Keep error response bodies, routes and status codes. Ask before any
  breaking change (log or access-log format, metric names, removing a vendor SDK) and write a
  migration note.
- **Leverage, then enforce.** Use the framework's built-in observability when it exists, never
  add a second source for the same signal, and add what the built-in lacks.
- **Research before adding a dependency:** framework built-in → official OpenTelemetry
  instrumentation → framework-official plugin → maintained community library. Reject
  deprecated, squatted or wrong-publisher packages and record where each choice came from.
- **Secrets:** never print their values; they enter configuration only through `${file:…}` or
  `${env:…}` interpolation. Never read credential files (`.npmrc`, `.env`, token files), not
  even to install a private dependency for a test
  ([instrument step 7](./references/instrument.md#7-verify)). Browser code never holds a
  backend key: RUM and session replay go through the app's same-origin proxy
  ([references/signals/rum.md](./references/signals/rum.md)).
- **Your processes only.** When checking the app locally, stop exactly the processes you
  started, by PID, never by a name pattern. Point every exporter at the receiver or at an
  unused address before you break the config on purpose, so a failure test never reaches a real
  collector or a port another program uses.
- **No commit, push, deploy or cluster change** unless the user explicitly asks.

## Validate and report

Scripts live in this skill's `scripts/` folder and need Python 3.10+ with PyYAML; the receiver
also needs grpcio, protobuf and opentelemetry-proto, which `uv run --with` provides.

```bash
python3 scripts/detect.py <project>                       # stack and existing telemetry, as JSON
python3 scripts/validate.py <project>/observability.yml   # schema check (bundled schema) and interpolation dry run
python3 scripts/audit.py <project> --module <observability-module-path>
python3 scripts/contract.py record|compare --base <url> --requests <file> ...   # API responses before vs after
python3 scripts/hold_port.py <port>                        # take a port on every address
uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml \
  scripts/receive.py --out <scratch>/telemetry [--canaries <file>]   # real OTLP + Pyroscope + session-replay + Langfuse receiver
python3 scripts/llm_stub.py                                # OpenAI- and Anthropic-compatible model stub for AI features
```

Also run the project's own build and tests before and after the change where they can run,
compared suite by suite, and run the app against the receiver: only a real connection shows
whether exports arrive (instrument step 7). Report: the skill version (the version line in
[README.md](./README.md)), files changed, dependencies with versions and sources, the answers
used, handoffs for platform-builder, the migration note, functions skipped by the wrappers and
open observability risks (never security or code issues outside telemetry: other review layers
own those), plus the telemetry inventory in
[instrument step 8](./references/instrument.md#8-report): metrics and labels, logging that
bypasses the logger, work outside the API, the resource and span attributes, and, for AI
features, the features, content modes, Langfuse route and handoff.
