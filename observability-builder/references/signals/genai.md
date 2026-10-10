# GenAI

Model calls, agents, tools and retrieval produce two kinds of telemetry. **Ops telemetry**
(latency, errors, tokens, which dependency failed) is the same as for any other dependency and
goes where all ops telemetry goes. **AI-quality telemetry** (prompts, completions, tool
arguments, retrieved documents, prompt versions, scores) needs an LLM-observability backend,
Langfuse in this standard ([backends/langfuse.md](../backends/langfuse.md)), as a second
destination of the same traces. Content never travels on the ops path.

This standard covers telemetry only. Langfuse features an application *uses* (prompt management,
datasets, evaluators, score configs, annotation queues) belong to the application and its
developers; the standard defines only the telemetry they leave behind: the link from a
generation to the prompt version the app used, scores with stable ids, experiment keys. App
code reaches those features through the module's Langfuse client, the one client of the process.

The OpenTelemetry GenAI conventions are in Development and their names still move; the names
here are as of 2026-10. Check the current registry before relying on one, and keep every
Langfuse-specific key in the module's one translation table ([below](#translation-to-langfuse)).

## Required behaviour

1. **One tracer provider, two destinations.** The module's provider feeds the ops exporter
   and, with `genai.langfuse.export: app`, a Langfuse span processor attached to that same
   provider, so both tools show the same trace ids. Never a second provider or a second
   sampler for AI work. With `export: collector`, the collector's Langfuse branch picks the
   spans ([collector](../collector/README.md#genai-routing)).

   The Langfuse exporter takes nothing from the ops exporter. OTLP exporters fill unset options
   from the `OTEL_EXPORTER_OTLP_TRACES_*` variables, and the Langfuse exporter is a traces exporter
   too, including the default one inside the Langfuse SDK's processor. Python's and JS's merge the
   variables' headers into their own, which would send the ops backend's token to Langfuse. Ops TLS
   files would make the export fail against a public Langfuse. So the Langfuse path gets a
   transport built from explicit settings that reads no variables
   ([langfuse.md](../backends/langfuse.md#app-route-default)), and the loader passes the ops
   exporter's headers as options ([config.md](../config.md#what-the-loader-writes)).
2. **Every AI step is a span with the GenAI conventions** ([step kinds](#step-kinds)): the
   operation, provider, request and response model, finish reasons and usage. Model and
   embedding calls are `CLIENT` spans from the provider's instrumentation library or the
   module's model-call helper, never both. Where the instrumentation makes the span, the helper
   still wraps the call, with no span of its own: it takes the first span started inside it
   (some instrumentations set the operation and model only when the call ends, so a processor
   can't recognise the span when it starts), adds the prompt link and the time to first chunk,
   and records the metrics. A streamed call's span ends when the stream ends, fails or is left
   early: the helper closes it in its iterator's `finally` or `defer`, because SDK wrappers don't
   always do so. Where an instrumentation attaches its span to the context while a stream is open
   (Python's), the stream is opened and read in one context of its own: the server may send the
   response from another task ([sdk-notes](../sdk-notes.md#genai-and-langfuse)).
   The module's helpers set the Langfuse observation type where the operation doesn't imply it
   (retriever, workflow, guardrail, evaluator, event).
3. **The AI feature context.** Each AI entry point (a route, a WebSocket message type, a job
   item) declares its feature once, after authentication:
   `genai.feature(name, user, conversation)`, in the language's own form:
   - **Python:** it sets the context for the rest of the request.
   - **Node:** a callback, `feature(name, { user, conversation }, fn)`, because
     `AsyncLocalStorage.enterWith` is experimental.
   - **Go:** it returns the context that the handler passes on; spans started from the old one miss
     the stamps.
   - **Java:** a `Scope` closed with try-with-resources.

   It also stamps the entry span, which started before it (the module keeps it in the request
   state). The module's stamping processor copies the
   [trace context](#trace-context) (feature as trace name, user, conversation as session,
   release, tags, bounded metadata) onto every span started inside it, third-party spans
   included, because Langfuse filters and groups per observation. The context lives in a key
   the propagators never send: never baggage, which every outbound call carries, the model
   provider included. This is the one scoped exception to "request context on the entry span
   only" ([data-model.md](../data-model.md#resource-vs-request-attributes)), and the ops path
   strips these keys.
4. **Content follows the [content policy](#content).** Prompts, completions, system
   instructions, tool definitions, arguments and results, retrieval queries and retrieved
   documents are content. The module's content helper (`genai.content`) records them: it
   applies the feature's mode, the content sample, redaction and the size cap. Instrumentation
   libraries capture under the switches the loader writes
   ([config.md](../config.md#what-the-loader-writes)). Content goes to Langfuse only, never to
   logs, metrics, span events or the ops backend.
5. **The ops path carries no content.** On the app route the module's ops guard wraps the ops
   exporter: AI spans keep only the [allow-list](#the-ops-allow-list) and their error type as the
   status description (instrumentations put the provider's error message there, and providers
   and gateways can quote the input in it), every span loses the content keys and every
   `langfuse.*` key, and Langfuse event observations and [experiment](#experiments) runs are
   dropped (an event's log record is the ops copy). AI instrumentation scopes lose their
   attributes too: Langfuse's Python SDK puts its public key there. On the collector route the
   collector's ops branch does the same.
6. **Status and level.** A step's status is ERROR only when its own work failed: a provider
   error or timeout, a tool that threw, a refused tool call. Success is UNSET, never OK. A
   step that succeeded in a degraded way keeps UNSET and gets
   `langfuse.observation.level=WARNING` with a constant `langfuse.observation.status_message`:
   a guardrail block, output cut by the token limit, a fallback prompt or model, a recovered
   tool failure, the agent's step limit reached. Langfuse takes ERROR from the span status.
7. **Usage on every generation and embedding**, streams included (`stream_options.include_usage`
   or the provider's equivalent): `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`
   as the provider counts them, cache and reasoning tokens included, plus
   `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens` and
   `gen_ai.usage.reasoning.output_tokens` when reported. Never subtract them yourself; Langfuse
   does. Streamed calls record `gen_ai.response.time_to_first_chunk`. Provider-reported cost,
   when there is one, goes in `gen_ai.usage.cost`.
8. **Prompt links.** The app fetches its prompts itself (fetching, caching, labels and fallbacks
   are the app's own) and hands the model-call helper what it used: name, version, and whether
   it was the fallback kept in code. A generation that used a managed prompt carries
   `gen_ai.prompt.name` and `app.genai.prompt.version`; the translation table turns them into the
   keys Langfuse links on. A prompt's config parameters arrive as the call's `gen_ai.request.*`,
   and its variables and placeholders are content. A fallback is never linked: the generation
   gets WARNING and `app.genai.prompt.fallback=true`.
9. **GenAI metrics at the model-call hook:** `gen_ai_client_operation_duration_seconds`,
   `gen_ai_client_tokens_total` and `gen_ai_client_time_to_first_chunk_seconds`, recorded where
   each call returns, so unsampled requests count too ([metrics](metrics.md#genai-metrics)).
10. **Logs carry ids, never content.** One ERROR record per failure, as everywhere
    ([errors](errors.md)), with the provider's exception message sanitised: it can echo the
    prompt. Where content isn't allowed, a failed AI span sent to Langfuse gets its error type as
    `langfuse.observation.status_message`, which Langfuse keeps instead of the status description.
    The instrumentations' GenAI log events are off. Lifecycle events carry ids and
    bounded values only: `genai.approval.requested`, `genai.approval.decided`,
    `genai.feedback.received`, `guardrail.blocked`, `rag.no_documents`.
11. **AI entry points are always sampled.** Feedback, approvals and evaluations need the trace,
    so the module's rule sampler keeps the entry points the feature registry lists; everything
    else follows `tracing.sampler`. Langfuse never samples on its own.
12. **Langfuse never sits on the request path for telemetry.** Spans and scores leave in the
    background. A prompt fetch is the app's own call; the report notes it as a dependency, and
    the handoff list asks for a cache, a bounded timeout and a fallback in code
    ([backends/langfuse.md](../backends/langfuse.md#prompt-links)).
13. **Scores are sent by the server** with stable ids (`{entity id}:{score name}`), so a repeat
    updates the score instead of adding one ([ai-agent.md](../workloads/ai-agent.md#user-feedback)).
    The entity is what the score judges: a message, an approval, an experiment item, or, for a
    one-shot result with no id of its own (an extraction), its trace id. The secret key never
    reaches a browser.

## Step kinds

| Step | Kind | Name | Langfuse type | Attributes |
| --- | --- | --- | --- | --- |
| Model call | `CLIENT` | `chat {gen_ai.request.model}` (`generate_content`, `text_completion` likewise) | generation, implied | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` and parameters, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, usage, `gen_ai.response.time_to_first_chunk`, `gen_ai.output.type`, `gen_ai.prompt.name`, `app.genai.prompt.version` |
| Embedding | `CLIENT` | `embeddings {gen_ai.request.model}` | embedding, implied | `gen_ai.request.model`, `gen_ai.usage.input_tokens`; vectors never |
| Retrieval | `CLIENT` for a remote store, `INTERNAL` in process | `retrieval {gen_ai.data_source.id}` | retriever, set by the module | `gen_ai.data_source.id`, `app.rag.documents_retrieved`, `app.rag.top_score`; the query and the documents are content |
| Tool execution | `INTERNAL` | `execute_tool {gen_ai.tool.name}` | tool, implied | `gen_ai.tool.name`, `gen_ai.tool.type`, `gen_ai.tool.call.id`; arguments and result are content. The tool's own outbound calls are `CLIENT` children that stay ops-only |
| Agent run | `INTERNAL` (`CLIENT` for a remote agent) | `invoke_agent {gen_ai.agent.name}` | agent, implied | `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.conversation.id`, `app.genai.agent.steps`, `app.genai.agent.outcome` |
| Workflow | `INTERNAL` | `invoke_workflow {gen_ai.workflow.name}` | chain, set by the module | `gen_ai.workflow.name` |
| Guardrail | `INTERNAL` | `guardrail {name}` | guardrail, set by the module | `app.genai.guardrail.name`, `app.genai.guardrail.result` (`pass`, `block`, `flag`); a guardrail that calls a model has that generation as its child |
| In-app evaluation | `INTERNAL` | `evaluate {gen_ai.evaluation.name}` | evaluator, set by the module | `gen_ai.evaluation.name`, `gen_ai.evaluation.score.value`, `gen_ai.evaluation.score.label`; the explanation is content |
| Event (approval requested, handoff) | `INTERNAL`, zero duration | the event's name, e.g. `approval requested` | event, set by the module | ids and bounded values only |

Names never carry ids, user text or prompt content. A guardrail is never typed as a generation
and a model call never as anything else: the prompt link and the cost belong to generations.
The `app.*` attributes above are the module's own, in the project's namespace
([enrichment.md](../enrichment.md)); content never goes in an `app.*` attribute.

## Content

### Content keys

The module, the receiver and the collector use one list. Anything else an instrumentation adds
on an AI span is caught by the [ops allow-list](#the-ops-allow-list), which fails closed.

| Source | Keys |
| --- | --- |
| GenAI conventions | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `gen_ai.retrieval.query.text`, `gen_ai.retrieval.documents`, `gen_ai.memory.query.text`, `gen_ai.memory.records`, `gen_ai.evaluation.explanation`; the legacy `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.prompt.N.*`, `gen_ai.completion.N.*` and `gen_ai.prompt.variable*` |
| Langfuse | `langfuse.observation.input`, `langfuse.observation.output`, `langfuse.trace.input`, `langfuse.trace.output`, `langfuse.experiment.item.expected_output` |
| OpenLLMetry | `traceloop.entity.input`, `traceloop.entity.output` |
| OpenInference | `input.value`, `output.value`, `llm.input_messages.*`, `llm.output_messages.*`, `llm.prompts.*`, `llm.prompt_template.template`, `llm.prompt_template.variables`, `llm.tools.*`, `retrieval.documents.*`, `reranker.*`, `embedding.embeddings.*` |
| Vercel AI SDK | `ai.prompt*`, `ai.response.text`, `ai.response.object`, `ai.response.toolCalls`, `ai.toolCall.args`, `ai.toolCall.result` |
| Span events | every event named `gen_ai.*` |

### Modes

`genai.content.capture` is the most an environment allows; each feature also declares the most
it allows, in the feature registry, and gets the lower of the two:

| Mode | What reaches Langfuse | Where |
| --- | --- | --- |
| `off` | no content key; the step's structure, usage, timing and ids only | production by default; any feature that handles data nobody may read |
| `redacted` | content after the feature's redactor, then the size cap. A feature without a redactor records nothing | production, for a feature whose owner signed off on the redactor |
| `full` | raw content, size cap only | environments with synthetic or consented data only; the chart sets it ([charts.md](../charts.md)) |

- **The content sample.** `content.sample_ratio` keeps content on that share of traces,
  decided from the trace id so a trace has content on all its steps or on none.
- **The size cap.** `content.max_chars` per attribute, after redaction, cut structurally: the
  value stays valid JSON and the newest turn is kept.
- **Redaction happens where the attribute is created,** so every destination sees the same
  safe value. A redactor that fails omits the content and logs one rate-limited WARN naming the
  feature, never the value: content fails closed. `redacted` is not anonymisation; structured
  capture (extracted fields and confidences instead of the text) is often the better choice.
- **Media** (images, audio, files): `reference` records type, size and a hash; `upload` sends
  the file to Langfuse's media store and is allowed only with `full`. Identity documents are
  never uploaded.
- **Content is never logged**, never a metric label and never a span event; compiled prompts
  hold user input, so prompt variables are content too.

### The ops allow-list

An AI span (one with `gen_ai.operation.name`, `langfuse.observation.type` or
`openinference.span.kind`) keeps only these keys on the ops path:

- `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.*` except
  `gen_ai.request.stop_sequences`, `gen_ai.response.id`, `gen_ai.response.model`,
  `gen_ai.response.finish_reasons`, `gen_ai.response.time_to_first_chunk`, `gen_ai.usage.*`,
  `gen_ai.output.type`;
- `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.tool.type`,
  `gen_ai.tool.call.id`, `gen_ai.conversation.id`, `gen_ai.data_source.id`,
  `gen_ai.workflow.name`, `gen_ai.prompt.name`, `gen_ai.evaluation.name`,
  `gen_ai.evaluation.score.*`;
- `error.type`, `server.*`, `service.peer.name`, `code.*`, `thread.name` and the project's
  `app.*` attributes.

Every span, AI or not, also loses the content keys and every `langfuse.*` key on that path.

## Trace context

| Concept | Langfuse key | Source and rule |
| --- | --- | --- |
| Feature | `langfuse.trace.name` | the feature registry: kebab-case, bounded; the only source of trace names |
| User | `langfuse.user.id` | the verified principal, the same value as `user.id` on the entry span; at most 200 characters |
| Conversation | `langfuse.session.id`, with `gen_ai.conversation.id` on the AI spans | the app's conversation or thread id; at most 200 US-ASCII characters; never derived from `session.id`, which stays the login or replay session |
| Release | `langfuse.release` | `service.version` |
| Version | `langfuse.version` | the feature's or agent's configuration version, when it has one |
| Tags | `langfuse.trace.tags` | a few bounded values: channel, variant, `resumed`, `batch` |
| Metadata | `langfuse.trace.metadata.<key>` | bounded values such as tenant tier, document type or job run; alphanumeric keys, string values up to 200 characters; filterable only at the top level |
| Environment | none | the resource's `deployment.environment.name`, which Langfuse reads and lowercases |

Never personal data in tags or metadata, and never `langfuse.trace.public`. On the ops path the
same trace already carries `user.id` and `session.id` on its entry span, so the stamps are
stripped there.

## Translation to Langfuse

One table in the module, applied on the Langfuse path only. Chat messages, system instructions,
tool arguments and results, usage, cost, model and parameters need no translation: Langfuse
reads them from the GenAI conventions ([backends/langfuse.md](../backends/langfuse.md#mapping)).

| From | To | When |
| --- | --- | --- |
| feature, user, conversation, release, tags, metadata, version | the [trace-context keys](#trace-context) | every span in the feature context |
| step kind without an implied type | `langfuse.observation.type`: `retriever`, `chain`, `guardrail`, `evaluator`, `event` | that step's span |
| `gen_ai.prompt.name`, `app.genai.prompt.version` | `langfuse.observation.prompt.name`, `langfuse.observation.prompt.version` (an integer) | generations that used a managed prompt, never a fallback |
| `gen_ai.response.time_to_first_chunk` | `langfuse.observation.completion_start_time` (start time plus the delay, ISO 8601) | streamed generations |
| `gen_ai.retrieval.query.text`, `gen_ai.retrieval.documents` | `langfuse.observation.input`, `langfuse.observation.output` | retriever spans, under the content policy |
| a degraded success | `langfuse.observation.level=WARNING`, `langfuse.observation.status_message` (a constant reason) | the step that degraded |

## Modes

`genai.langfuse.mode` says who makes the AI spans and who sends them; app code written against the
module's helpers (`genai.feature`, the step helpers, `genai.model_call`, `genai.experiment_item`)
is the same in all three, so switching is one key (for example
`mode: "${OBS_LANGFUSE_MODE:-hybrid}"`):

| Mode | AI spans made by | Sent to Langfuse by | Langfuse API (scores) | Stacks |
| --- | --- | --- | --- | --- |
| `hybrid` (default) | the OpenTelemetry GenAI instrumentation and the module's helpers | the Langfuse SDK's span processor on the module's provider (`export: app`), or the collector (`export: collector`) | yes, through the SDK client (a small REST client in Go and Java) | all |
| `sdk` | the Langfuse SDK: its OpenAI integration for model calls, its observations for the module's steps | the SDK, from the module's provider | yes | Python, TypeScript |
| `otel` | as in `hybrid` | the module's own OTLP exporter (`export: app`), or the collector | no: reduced features, no feedback or evaluator scores | all |

What changes and what doesn't:

- **The same in every mode:** one tracer provider, the feature context and its stamps, the export
  scope, the content policy (the module's content guard runs on every Langfuse-bound span: an
  SDK's own capture is capped and redacted too), the ops guard, the GenAI metrics, the prompt link
  and the experiment keys.
- **`sdk` mode:** Langfuse's OpenAI integration makes the model observations and the module's
  steps use the SDK's observations; the GenAI instrumentation stays off (never both). Either way
  the model-call helper still wraps each call:
  - it renames the generation to the convention's `chat {model}` or `embeddings {model}`;
  - it sets the right type and the `gen_ai.*` keys the ops path keeps;
  - it ends the span on every path;
  - it puts the error type where the integration wrote the provider's message.

  Per language ([sdk-notes](../sdk-notes.md#genai-and-langfuse)):
  - **Python:** importing `langfuse.openai` patches the client library, so app code keeps its client.
    It names generations `OpenAI-generation`, sends usage in OpenAI's keys (which Langfuse maps) and
    logs the provider's exception, so the module's logging keeps only a logged exception's type.
  - **TypeScript:** `observeOpenAI(client)` wraps one client, so the module wraps the app's client
    and taps the client beneath the wrapper. The wrapper:
    - names spans `{Class}.{method}` and types embeddings as generations without usage;
    - ends the span before the caller's `await` resumes, and never ends a stream left early;
    - makes INTERNAL spans, so the convention's `CLIENT` kind is lost in this mode;
    - sets no OTel status.

    `@langfuse/tracing` also writes `langfuse_trace_id` into baggage, which the allow-listed baggage
    propagator keeps from going out ([traces](traces.md#propagation)).
- **`otel` mode:** no Langfuse SDK is loaded for telemetry. The exporter sends OTLP over HTTP with
  Basic auth from the keys and `x-langfuse-ingestion-version: 4`, applies the scope and the
  content guard itself, and can sanitise the status description too. Feedback stays in the
  app's database, its log record and its counter; nothing reaches Langfuse's API.
- **The Langfuse client is the module's.** The SDK keeps one client per public key and ignores
  the settings of any later one, so app code takes it from the module for its prompts and
  datasets, never constructs its own; in `otel` mode, and with the collector route, it's an
  API-only client created on first use.

Choosing:

| Project context | Mode |
| --- | --- |
| Go, Java and Kotlin, Rust, .NET, Ruby, PHP (no Langfuse tracing SDK) | `hybrid` (OTLP plus a REST client for scores) or `otel` |
| A framework that already emits GenAI or OpenInference spans (Spring AI, Semantic Kernel, Pydantic AI, Vercel AI SDK, OpenAI Agents SDK, LlamaIndex) | `hybrid` or `otel`, with no second source |
| Several destinations, or a likely backend change | `hybrid` or `otel` |
| No Langfuse keys in the app, or no scores needed | `otel`, often with the collector route |
| A Python or TypeScript team that works mainly in Langfuse (its OpenAI integration, `@observe`, the LangChain callback handler) | `sdk` |
| LangChain or LangGraph with Langfuse's callback handler | `sdk`, and no other LangChain instrumentation |

The invariants, checked by the [verification](#verification) runs in whichever mode:

1. One tracer provider; one instrumentation per AI library.
2. The trace context on every Langfuse-bound observation, never in baggage.
3. Explicit types where the operation doesn't imply one.
4. Semantic-convention names.
5. ERROR only for the step's own failure, WARNING through the level for degraded success, never
   OK.
6. Content only through the content policy.
7. Prompt links on generations, never on a fallback.
8. Usage on generations and embeddings.
9. The ops rules unchanged: metrics at the hook, one ERROR log per failure, no content in logs.
10. Scores sent by the server with stable ids (`hybrid` and `sdk`).

The SDK's `propagate_attributes` writes `session.id` and `user.id` on every span and can write
baggage, so it isn't used for them in any mode; the module stamps the trace context.

## Export scope

`genai.langfuse.scope` decides which spans go to Langfuse; each one is a billed observation on
Langfuse Cloud.

- **`ai_context`** (default): AI spans plus the spans of the feature context: the entry span,
  the module's function spans, a job or WebSocket root. Database, cache, HTTP and queue client
  spans stay ops-only. Langfuse shows the request's shape around the model calls.
- **`ai_only`**: only the spans the AI instrumentations and the Langfuse SDK create, Langfuse's
  own default. Observations then point at parents Langfuse never receives. The cheapest choice,
  for indexing and other high-volume AI work.

Either scope also exports the spans of an [experiment](#experiments) item, which carry
`langfuse.experiment.id` instead of a feature's trace name when Langfuse's own runner made them.

The Langfuse SDKs ask the scope filter when a span starts too, and mark the first span they
expect to export under one they don't as the trace's app root. The entry span starts before the
feature is declared, so at start the `ai_context` filter counts every span that could still join
a feature (all but non-AI `CLIENT` and `PRODUCER` spans) and decides on the stamp once the span
has ended; otherwise the first stamped span becomes a second root of the trace.

## Experiments

An experiment runs one feature over a Langfuse dataset, offline, and lets Langfuse compare the
runs: prompt v3 against v4, one model against another, a retrieval change. Each dataset item is
its own trace; the experiment keys group the traces ([backends/langfuse.md](../backends/langfuse.md#experiments)).
The runner is a command in the service's repository that calls the feature's own functions, the
code path production uses minus HTTP, with the module set up as in the service:

```python
run = genai.experiment(ASSISTANT, "prompt v4", dataset_id=data.id, model=settings.llm_model)
for item in data.items:
    with genai.experiment_item(run, item.id, item.input, item.expected_output, **item.metadata) as current:
        reply = await assistant.chat("experiment", f"experiment-{item.id}", item.input["message"])
        current.output({"answer": reply.answer})
        langfuse.score_item(current, "expected_terms", expected_terms(reply.answer, item.expected_output))
```

- **One trace per item.** `experiment_item` starts a root span with no parent and no enclosing
  run span, opens the feature context, and stamps the experiment and item keys on every span of
  the item's trace, in the process like the [trace context](#trace-context), never as baggage:
  Langfuse's own recipe uses baggage, which every outbound call carries, the model provider
  included. The keys go on the root when it starts; `root_observation_id` is the root's span id.
- **Unique per run.** The experiment's id and name are new for every run (the module appends a
  timestamp); bounded run metadata (prompt version, model, dataset version) goes in
  `langfuse.experiment.metadata.*`, item metadata in `langfuse.experiment.item.metadata.*`.
- **Content.** The item's input and output on the root, and the expected output, are content:
  an experiment runs only where the feature's mode is `full` (`genai.content.capture: full`,
  synthetic or curated data), unsampled, and the runner refuses to start otherwise, because an
  item without its input and output can't be compared.
- **Environment `experiment`** on the Langfuse path (`langfuse.environment`), so runs stay out
  of the service's dashboards; Langfuse's Python and TypeScript runners use `sdk-experiment`.
- **Off the ops path.** Experiment spans are evaluation data: the ops guard (or the collector's
  ops branch) drops every span with `langfuse.experiment.id`. Logs and the run's summary line
  behave as in any job.
- **Scores** from evaluators in code (exact match, schema valid, expected terms) go on the item's
  root observation with a stable id, `{experiment id}:{item id}:{score name}`, and the
  `experiment` environment; LLM-as-a-judge evaluators set up in Langfuse score the items too.
- **Failures.** A failed item keeps its ERROR on its own trace and the run goes on; the runner
  exits non-zero when any item failed.
- **Langfuse's runners** (`dataset.run_experiment` in Python and TypeScript) also work through the
  module's provider: they keep the keys in the process, and the module's scope filter and
  content guard accept experiment spans. Choose them when the team works mainly in Langfuse.

## Correlation

- **One trace id in both tools.** The ops backend shows the trace without content; Langfuse
  shows the AI steps with content. Langfuse's trace URL is
  `{base_url}/project/{project id}/traces/{trace id}`, for runbooks and internal tools.
- **Ids with the results.** The service stores the trace and observation ids next to each AI
  result it returns (a message id), so feedback, approvals and support requests find the trace.
- **Logs** carry native trace ids as everywhere; **metrics** join by service, model and time;
  **profiles** link to spans as usual ([profiling](profiling.md)).

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `genai.enabled` | `false` | the GenAI metrics, the feature context and, when configured, Langfuse; off keeps every capture switch off |
| `genai.content.capture` | `off` | `off`, `redacted` or `full`: the most this environment allows |
| `genai.content.sample_ratio` | `1.0` | share of traces with content, by trace id |
| `genai.content.max_chars` | `20000` | per content attribute, after redaction |
| `genai.content.media` | `reference` | `upload` only with `full` |
| `genai.metrics.enabled` | `true` | `gen_ai_client_*` on `/metrics` |
| `genai.langfuse.enabled` | `false` | Langfuse as the second destination |
| `genai.langfuse.mode` | `hybrid` | `hybrid`, `sdk` or `otel` ([above](#modes)) |
| `genai.langfuse.export` | `app` | `app` or `collector` (not with `sdk`) |
| `genai.langfuse.scope` | `ai_context` | `ai_context` or `ai_only` |
| `genai.langfuse.base_url` | none | the origin; required when Langfuse is on |
| `genai.langfuse.public_key`, `secret_key` | none | `${file:...}`; required for the app route or scores, and for app code that uses the module's client |
| `genai.langfuse.timeout`, `flush_interval` | `10s`, `5s` | per request; batch interval |
| `genai.langfuse.scores.enabled` | `false` | feedback, approval and evaluation scores (`hybrid` and `sdk`) |

The loader writes every instrumentation's capture switch from `genai.content.capture`, owns the
`LANGFUSE_*`, `TRACELOOP_*` and `OPENINFERENCE_*` variables like `OTEL_*`, and passes the keys
to the SDK as options, never as environment variables ([config.md](../config.md#genai)).

## Failure behaviour

- **Export fails open.** The Langfuse processor or exporter batches with a bounded queue; a slow,
  failing or unreachable Langfuse drops batches with one rate-limited WARN and never touches
  requests or the ops export.
- **A fallback prompt** (the app's choice when its fetch fails) is WARNING on the generation.
- **Content fails closed.** A redactor or size-cap failure omits the content.
- **A broken Langfuse configuration** (keys missing, `base_url` unusable) leaves Langfuse off
  with one ERROR naming the key; the ops telemetry is unaffected.
- **Shutdown** flushes the tracer provider, then the Langfuse client (pending scores), within
  the grace period.

## Verification

Model providers need credentials the skill never reads, so verification runs the app against
the skill's model stub, `scripts/llm_stub.py`, and the receiver's Langfuse stand-in
([acceptance.md](../acceptance.md#genai)):

```bash
python3 scripts/llm_stub.py    # prints {"openai": ".../v1", "anthropic": "..."}
```

Point the app's provider client at the stub with a dummy key, and `genai.langfuse.base_url` at
the receiver's OTLP/HTTP endpoint (the stand-in serves Langfuse's paths there). Run twice, with
scratch copies of the yml: once as a non-production environment with `full`, once as production
with `off` (or `redacted` for the features that have a redactor), each time with the
`TRACELOOP_*`, `OPENINFERENCE_*` and `LANGFUSE_*` capture variables set to "on" in the
environment to prove the loader overrides them. Drive a chat, a streamed chat, a tool call, a
failed call, a 429, a feedback score and, where there is one, an approval. When environments
switch modes, repeat the `full` run in each mode the service uses. The export line of the summary
shows who sent the spans:

- **`hybrid` and `sdk` in Python and TypeScript:** a Langfuse SDK.
- **`otel`, and `hybrid` in Go and Java:** no SDK, with the ingestion header.

`otel` sends no score. Then check in `summary.md` and `/metrics`:

- Langfuse-bound spans arrive with Basic auth and `x-langfuse-ingestion-version: 4`, with the
  step kinds, types and names above and the trace context on every observation;
- generations carry the model, usage and, for streams, the time to first chunk; prompt links
  only where a managed prompt was used;
- the failed call is ERROR with `error.type`; degraded steps are WARNING; nothing is OK;
- the `full` run has content on the Langfuse path only; the production run has none anywhere,
  redacted values only for redacting features, and the stray variables changed nothing;
- the ops stream has no content key, no `langfuse.*` key and no `gen_ai.*` span event, and the
  log records carry ids only;
- the feedback score was sent once per id, by the server; `gen_ai_client_*` series are on
  `/metrics` with bounded labels;
- an experiment runner, started once with the receiver's `--langfuse-datasets` serving a small
  dataset, shows in the summary's Experiments line: one trace and one root without a parent per
  item, input, output and expected output on every root, the item keys on every span, the
  `experiment` environment, scores on the roots, and no experiment span in the ops stream.

[Documentation index](../README.md)
