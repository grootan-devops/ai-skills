# A2A agents and callers

The Agent2Agent protocol (A2A) lets one agent call another agent over JSON-RPC 2.0 on HTTP. The
caller sends a message and gets back a message or a task, which it can poll, cancel or stream.
The remote agent publishes an agent card that names it. Everything in [API backend](api.md) still
applies to the remote agent's HTTP side: the module, the logger, the access log of its route, and
errors through logs. [AI features and agents](ai-agent.md) applies to the AI work either side
does. This page adds what A2A needs:

- one span per A2A request on each side, with the context in the HTTP headers;
- status rules for JSON-RPC errors and failed tasks;
- the A2A metrics;
- the feature context on the remote agent.

OpenTelemetry has no A2A conventions yet. The caller's span is an agent invocation from the GenAI
conventions, the RPC fields are the RPC conventions', and the protocol fields are custom
`app.a2a.*` names in the project's namespace ([enrichment](../enrichment.md)). The classes
(M, C, R, O, P) are in [data-model.md](../data-model.md#a2a-agent-to-agent-protocol). Check the
OpenTelemetry registry and the A2A specification before relying on a name: both move.

## What changes

| Concern | API backend | A2A remote agent | A2A caller |
| --- | --- | --- | --- |
| Unit of work | HTTP request | each A2A request it receives | each A2A request it sends |
| Span | `SERVER` per HTTP request | `SERVER` `{rpc.method}`, the entry span | `CLIENT` `invoke_agent {gen_ai.agent.name}`, or the agent framework's own `invoke_agent` span |
| Context | W3C headers | read from the HTTP headers | written to the HTTP headers |
| Metrics | `http_server_*` | `a2a_server_request_duration_seconds` | `a2a_client_request_duration_seconds` |
| Module addition | `adapters/<framework>` | `adapters/a2a` | `adapters/a2a` |

Every A2A request reaches one route (the agent's JSON-RPC URL), so the access log and the HTTP
metrics can't tell methods or tasks apart: per-method latency and errors come from the A2A spans
and metrics.

## Spans

- **Caller:** one `CLIENT` span per request, named `invoke_agent {gen_ai.agent.name}`, where the
  name is the remote agent's card name. `rpc.method` says which call it was.
- **Remote agent:** one `SERVER` span per request, named by the method (`message/send`,
  `message/stream`, `tasks/get`, `tasks/cancel`). It is the service's entry span, so the
  [request context](../data-model.md#request-and-identity-context) goes on it.
- **One span, not two.** When the agent's framework already makes an `invoke_agent` span for the
  remote call, the A2A client adds its attributes to that span instead of starting a child. A
  remote agent offered to the model as a tool is that one `invoke_agent` span, never an
  `execute_tool` span around it ([genai](../signals/genai.md#step-kinds)).
- **HTTP below it.** The HTTP client instrumentation's `POST` span is a child of the caller's
  span, as for any [call to another service](../data-model.md#operations).
- **The agent card** fetch (`GET /.well-known/agent-card.json`) is a plain HTTP client span, ops
  only. Cache the card; it is not an agent invocation.
- **Push notifications** the remote agent posts to the caller's webhook are HTTP requests: a
  `CLIENT` span on the remote agent and a `SERVER` span on the caller, joined by the headers.

| Attribute | Where | Class | Notes |
| --- | --- | --- | --- |
| `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` | caller | M | the card name; makes the span an agent step in LLM views |
| `rpc.system.name=jsonrpc`, `rpc.method` | both | M | the A2A method name |
| `server.address`, `server.port`, `url.template` | caller | M | the remote agent behind any proxy; the URL template, never a URL with ids |
| `app.a2a.task.id` | both | C (a task was created) | an id, never in a span name or a metric label |
| `app.a2a.context.id` | both | C (multi-turn) | the protocol's context id |
| `app.a2a.task.state` | both | C (a task was returned) | the state in the response, such as `completed` or `input-required` |
| `app.a2a.protocol.version` | both | R | from the agent card |
| `jsonrpc.request.id` | both | R | |
| `rpc.response.status_code`, `error.type` | both | C (failure) | the JSON-RPC error code, or the failure's type |
| `client.address` | remote agent | C (as on any entry span) | from the trusted-proxy resolver |
| message parts, artifacts, task history | — | P on the ops path; Langfuse by the content policy | recorded only through the content helper |

## Status and errors

- **JSON-RPC errors:** the code goes in `rpc.response.status_code`.
- **On the caller:** every JSON-RPC error is ERROR, with the code as `error.type`. A timeout or a
  transport failure is ERROR with its exception type. A task returned as `failed` is ERROR with
  `error.type=task_failed`: the remote agent's work failed.
- **On the remote agent:** the caller's errors stay UNSET, like a 4xx: -32700 parse error,
  -32600 invalid request, -32601 method not found, -32602 invalid params, and the A2A codes for a
  caller's mistake (-32001 task not found, -32002 task not cancelable, -32003 push notifications
  not supported, -32004 unsupported operation, -32005 content type not supported). Any other
  code, and a task that ends `failed`, is ERROR with `error.type`.
- **Not failures:** `input-required`, `auth-required` and `canceled` are states, not errors; the
  span stays UNSET. A remote agent that answers with a fallback keeps UNSET and gets
  `langfuse.observation.level=WARNING` on the Langfuse path, as any degraded step
  ([genai](../signals/genai.md#required-behaviour)).
- **Ops path:** the status description is replaced by the error type there, as on every AI span.

## Context propagation

- **The caller** sends W3C `traceparent` and `tracestate` in the HTTP headers of every request,
  plus the allow-listed baggage. It never adds AI context, which stays in the process
  ([genai](../signals/genai.md#trace-context)).
- **The remote agent** reads them as the parent of its entry span, like any HTTP server.
- **Streaming** (`message/stream`, server-sent events) stays one span on each side until the
  stream ends, fails or is closed early. Events are not spans. A resubscribe is a new request
  with its own span.
- **Polling:** each `tasks/get` is its own short span with the same `app.a2a.task.id`, so the
  task's spans can be found together.
- **A conversation id** is the A2A context id. When both ends are the app's own, the caller can
  also send its conversation id under an app-prefixed key in the message metadata. Never in
  baggage.

## Built-in tracing first

Check the A2A SDK before adding code. Some SDKs trace their client and server already, under
their own names. Where an SDK traces A2A itself, it is the one source: the module renames the
span to the shape above, adds the missing attributes and the metrics while the span is open, and
never wraps the same handlers or sends in a second span ([sdk-notes](../sdk-notes.md)).
Otherwise the module's A2A adapter wraps the server's request handler and the client's send,
through the middleware or interceptors the SDK offers.

## Remote agents that reach a model

- **Entry points:** an A2A request handler that can reach a model is an AI entry point. It is
  listed in the feature registry, so its traces are always sampled, and it declares
  `genai.feature(name, user, conversation, team)` after the server's own authentication
  ([who spent what](../signals/genai.md#who-spent-what)).
  - The user is the caller's authenticated principal, such as the calling agent's service
    account or the user it acts for.
  - The conversation is the A2A context id, scoped the way the agent keys its history.
- **Nesting:** the remote agent's model calls and tools nest under its `SERVER` span, so the
  caller's trace shows the whole call.
- **Content:** message parts, artifacts and task history are content. They are recorded only
  through the content helper, on the Langfuse path only, under `genai.content.capture`
  ([genai](../signals/genai.md#content)). Files sent as parts follow the media rule.

## Metrics

Both sides record the duration where each request completes, so unsampled traffic counts too:

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `a2a_server_request_duration_seconds` | histogram | `agent`, `method`, `outcome` | each request the remote agent handles, from receipt to the response or the end of the stream |
| `a2a_client_request_duration_seconds` | histogram | `agent`, `method`, `outcome` | each request the caller sends, from sending to the response or the end of the stream |

- **`agent`:** on the caller, the remote agent's card name from a fixed list, `other` beyond it;
  on the remote agent, its own name.
- **`method`:** the `rpc.method` value.
- **`outcome`:**
  - `success`;
  - `error`: a failure on the receiving side, a `failed` task, or a transport failure;
  - `rejected`: the caller's errors listed above, on both sides;
  - `timeout`: caller only.
- **Never labels:** task ids, context ids, URLs and message text.
- **Buckets:** the standard list, plus `120` and `300` for long tasks
  ([metrics](../signals/metrics.md)).

## Ops path

The caller's span carries `invoke_agent`, so it is an AI span, and the ops allow-list keeps its
A2A keys ([genai](../signals/genai.md#the-ops-allow-list)). The remote agent's `SERVER` span is
not an AI call: it keeps its attributes and carries no `gen_ai.*` key. Neither side carries
content or `langfuse.*` keys on the ops path. A2A calls get no profiling label: they are I/O
([data-model.md](../data-model.md#ai-metrics-logs-and-profiling)).

## Checklist

- One `CLIENT` `invoke_agent {agent}` span per request on the caller, with `rpc.method`.
- One `SERVER` `{method}` entry span per request on the remote agent, parented by the caller
  through the headers.
- Task, context and state attributes only when the response has them.
- Caller errors UNSET on the remote agent and ERROR on the caller; `failed` tasks ERROR on both.
- A stream is one span until it ends.
- The two metrics with bounded labels.
- Content only through the content helper, on the Langfuse path.

## Verification

Run the caller against the remote agent (the app's own, or the SDK's sample agent) with the
receiver, then check `summary.md` and `/metrics`:

- a `message/send`, a `message/stream`, a `tasks/get` and a `tasks/cancel`, each with a caller
  span and a remote agent span in one trace;
- a request to an unknown task: UNSET on the remote agent, ERROR on the caller, and outcome
  `rejected` on both;
- a streamed call as one span on each side, ended when the stream ends;
- the two metrics with bounded labels;
- no message content, task id or `langfuse.*` key on the ops path.

[Documentation index](../README.md)
