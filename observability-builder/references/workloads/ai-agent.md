# AI features and agents

How to instrument AI work by what it does: chat assistants, retrieval-augmented answers,
extraction, classification, agents with tools, several agents, batch AI jobs, indexing,
guardrails and in-app evaluation, plus human approval, user feedback and chat interfaces. The
signal rules are in [signals/genai.md](../signals/genai.md); this page chooses, per kind of
function, the observations, the trace context, the content mode, the levels and the scores.

## Shared defaults

- **One AI trace per request,** or per item in batch work. The entry point declares the feature
  once, after authentication (`genai.feature(name, user, conversation)`), and every span inside
  carries the trace context.
- **Content:** `full` outside production, `off` in production; `redacted` only for a feature
  with a redactor and the owner's sign-off ([modes](../signals/genai.md#modes)).
- **The usual signals stay:** the access log, step logs, one ERROR record per failure, the job
  helper for jobs ([batch.md](batch.md)), and the GenAI metrics on `/metrics`
  ([metrics](../signals/metrics.md#genai-metrics)).
- **Ids with every result.** Each AI response carries a message id; the service stores message
  id → trace id, observation id, feature and whether the trace was sampled, for feedback,
  approvals and support.

## By function

| Function | Observations | Trace context | Production content | Levels and scores | Also |
|---|---|---|---|---|---|
| Chat assistant | `invoke_agent` or `invoke_workflow` → `chat {model}` per turn; input and output guardrails | feature, user, conversation, a channel tag | off; `redacted` only with the owner's sign-off | WARNING on a fallback or a truncated answer; `user_feedback` scores; judges in Langfuse | time to first chunk; profile prompt assembly and tokenisation |
| Retrieval-augmented answer | `embeddings` (the query) → `retrieval {source}` (counts, top score, document ids as metadata) → `chat` with the prompt link | as above | the retrieved chunks are content | WARNING when no documents were found and the answer fell back; groundedness and relevance through Langfuse evaluators | embedding tokens; a `rag.no_documents` log event |
| Extraction or OCR | `invoke_workflow` → OCR (ops-only unless a vision model) → `chat` with `gen_ai.output.type=json` → `evaluate schema` | feature, requester, document type as metadata | off; record the extracted fields and confidences instead of the text; media as a reference | WARNING when the output had to be repaired; a `schema_valid` score with the id `{trace id}:schema_valid`; corrections through an annotation queue | the job metrics; profile rasterisation |
| Classification or routing | one `chat`; the label as bounded metadata | feature, user | input off | WARNING for a label outside the set (`other`); a categorical score | on request `app_genai_classifications_total{feature,label}` |
| Agent with tools | `invoke_agent` → `chat` per turn → `execute_tool {tool}`, whose own calls stay ops-only; a guardrail before risky tools | feature, user, conversation | tool arguments and results are content | a failed tool is ERROR, and the agent WARNING when it recovers; the step limit reached is WARNING; a task-success score | on request: tool-call and agent-step counters |
| Several agents | a nested `invoke_agent` per agent; across services, W3C context and each service stamps its own feature context | the same conversation id everywhere | as above | per agent | the collector gateway when Langfuse must show the upstream services too ([collector](../collector/README.md#genai-routing)) |
| Batch AI job | a `job {name}` root (ops) → one AI trace per item, linked to the run; provider batch results as generations with their usage | the run id as the session, job metadata, a `batch` tag | off; `sample_ratio` for spot checks | a failed item is ERROR and the run `partial`; offline evaluation with Langfuse datasets | the job metrics and job log |
| Embedding and indexing | one `embeddings` per batch call; vectors never captured | index name and version as metadata | off | ERROR on a failed batch | scope `ai_only`, or Langfuse off; embedding tokens; profile chunking |
| Guardrail | `guardrail {name}` with the result (`pass`, `block`, `flag`) as metadata | inherited | the flagged text is content | a block is WARNING; a `guardrail.blocked` log event | on request a decision counter |
| In-app evaluation | `evaluate {name}`, a score with a stable id, and a `gen_ai.evaluation.result` log event | inherited | the explanation is content | only when the result changes what the app does; otherwise evaluate in Langfuse | — |
| Offline evaluation (experiment) | a run over a Langfuse dataset: one root per item, `experiment {feature}`, then the feature's own steps | the feature; experiment and item keys on every span | runs only where the mode is `full` (curated data) | evaluator scores on each item's root; Langfuse judges | [below](#experiments-offline-evaluation); off the ops path |

## Human-in-the-loop approval

Langfuse has no runtime approval feature, and a span left open while a person decides would
count the wait as latency. The approval is three traces joined by ids:

1. **Trace A, the proposal.** The agent proposes a risky action and records:
   - an event observation `approval requested`, with the approval id and the tool name as
     metadata, never the arguments;
   - `app.genai.agent.outcome=awaiting_approval` on the agent span, status UNSET;
   - an INFO `genai.approval.requested` log event with the approval id.

   The service stores the approval id with the conversation, the feature, trace A's id, the
   event's span id and the tool call id. No span stays open across the wait.
2. **Trace B, the decision request.** The reviewer's request records, with stable ids such as
   `{approval id}:decision`:
   - scores on trace A and on its event: `approval_decision` (categorical: `approved`,
     `rejected`, `edited`, `expired`) and `approval_wait_seconds` (numeric);
   - the edited arguments, as a text score, only when the feature's content mode allows it;
   - the reviewer's id in the score's metadata;
   - an INFO `genai.approval.decided` log event, and on request
     `app_genai_approvals_total{feature,decision}` and `app_genai_approval_wait_seconds`.
3. **Trace C, the resumed run,** is a new trace with an OpenTelemetry link to trace A (for the
   ops backend), the metadata `resumed_from=<trace A id>` and the tag `resumed` (Langfuse
   ignores links), and the same conversation id, so the Langfuse session shows A, then C.
4. **Expiry:** a job closes approvals past their deadline with a WARN `approval expired` record
   and the `expired` decision score.

Annotation queues stay for reviewing after the fact, not for approving at run time.

## User feedback

1. Every AI response returns its message id. The service stores message id → trace id,
   observation id, feature and the sampled flag.
2. The browser posts feedback (thumbs, a rating, a comment) to the app's own API, which checks
   that the user owns the message and stores the feedback in the app's database first.
3. The module then scores asynchronously, from the server:
   - `user_feedback` (boolean or numeric) on the trace and the observation, with the id
     `{message id}:user_feedback`, so a changed rating replaces the old one;
   - the comment only as the feature's content mode allows;
   - an INFO `genai.feedback.received` log event, and on request
     `app_genai_feedback_total{feature,value}`.
4. Feedback on an unsampled trace is stored and counted but not scored: the score would have
   no trace. AI entry points are always sampled, so this is rare.
5. Score configs for each score name are a platform handoff
   ([langfuse.md](../backends/langfuse.md#platform-handoff)).

The browser never holds the secret key. Scoring straight from the browser with the public key
is for low-risk internal tools only; the audit reports it (`genai-browser-score`).

## Batch AI jobs

A batch job's run is its root span, `job {name}`, with the job log and metrics
([batch.md](batch.md)). AI work inside it adds:

- **One AI trace per item,** a new root linked to the run, so Langfuse shows items rather than
  one huge trace and evaluations attach to a single item.
- **The run as the session:** the run id as the conversation, so Langfuse groups the items.
- **Provider batch APIs:** each result is recorded as a generation when it is collected, with
  its usage; the submission and the polling stay ops spans.
- **Content off** in production; `content.sample_ratio` keeps content on a small share of items
  for spot checks outside production.

## Experiments (offline evaluation)

Use an experiment when a change to a prompt, a model, retrieval or a tool should be judged on the
same inputs before it ships: a CI job on such changes, a scheduled regression on a golden set, or
a one-off comparison. The keys and rules are in [signals/genai.md](../signals/genai.md#experiments)
and [backends/langfuse.md](../backends/langfuse.md#experiments).

- **The runner** is a command in the service's repository (`python -m app.evals --dataset … --name
  "prompt v4"`), set up like the service and calling the feature's own functions, so the run
  exercises the production code path. Tools read fixture data, never a live system.
- **Datasets** are small and curated (tens to a few hundred items: every span is a billed
  observation). Items made from production traces go through redaction or an annotation queue
  first.
- **Evaluators:** deterministic checks in code (exact match, schema valid, expected terms) score
  each item's root right away; LLM-as-a-judge evaluators configured in Langfuse add their
  scores. Compare runs in Langfuse's experiment view.
- **Where it runs:** CI or a non-production environment with `OBS_GENAI_CONTENT=full`, the
  model provider (or the model stub for a dry run) and Langfuse keys; the ops collector isn't
  needed, since experiment spans never go there.
- **Output:** one summary line (experiment name and id, items, failures, the evaluator means)
  and a non-zero exit when an item failed, so CI can gate on it.

## Several agents and services

- **In one process,** each agent is a nested `invoke_agent` under the request's feature
  context; handoffs between agents are event observations.
- **Across services,** the W3C context carries the trace; each service declares its own
  feature context, so each one stamps its own spans, and the conversation id travels in the
  request body or a header the services agree on, never in baggage.
- **Langfuse sees what each service sends.** Upstream services without AI work don't send
  spans to Langfuse; to show them there too, the collector gateway keeps whole traces that
  contain an AI span ([collector](../collector/README.md#genai-routing)).

## Chat interfaces and streaming

- **Streaming responses** (server-sent events, WebSocket messages): the generation records the
  time to first chunk, and usage from the stream's final chunk; the message id is sent to the
  client in the first event, so feedback works before the stream ends.
- **WebSocket chat:** each user message is a root `WS {type}` span that declares the feature
  ([api.md](api.md#websocket)).
- **Session replay** blocks the transcript and the input box (`oneuptime-block`): replay would
  otherwise record what the user and the model wrote ([rum.md](../signals/rum.md#safe-capture-in-the-browser)).
- **Browser telemetry** never records prompts or answers; the browser's fetch span to the chat
  API continues into the backend trace as usual.

## Planned

Not written yet; say so instead of inferring: realtime and voice sessions, AI gateways
(proxies in front of several providers), self-hosted model servers, and MCP servers that expose
tools.

## Verification

[signals/genai.md](../signals/genai.md#verification) describes the two runs against the model
stub and the Langfuse stand-in. For the functions on this page, also check:

- a chat turn: the feature context on every observation, the prompt link, and WARNING on a
  truncated answer (send a small `max_tokens`);
- a tool call: the tool observation with arguments and result only in the non-production run,
  and its outbound calls absent from the Langfuse path;
- a failed call and a 429 (the stub's `[[fail]]` and `[[ratelimit]]`): ERROR on the
  generation, the `outcome` label on the duration metric, one ERROR record;
- feedback sent twice for one message: one score, sent by the server;
- an approval: the event in trace A, the decision scores, trace C linked and tagged `resumed`;
- an experiment over a two-item dataset: two traces with one root each, the experiment and item
  keys on their spans and none in the requests to the model provider, input, output and expected
  output on each root, the evaluator scores on the roots, nothing on the ops path.

[Documentation index](../README.md)
