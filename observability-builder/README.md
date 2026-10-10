# Observability Builder

Version: 1.5.0

An AI agent skill that instruments any service or frontend for end-to-end observability —
access logs, structured logs, OpenTelemetry traces with nested function spans, metrics,
error logs, profiling, RUM and session replay, and LLM and agent telemetry with Langfuse — and
audits telemetry for unsafe capture.
It is self-contained: the workflow, the observability standard it applies, the
`observability.yml` schema and the checking scripts are all in this folder.

## Commands

| Command | What it does |
|---|---|
| `observability instrument [path]` | Detects the stack, asks the deployment questions, researches current libraries, plans, implements and verifies against a real receiver |
| `observability audit [path]` | Finds secrets, bodies, unbounded labels and misplaced setup in instrumentation; changes nothing unless asked |

Copyable prompts are in [PROMPT.md](./PROMPT.md).

## Install

Claude Code loads skills from `~/.claude/skills/` (every project) or `<project>/.claude/skills/`
(one project). Link or copy this folder there, then call it as `/observability-builder …` or ask
in plain words. Other agents start from [AGENTS.md](./AGENTS.md).

```bash
ln -s "$(pwd)/observability-builder" ~/.claude/skills/observability-builder
```

## Layout

| Path | Purpose |
|---|---|
| `SKILL.md` | Entry point: routing, working rules, commands |
| `references/README.md` | Index of the standard: principles, data model, config, layout, hooks, signals (GenAI included), workloads (AI features and agents included), backends (Langfuse included), collector, charts, acceptance, SDK notes |
| `references/instrument.md`, `references/audit.md` | The two workflows |
| `assets/observability.schema.json` | JSON Schema for `observability.yml` |
| `assets/java/PprofExporter.java` | Template: the Java profiler's JFR snapshots as pprof, for backends without JFR |
| `assets/go/langfuse/`, `assets/java/langfuse/` | Templates: the Langfuse exporter (export scope, content guard) and REST client (scores, prompts, datasets) for stacks without a Langfuse SDK |
| `assets/js/langfuse/` | Templates for Node and TypeScript: the export scope, the content guard, the guarded processor proxy, env-free OTLP transports and the score client, around Langfuse's JS SDK |
| `scripts/` | The tools below |

## Scripts

Python 3.10+ with PyYAML; `jsonschema` is used when installed.

| Script | Purpose |
|---|---|
| `scripts/detect.py <project>` | Stack, entrypoints, workload type, existing telemetry, and AI use (model providers, AI frameworks, vector stores, LLM-observability tools) as JSON |
| `scripts/validate.py <observability.yml>` | Schema check against the bundled schema and an interpolation dry run; never prints secret values |
| `scripts/audit.py <project> --module <path>` | Static findings with severities: safe capture, layout, logging that bypasses the app's logger, schedulers without a job span, patched SDK internals, AI content outside the content policy, capture switches, Langfuse keys; `--strict` exits 1 on any finding, a P0 always exits 2 |
| `scripts/contract.py record\|compare` | Records API responses before a change and compares them after; never prints header values |
| `scripts/hold_port.py <port>` | Holds a port on every IPv4 and IPv6 address, to test the "port taken" failure mode |
| `scripts/receive.py --out <dir>` | Local OTLP/gRPC, OTLP/HTTP, Pyroscope, OneUptime session-replay and Langfuse receiver; writes decoded payloads and a `summary.md` (with content keys per destination, never values). Run with `uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml` |
| `scripts/llm_stub.py` | Deterministic OpenAI-compatible (chat with tools, streaming with usage, embeddings) and Anthropic Messages stub with failure, rate-limit and slow triggers, so AI features run without a provider key; standard library only |

## Changes

- 2026-10-09, Langfuse for TypeScript, Go and Java, in every mode they support.
  - **Assets:**
    - `assets/js/langfuse`: the export scope, the content guard, the guarded processor proxy,
      env-free OTLP transports and the score client, around Langfuse's JS SDK.
    - `assets/go/langfuse` and `assets/java/langfuse`: the exporter and a REST client for scores,
      prompts and datasets, where there is no Langfuse SDK.
  - **Verification:**
    - Reference apps passed every harness check: TypeScript in `hybrid`, `sdk` and `otel`, Go in
      `hybrid` and `otel`. Go and Java have no `sdk` mode; the loader logs one ERROR and keeps
      Langfuse off.
    - The Go reference and both REST assets were checked against a self-hosted Langfuse 4.55.
  - **What the references taught the skill:**
    - **No setting of the ops exporter reaches Langfuse.** Python's and JS's OTLP exporters merge
      the header variables into their own and take the TLS files and compression. Go's took the
      ops protocol. Langfuse's SDK processors build such an exporter by default. So the Langfuse
      path gets a transport that reads no variables, and the loader passes headers as options.
    - **Span attribute limit:** at least `genai.content.max_chars`, because SDKs cut values blindly.
    - **Baggage:** the allow-listed propagator works on extract and inject, because Langfuse's JS
      tracing writes `langfuse_trace_id`.
    - **Feature declaration** has a form per language.
    - **TypeScript `sdk` mode** taps the client beneath `observeOpenAI`, whose spans need renaming,
      retyping, usage and ending.
    - **Ops guard** drops AI scopes' attributes, where Langfuse's Python SDK puts its public key.
    - **Smaller fixes:**
      - The content-key list matches across the docs, the collector and the code.
      - The Java client needs HTTP/1.1.
      - One-shot results key their scores on the trace id.
      - The app's own Langfuse features stay out of the risk list.
      - The audit ignores package names in comments.
  - **Verification step:** it now repeats the `full` run in each mode a service switches between.
  - **After the eval runs (iteration 7):** with the skill, every graded run passed every check in
    each mode tried; the old skill scored 25-29 of 35. Fixes from the runs:
    - the Go asset clones span attributes before building a set (a data race with the ops
      exporter);
    - Go and Java get an uncached prompt fetch and a translation hook on the exporter;
    - sdk-notes gains the pyroscope-go flush before stop.
  - **`PROMPT.md`** now asks for the mode and an observability-only risk list.
- 2026-10-09, Langfuse modes and a telemetry-only scope: `genai.langfuse.mode` (`hybrid`, `sdk`,
  `otel`) replaces `genai.vocabulary`, with app code the same in all three so one key (or a
  variable) switches them: `hybrid` keeps the GenAI instrumentation with the Langfuse SDK as
  exporter and score client, `sdk` lets the Langfuse SDK make the AI observations (its OpenAI
  integration, SDK observations for the steps), and `otel` loads no Langfuse SDK (the module's own
  OTLP exporter or the collector) and sends no scores. Prompt management, datasets and evaluators
  are the app's own Langfuse features: `genai.langfuse.prompts` is gone, app code fetches prompts
  through the module's client (the SDK keeps one client per public key and ignores later ones) and
  hands the model-call helper the prompt it used, which the generation links. The content guard
  now caps and redacts any SDK's own capture, the module's logging keeps only the type of a logged
  exception, and the harness grades each mode (`--genai-mode`); the reference app passes all
  checks in every mode, and the three modes were verified against Langfuse 4.55.
- 2026-10-09, LLM observability with Langfuse: a GenAI signal page, an AI-features workload page
  and a Langfuse backend page; one tracer provider feeding the ops backend and Langfuse, the AI
  feature context stamped on every AI span (never baggage), content only through a content
  policy (`full` where data is synthetic, `off` in production, `redacted` per feature) and
  never on the ops path, the GenAI metrics, approval and feedback designs, and both routes (a
  Langfuse processor in the app, or Alloy branches with an optional tail-sampling gateway). New
  `genai` section in the schema with consistency checks in `validate.py`, GenAI detection in
  `detect.py`, ten GenAI audit rules, a Langfuse stand-in in the receiver, and `llm_stub.py`.
  Verified against Langfuse 4.55, Alloy 1.20.1 and the Python SDKs and instrumentations named
  in `sdk-notes.md`: Langfuse reads the GenAI chat and tool attributes itself, the retrieval,
  workflow, prompt-link and time-to-first-chunk keys need the module's translation, the
  OpenInference switches leak embedding text unless the whole hide set is written, and Alloy
  1.20.1's `client_auth` block fails while its top-level basic-auth arguments work. An end-to-end
  run of a reference assistant against a real Langfuse added four rules: streamed calls are
  opened and read in one context (otherwise every streamed answer logs `Failed to detach
  context` at ERROR), the export scope counts at span start every span that could still join a
  feature (otherwise a trace gets a second root), AI spans carry their error type as the status
  description on the ops path and as `langfuse.observation.status_message` where content isn't
  allowed (instrumentations put the provider's message there, which can quote the input), and a
  managed prompt of another type is a fallback. The model stub's errors now quote the input.
  Experiments over OpenTelemetry: the module's `experiment()` and `experiment_item()` run a feature
  over a Langfuse dataset, one trace per item with the experiment keys stamped in the process
  (Langfuse's recipe uses baggage, which reaches the model provider), content only where the
  ceiling is `full`, evaluator scores on each item's root, and nothing on the ops path; the
  receiver's stand-in serves datasets (`--langfuse-datasets`) and summarises experiments; the
  audit also flags Langfuse keys handed to an OpenTelemetry baggage call. Verified against
  Langfuse 4.55 (experiments from plain OTLP, the experiments read API) and Alloy 1.20.1 (the
  collector's experiment routing).
- 2026-10-09, from instrumenting a production TypeScript API (Express, run with `tsx`) end to
  end into OneUptime: the job metrics label the job `job_name`, because the scrape sets `job` on
  every series (an app `job` label arrived as `exported_job`); extra resource attributes in the
  yml (such as `oneuptime.label.*`) are also rendered as `resource.opentelemetry.io/*` pod
  annotations, since the yml's resource reaches only the SDK's signals and the collector gives
  stdout logs and scraped metrics theirs; an app-metrics section (names, gauge in flight around a
  function, bounded labels); PROMPT.md trimmed to one detailed, stack-neutral prompt per task
  (add, find gaps, extend with every signal, verify); Node.js notes: the import-in-the-middle
  `include` list and one copy of it, the route through the RPC metadata (no Express
  instrumentation, `unmatched` for requests rejected before routing), the root-route trailing
  slash, a rate-limited SDK diagnostics logger (failed exports were silent), CLIENT spans for FCM
  and SMTP, wrapping classes of static methods, SIGTERM as PID 1, a module logger when the app has
  none, npm 11 install scripts and lockfiles. `detect.py` reports the container's real start
  command (Dockerfile, its entrypoint script, Procfile) and a TypeScript loader, and no longer
  reads `vitest` as vite; `audit.py`'s `logger-bypass` also reports `console`/`print` lines in a
  project with no app logger. The instrument steps run dependencies as throwaway containers and
  pick contract requests that answer the same way twice.
- 2026-10-08, from an end-to-end run of 13 services through Alloy into OneUptime 14: the
  collector passes metric types on (`honor_metadata`, otherwise every scraped series becomes a
  gauge) and replaces the scrape identity, including its legacy attribute names; a tested Alloy
  mapping for the JSON log format, without the loki receiver's own attributes; OneUptime notes
  (an exception's environment comes from the older `deployment.environment`, any record with
  `exception.*` becomes an exception, `rum.app_identifier` must equal the browser's
  `service.name`, profiles keep only `service_name` and use `trace_id`/`span_id` sample labels as
  span links, Java converts JFR to pprof in-process with `assets/java/PprofExporter.java`);
  `exception.*` only on the failure's ERROR record; span profiles on backends that index the ids:
  integrations for Go, threaded Python servers and Java, and on event loops (Node.js, Python
  asyncio) the span ids on the labelled heavy synchronous sections; backend-side acceptance
  checks; browser RUM flushes metrics on `visibilitychange` only (not `pagehide`, whose export
  would hold back INP and CLS) and exports them as deltas, and the browser test slows clicks so
  INP is measured; `validate.py` warns when `rum.app_identifier` differs from
  `resource.service.name`.
- 2026-10-07: metrics are ops-only by default, produced with each stack's Prometheus client
  library with short labels and fixed buckets (OTLP metric push is opt-in); the resource is the
  eight standard attributes, plus `k8s.cluster.name` for several clusters; no framework
  middleware spans; scheduled jobs and WebSocket traffic get root spans; a logging level policy
  with step logs and a slow-step threshold; API docs paths are excluded like probes; emails in
  request paths are masked; the instrument report ends with a telemetry inventory. New audit
  rules `logger-bypass`, `scheduler-no-root-span` and `sdk-internal-patch`.

## Maintainer checks

```bash
python3 -m unittest discover -s scripts/tests -v
uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml \
  python -m unittest discover -s scripts/tests -p "test_receive.py"
```

The plain run skips the receiver tests when grpcio and the protobuf packages are missing; the
second command runs them.
