# Langfuse

Langfuse is the standard's LLM-observability destination: it receives the AI part of each
trace, with content where the [content policy](../signals/genai.md#content) allows it and the
content destination is `langfuse` ([destination](../signals/genai.md#destination)), and holds
prompts and scores. It takes traces only; metrics, logs, profiles and the content-free copy of
every trace go to the ops backend as usual. What to emit is in
[signals/genai.md](../signals/genai.md); this page covers ingestion, the routes, what Langfuse
reads from a span, and the platform work that stays in Langfuse.

Check Langfuse's current docs at run time; ingestion details, APIs and limits change between
releases. Its agent-facing docs are `https://langfuse.com/llms.txt`, any docs page as Markdown
(append `.md`, or send `Accept: text/markdown`), and the docs MCP server at
`https://langfuse.com/api/mcp`. Platform work (prompts, datasets, experiments, evaluators, score
configs, annotation queues) follows Langfuse's own agent skill, `github.com/langfuse/skills`,
and its CLI; this standard owns what the service emits, its safety, configuration and
verification.

## Ingestion

| What | Endpoint | Protocol | Auth |
| --- | --- | --- | --- |
| Traces | `{base_url}/api/public/otel/v1/traces` | OTLP over HTTP (protobuf or JSON); no gRPC; traces only | Basic auth from `public_key:secret_key`, plus `x-langfuse-ingestion-version: 4` |
| Scores | `POST {base_url}/api/public/scores`; the JS client (5.11) batches them into `POST /api/public/ingestion` instead | JSON | Basic auth |
| Prompts | `GET {base_url}/api/public/v2/prompts/{name}?label={label}` (or `?version=`) | JSON | Basic auth |
| Media | `POST {base_url}/api/public/media`, then an upload to the presigned URL it returns | JSON | Basic auth |
| Reading back (verification) | `GET {base_url}/api/public/v2/observations?traceId={id}&fields=core,basic,time,io,metadata,prompt,usage,model` (`time` holds the completion start and time to first token); `GET {base_url}/api/public/v3/scores?traceId={id}` | JSON | Basic auth |

- **The ingestion header** turns on real-time ingestion into Langfuse 4's data model; without
  it, spans can take ten minutes to appear. Langfuse's own SDKs don't send it (they identify
  themselves with `x-langfuse-sdk-name` and `x-langfuse-sdk-version`); every other exporter (a
  Go or Java module, the collector) sends it. It is a protocol constant fixed in code, never a
  yml header.
- **Base URL** is the origin only, for example `https://cloud.langfuse.com` (EU) or
  `https://us.cloud.langfuse.com`, or the self-hosted URL; the module adds the paths.
- **Keys** belong to one project. The public key alone can create scores (the browser SDK uses
  it), so it is not harmless; the secret key reads and writes the whole project and stays on
  the server.

## Routes

The mode ([signals/genai.md](../signals/genai.md#modes)) decides who makes the AI spans; the
route decides who sends them. `sdk` goes with the app route only. Either way each span reaches
Langfuse once, from one sender: the SDK's span processor or plain OTLP, never both
([one span sender](../signals/genai.md#modes)).

### App route (default)

`genai.langfuse.export: app`. The module attaches Langfuse to its own tracer provider, so the
ops exporter and Langfuse see the same spans and trace ids. Per stack and mode:

| Stack | `hybrid` | `sdk` | `otel` |
| --- | --- | --- | --- |
| Python (`langfuse` 4.7.0 or later; `span_exporter` seen with 4.15) | `Langfuse(public_key, secret_key, base_url, tracer_provider=<the module's provider>, should_export_span=<the scope filter>, mask_otel_spans=<the content guard>, span_exporter=<the module's transport>, timeout, flush_interval)`. The transport posts OTLP protobuf with the SDK's own headers (Basic auth, `x-langfuse-sdk-name`, `x-langfuse-sdk-version`, `x-langfuse-public-key`) and reads no `OTEL_EXPORTER_OTLP_*` variable. The SDK's default `OTLPSpanExporter` would merge the ops headers and take the ops TLS files | the same client; the module imports `langfuse.openai` (it patches the OpenAI classes) instead of starting the GenAI instrumentation, and its step helpers use `start_as_current_observation(as_type=…)` | the module's exporter: the same transport, with Basic auth, the ingestion header and gzip, behind the scope filter and the content guard, in a `BatchSpanProcessor` |
| JS and TS (`@langfuse/otel`, `@langfuse/client`, `@langfuse/tracing`, `@langfuse/openai`, 5.x) | `NodeSDK({ spanProcessors: [stamping, new BatchSpanProcessor(opsGuard(otlpExporter)), guarded(new LangfuseSpanProcessor({ publicKey, secretKey, baseUrl, shouldExportSpan, mediaUploadEnabled, exporter }))] })`. `exporter` is the module's transport: `createOtlpHttpExportDelegate` with `httpAgentFactoryFromOptions` from `@opentelemetry/otlp-exporter-base/node-http` (0.222), with the processor's own headers. The default `OTLPTraceExporter` merges the ops header variables into its own headers, takes the ops TLS files and compression, and can't be made to read none. `guarded` hands the inner processor a copy of each ended span after the content guard (the SDK's `mask` sees only `langfuse.*` keys). No `environment` or `release`: Langfuse reads the environment from the resource and the module stamps the release. Also: an allow-listed baggage propagator, the SDK's logger off (`configureGlobalLogger`), and scores under `suppressTracing` | the same processors. Model calls go through `observeOpenAI(client)`: the module wraps the app's client and taps the client beneath it. The step helpers use `startActiveObservation(name, fn, { asType })`, after `setLangfuseTracerProvider(trace.getTracerProvider())` | `new BatchSpanProcessor(langfuseExporter)`: the same transport, with Basic auth, the ingestion header and gzip, behind the scope filter and the content guard |
| Go (no Langfuse SDK) | the asset's exporter in a batch processor on the same provider: OTLP/HTTP to `{base_url}/api/public/otel/v1/traces` with Basic auth, the ingestion header and gzip, on a transport of its own (`otlptracehttp` would read the ops exporter's variables). It is wrapped by the scope filter and the content guard, using an embedded `sdktrace.ReadOnlySpan` with its own attributes, status and events. Scores go through the asset's queue; app prompts and datasets through its REST client | not available: `hybrid` or `otel` | the same exporter; no REST client |
| Java (no Langfuse SDK) | the same exporter shape with the OTLP HTTP exporter (`DelegatingSpanData` for the guarded copy) and a REST client; Java agent services: prefer the collector route | not available | the same exporter |
| Others | the same exporter wrapper; .NET: prefer the collector route | not available | the same exporter |

- **Model calls.** A GenAI instrumentation makes the generation spans where it covers the client
  library and version; otherwise, and in Go, Java and the other stacks without one, the module's
  model-call helper makes the `CLIENT` span itself (`chat {model}`, the request and response
  attributes, usage, the time to first chunk), never both. In `sdk` mode Langfuse's own
  integration makes them.
- **The exporter (`otel`, and Go and Java in `hybrid`)** sends OTLP over HTTP (Langfuse takes no
  gRPC). It builds `Authorization: Basic base64(public:secret)` and
  `x-langfuse-ingestion-version: 4` in code and uses gzip. It applies the scope filter at export
  time and the content guard on a copy of each span; the guard can also replace a failed AI span's
  status description with its error type.
- **No setting of the ops exporter reaches Langfuse,** in any mode. The Langfuse path's transport
  reads no `OTEL_EXPORTER_OTLP_*` variable:
  - in Go, Java and Python because they are traces variables too;
  - in JS and Python because those exporters merge the variables' headers into their own.

  TLS to Langfuse uses the process's trust store. A private CA goes there:
  - `NODE_EXTRA_CA_CERTS` (Node);
  - `SSL_CERT_FILE` (Go);
  - `REQUESTS_CA_BUNDLE` (Python's `requests`);
  - the JVM truststore (Java).
- **The REST client (Go, Java)** has two calls: `POST /api/public/scores` (stable ids, the
  resource's environment) and `GET /api/public/v2/prompts/{name}?label=…` for app code, with a
  short TTL cache, a bounded timeout and the caller's fallback, like the SDKs' prompt cache.
  Ready-to-adapt, tested copies: [assets/go/langfuse](../../assets/go/langfuse) and
  [assets/java/langfuse](../../assets/java/langfuse); for Node, the scope, guard, processor proxy,
  transports and score client are in [assets/js/langfuse](../../assets/js/langfuse).
- **Shutdown:** the provider first (its Langfuse processor or exporter flushes), then the client's
  pending scores, within the grace period. Serverless JS: `exportMode: "immediate"` and a flush
  before the handler returns.

**One client per process.** The Python SDK keeps one client per public key and ignores the
settings of any later one, and every SDK shares state the same way, so the module builds the
client (with the provider, the scope filter and the guard in `hybrid` and `sdk`, API-only
otherwise) and app code takes it from the module for prompts and datasets; a client built
anywhere else moves into the module.

```yaml
genai:
  enabled: true
  langfuse:
    enabled: true
    export: app
    base_url: "${OBS_LANGFUSE_URL}"
    public_key: "${file:/var/run/secrets/observability/langfuse-public-key}"
    secret_key: "${file:/var/run/secrets/observability/langfuse-secret-key}"
```

### Collector route

`genai.langfuse.export: collector`. The app exports as usual, content and stamps travelling
inside the cluster, and the collector's Langfuse branch sends the AI spans
([collector](../collector/README.md#genai-routing)); it goes with `hybrid` (the app keeps the
SDK client for scores) or `otel` (the app needs no Langfuse keys unless its own code uses the
module's client). The exporter, with the keys read from files:

```alloy
local.file "langfuse_public_key" {
  filename = "/var/run/secrets/langfuse/public-key"
}

local.file "langfuse_secret_key" {
  filename  = "/var/run/secrets/langfuse/secret-key"
  is_secret = true
}

// The top-level form: in Alloy 1.20.1 the client_auth block fails with
// "no credential source provided". The files must not end with a newline:
// Alloy can't trim a secret.
otelcol.auth.basic "langfuse" {
  username = local.file.langfuse_public_key.content
  password = local.file.langfuse_secret_key.content
}

otelcol.exporter.otlphttp "langfuse" {
  client {
    endpoint    = "https://<langfuse host>/api/public/otel"   // the exporter appends /v1/traces
    auth        = otelcol.auth.basic.langfuse.handler
    compression = "gzip"
    headers     = { "x-langfuse-ingestion-version" = "4" }
  }
}
```

A Kubernetes Secret created with `kubectl create secret generic … --from-literal` has no
trailing newline; a file written with `echo` does.

## Mapping

What Langfuse 4 reads from a span (verified with 4.55 self-hosted). `langfuse.*` keys win over
the conventions.

| Langfuse field | Read from |
| --- | --- |
| Trace name | `langfuse.trace.name`, else the root span's name |
| User, session | `langfuse.user.id` or `user.id`; `langfuse.session.id`, `session.id` or `gen_ai.conversation.id` |
| Release, version | `langfuse.release`; `langfuse.version`, else `service.version` |
| Environment | `langfuse.environment`, else the resource's `deployment.environment.name`, lowercased |
| Tags, metadata | `langfuse.trace.tags`; `langfuse.trace.metadata.<key>` (top-level keys filterable); other span attributes land in the observation's metadata, resource attributes too |
| Type | `langfuse.observation.type`, else `gen_ai.operation.name`: `chat` → generation, `embeddings` → embedding, `invoke_agent` → agent, `execute_tool` → tool (renamed to `gen_ai.tool.name`); `retrieval` and `invoke_workflow` stay plain spans; any model attribute → generation |
| Input | `langfuse.observation.input`, else `gen_ai.input.messages` with `gen_ai.system_instructions` prepended as a system message, `gen_ai.prompt`, `input.value`, and on tool spans `gen_ai.tool.call.arguments` |
| Output | `langfuse.observation.output`, else `gen_ai.output.messages`, `gen_ai.completion`, `output.value`, and on tool spans `gen_ai.tool.call.result` |
| Trace input, output | the root observation's input and output (`langfuse.observation.input` and `langfuse.observation.output` on the root span), which the trace list and the session view show; `langfuse.trace.input` and `langfuse.trace.output` are dropped (seen with 4.58) |
| Model, parameters | `langfuse.observation.model.name`, else `gen_ai.response.model`, then `gen_ai.request.model`; `gen_ai.request.*` |
| Usage | `gen_ai.usage.*` (below) |
| Cost | `langfuse.observation.cost_details`, `gen_ai.usage.cost` as the total, else the model's prices |
| Prompt link | `langfuse.observation.prompt.name` and an integer `langfuse.observation.prompt.version`, for a prompt that exists in the project; `gen_ai.prompt.name` alone doesn't link |
| Level, status message | `langfuse.observation.level` (`DEBUG`, `DEFAULT`, `WARNING`, `ERROR`), else ERROR from the span status; `langfuse.observation.status_message`, else the status description, which isn't kept when the attribute is set |
| Completion start | `langfuse.observation.completion_start_time` (ISO 8601, `Z` or `+00:00`); `gen_ai.response.time_to_first_chunk` isn't read |

The retrieval query and documents aren't read either, so the module's translation table maps
them, the types and the time to first chunk
([signals/genai.md](../signals/genai.md#translation-to-langfuse)).

## Trace context in Langfuse 4

Langfuse 4 stores observations and filters on each observation's own attributes: a user,
session, tag or metadata value set only on the root is invisible to filters on the other
observations. The module's stamping processor therefore puts the
[trace context](../signals/genai.md#trace-context) on every span of the feature; Langfuse's own
SDKs (Python 4.7.0, JS 5.4.0 and later) do the same for the attributes they propagate. A trace
whose root comes from an unstamped upstream service (the collector gateway) gets user and
session from the stamped observations.

## Usage, cost and models

- Send the semantic-convention usage as the provider reports it: input and output inclusive of
  cache and reasoning tokens, plus the cache and reasoning counts. Langfuse makes the buckets
  exclusive: input 100 with 30 cache reads and 10 cache writes is stored as input 60, cache
  read 30, cache write 10; output 40 with 15 reasoning tokens as output 25, reasoning 15; the
  total stays 140.
- Cost comes from Langfuse's model definitions, matched by a regex on the model name (the
  response model first), unless the span carries a cost; a reported cost, 0 included, wins.
  Self-hosted and custom models need their own definitions with prices: a platform handoff.

## Prompt links

Prompt management is the application's own Langfuse feature: its code fetches prompts (through
the module's client), caches them, picks labels and keeps a fallback. Telemetry covers what the
generations show:

- **Linking:** the app hands the model-call helper the prompt it used (name, version, fallback or
  not); the generation gets the two prompt keys above. Links on non-generation observations are
  ignored, and a fallback prompt is never linked: the generation is WARNING with
  `app.genai.prompt.fallback`.
- **Variables and placeholders** hold user input, so a compiled prompt is content; a prompt that
  includes another (`@@@langfusePrompt:…@@@`) is resolved by Langfuse when fetched, and the link
  names the prompt the app fetched.
- **Config:** a prompt version's config (model, temperature) reaches the generation as the call's
  `gen_ai.request.*` attributes.
- **For the app's developers** (the handoff): fetch through the SDK's cache with a bounded timeout
  and a fallback in code, so a fetch never blocks a request; ask for the prompt type the code
  expects, since a prompt name keeps its type and the SDKs return the stored type whatever the
  code asks for (treat a mismatch as the fallback).

## Scores

- **Types:** numeric, categorical, boolean (`1` or `0`), text (1 to 500 characters) and
  correction (edited output; in the API and the Python SDK 4.15). Text and correction values
  and comments can hold what a user wrote: send them only where the feature's content mode
  allows it.
- **Targets:** a trace, an observation (with its trace id), a session or a dataset run. A score
  may arrive before its trace and links when the trace does.
- **Idempotency:** a score sent again with the same id replaces the earlier one when its name
  and date match too (Langfuse's docs); otherwise it is a second score. Use stable ids such as
  `{message id}:user_feedback`, and send the whole score each time.
- **Environment:** a score's environment is `default` unless sent; the module sends the
  resource's environment.
- **Configs:** a score config (name, type, range or categories) validates the score when its id
  is sent; configs are a platform handoff.
- **Modes:** `hybrid` and `sdk` send scores; `otel` never calls Langfuse's API, so feedback stays
  in the app's database, its log record and its counter.
- **Server-side:** the app's API receives feedback and approvals, checks ownership, stores
  them, then scores with the secret key. The browser SDK can score with the public key alone,
  which lets anyone holding it score any trace: only for low-risk internal tools.

## Experiments

An experiment is one run of a feature over a dataset; each item is a trace, and Langfuse groups
the item traces into the run and compares runs ([signals/genai.md](../signals/genai.md#experiments)
for the module's side). Verified with Langfuse 4.55: experiments are built from OTLP spans alone.

- **Datasets** are platform data: built in the UI, from annotated traces, or through the API
  (`POST /api/public/v2/datasets`, then `POST /api/public/dataset-items` with `datasetName`,
  `input`, `expectedOutput`, `metadata`; an item upserts by `id`, `ARCHIVED` items drop out of
  later runs, and every change makes a new version, by timestamp). The runner reads the items
  (`GET /api/public/dataset-items?datasetName=…`, or the SDK's `get_dataset(name, version=…)`).
  Items copied from production traces hold what users wrote: create them only where content may
  live, or after redaction.
- **Keys on the item's spans** (every span of the item's trace unless marked root):

  | Key | Where | Notes |
  | --- | --- | --- |
  | `langfuse.experiment.id`, `langfuse.experiment.name` | every span | required, unique per run |
  | `langfuse.experiment.dataset.id` | every span | the dataset's id; for local data, a stable id |
  | `langfuse.experiment.item.id` | every span | the dataset item's id |
  | `langfuse.experiment.item.root_observation_id` | every span | the item root's own span id |
  | `langfuse.experiment.item.version` | every span | the dataset version the run pinned, if any |
  | `langfuse.experiment.metadata.*` | every span | run metadata, the same for every item |
  | `langfuse.environment` | every span | `experiment` |
  | `langfuse.observation.input`, `langfuse.observation.output` | root | required: the item's input and the actual output |
  | `langfuse.experiment.item.expected_output` | root | JSON |
  | `langfuse.experiment.description`, `langfuse.experiment.item.metadata.*` | root | |

  The root has no parent and there's no enclosing run span. Langfuse 4.55 also built the run
  with the keys on the root only; the module stamps every span, as Langfuse's runners do.
  Langfuse's own recipe carries the keys in baggage with a `BaggageSpanProcessor`; the module
  never does, since baggage reaches every outbound call.
- **Scores** go on the item's root observation: `traceId` and `observationId` (the root's span
  id), with a stable id. Evaluators configured in Langfuse also run on experiment items.
- **Reading back:** `GET /api/public/experiments?fromStartTime=…&id=…` (run, item count, dataset,
  `fields=core,metadata`) and `GET /api/public/experiment-items?fromStartTime=…&experimentId=…`
  with `fields=core,dataset,io,itemMetadata,experimentMetadata,scores` (input, output, expected
  output, scores; metadata arrives as `experimentItemMetadata` and `experimentMetadata`). The
  dataset-runs endpoints answer 404 on a v4 events-only deployment.

## Media

Base64 images, audio and files in observations are uploaded to Langfuse's media store and
replaced by reference strings when the SDK handles them. Under the standard, media is a
reference (type, size, hash) unless `genai.content.media: upload`, the feature's mode is
`full` and the destination is `langfuse` ([signals/genai.md](../signals/genai.md#modes)).

## Without content

With `genai.content.destination: ops`, content goes to the ops backend's AI views instead, and
Langfuse receives the AI steps without it:

- **What arrives:** the structure, types, usage, cost, timing, levels, prompt links and the trace
  context.
- **No content:** the module's content guard removes every content key on the Langfuse path, as
  in mode `off`.
- **Experiments** need the `langfuse` destination.

## Environments and projects

- **Environment names** come from the resource, lowercased. Langfuse accepts lowercase letters,
  digits, `-` and `_`, at most 40 characters, not starting with `langfuse`; `validate.py` checks
  the resolved value.
- **Projects:** keys belong to a project, and access is granted per project. Use separate
  projects for production and non-production whenever non-production may hold real data, and
  give each its keys; one project with environments fits only when every environment's data is
  synthetic.
- **Data residency and retention:** on Langfuse Cloud, user ids and content leave the cluster;
  pick the region and set retention per project.

## Self-hosting

- The stack: web and worker containers, Postgres, ClickHouse, Redis or Valkey, and an
  S3-compatible blob store; `LANGFUSE_INIT_*` variables create the organisation, project, keys
  and first user headlessly.
- In Langfuse 4's events-only mode, the traces, trace-by-id and sessions read APIs answer 404;
  read observations through the v2 observations API (seen with 4.55).

## Versions and dates

- Seen with: Python SDK 4.17, JS SDK 5.13 (`@langfuse/otel`), server 4.55 self-hosted, Alloy
  1.20.1 (2026-10).
- Langfuse Cloud becomes v4-only on 2026-11-16, when legacy APIs and ingestion are removed;
  self-hosted v3 gets security patches through January 2027.

## Replacing the SDK's own instrumentation

When the module takes over from the SDK's `@observe` decorators and its OpenAI integration (modes
`hybrid` and `otel`), what people already use in Langfuse moves too:

- **Trace names:** name the feature after the existing trace name, so trace-name filters,
  dashboards and evaluators keep working.
- **Observation names** become the conventions' (`OpenAI-generation` → `chat {model}`, a tool
  wrapper → `execute_tool {tool}`, a retriever → `retrieval {source}`). List them in the
  migration note: evaluators and saved filters keyed on the old names need updating.
- **Metadata:** the team stamp is `langfuse.trace.metadata.team`. Keep an existing metadata key
  that people filter on (`org`, for example) as well, or list the change.

## Platform handoff

The skill emits telemetry; these stay with the platform or the Langfuse project owners, and the
report lists them:

- projects and keys per environment, as mounted Secret files for the app or the collector;
- egress from the cluster to the Langfuse host;
- the data region, retention, members and roles;
- model definitions with prices for custom and self-hosted models;
- score configs for every score name the service sends;
- prompt management, which stays with the app's developers: labels per environment, the cache,
  timeout and fallback in code for every fetched prompt, and an owner for each fallback;
- LLM-as-a-judge evaluators with their sampling, and annotation queues for review;
- the datasets experiments run on, their owners, and which items may come from production;
- the ingestion header on any exporter that isn't a Langfuse SDK.

Never `langfuse.trace.public`: a public trace is readable by anyone with its link.

[Documentation index](../README.md)
