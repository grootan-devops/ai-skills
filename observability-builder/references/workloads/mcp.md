# MCP servers and clients

Model Context Protocol servers expose tools, prompts and resources to AI agents over JSON-RPC,
on stdio or Streamable HTTP. MCP clients are the agents and hosts that call them. Everything in
[API backend](api.md) still applies to a server's HTTP side: the module, the logger, the access
log of its route, and errors through logs. [AI features and agents](ai-agent.md) applies to the
AI work a tool does. This page adds what MCP needs:

- one span per message on each side, with the context carried through `params._meta`;
- status rules for JSON-RPC errors;
- the MCP metrics;
- the feature context for tools that reach a model.

The names follow OpenTelemetry's MCP semantic conventions (Development) as of 2026-10. Check
the registry before relying on one.

## What changes

| Concern | API backend | MCP server | MCP client |
| --- | --- | --- | --- |
| Unit of work | HTTP request | each request or notification it receives | each request or notification it sends |
| Span | `SERVER` per HTTP request | `SERVER` `{mcp.method.name} {target}`, parented by the context in `params._meta` and linked to the HTTP request span | `CLIENT` `{mcp.method.name} {target}`, or the agent's own `execute_tool` span |
| Context | W3C headers | read from `params._meta` | written to `params._meta` |
| Metrics | `http_server_*` | `mcp_server_operation_duration_seconds` | `mcp_client_operation_duration_seconds` |
| Logs | stdout | stderr for a stdio server, whose stdout carries JSON-RPC only | as the host's |
| Module addition | `adapters/<framework>` | `adapters/mcp` | `adapters/mcp` |

On Streamable HTTP every call reaches one route (`POST /mcp`), so the access log and the HTTP
metrics can't tell tools apart: per-tool latency and errors come from the MCP spans and metrics.

## Spans

- **Names:** `{mcp.method.name} {target}`, where the target is the tool name (`tools/call
  search_orders`) or the prompt name (`prompts/get summary`). Other methods use the method alone
  (`tools/list`, `resources/read`). A resource URI never goes in a name.
- **Kinds:** the sender's span is `CLIENT` and the receiver's `SERVER`. Requests a server sends
  to its client (`sampling/createMessage`, `elicitation/create`, `roots/list`) swap the roles.
- **Which messages:** every request and notification gets a span, except `ping`.
- **One span, not two.** When an agent's framework already makes an `execute_tool` span for the
  call, the MCP client adds its attributes to that span instead of starting a child.

| Attribute | Where | Notes |
| --- | --- | --- |
| `mcp.method.name` | every span | the protocol's method name |
| `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name` | `tools/call` | makes the span a tool step in LLM views ([genai](../signals/genai.md#step-kinds)) |
| `gen_ai.prompt.name` | `prompts/get` | |
| `mcp.resource.uri` | `resources/*` | sanitised like `url.full`; replaced by its template when it holds a user's data |
| `jsonrpc.request.id` | requests | never on notifications |
| `mcp.protocol.version`, `mcp.session.id` | every span, when known | |
| `network.transport` | every span | `pipe` for stdio, `tcp` for HTTP |
| `network.protocol.name`, `network.protocol.version` | HTTP | `http`, `1.1` or `2` |
| `server.address`, `server.port` | client | the server behind any proxy |
| `client.address`, `client.port` | server | from the trusted-proxy resolver |

**Status and errors:**

- **JSON-RPC errors:** the code goes in `rpc.response.status_code`.
- **On the client:** every JSON-RPC error is ERROR, with the code as `error.type`.
- **On the server:** the caller's errors stay UNSET, like a 4xx: -32700 parse error, -32600
  invalid request, -32601 method not found, -32602 invalid params (an unknown tool included),
  -32002 resource not found. Any other code is ERROR with `error.type`.
- **Tool errors:** a tool result with `isError: true` is ERROR with `error.type=tool_error` on
  both sides; a timeout or transport failure gets its exception type.
- **Protocol probes:** a client that first proposes a newer protocol (`server/discover`) and
  falls back to `initialize` when the server rejects it is negotiating, not failing. Record
  the rejected probe with outcome `rejected` and leave its span UNSET
  ([sdk-notes](../sdk-notes.md)).
- **Ops path:** the status description is replaced by the error type there, as on every AI span.

## Context propagation

- **The client** writes `traceparent` and `tracestate` into the `params._meta` of every request
  and notification, unprefixed, plus the allow-listed baggage. It never adds AI context, which
  stays in the process ([genai](../signals/genai.md#trace-context)).
- **The server** reads them as the parent of its span and links the transport's own context (the
  HTTP request span) when there is one. The MCP context and the HTTP request are independent: one
  Streamable HTTP request can carry several MCP messages, and a retry can repeat one.
- **A conversation id** travels under an app-prefixed `_meta` key when both ends are the app's
  own, so the server's AI spans join the agent's conversation. Never in baggage.

## Built-in tracing first

Check the SDK before adding code. The official Python SDK traces its server out of the box:
`SERVER` spans named as above, carrying:

- `mcp.method.name`, `mcp.protocol.version` and `jsonrpc.request.id`;
- `execute_tool` and `gen_ai.tool.name` on `tools/call`;
- `gen_ai.prompt.name` on `prompts/get`.

Its client also writes the context into `_meta`, and makes a CLIENT span per request named
`MCP send {method} {target}`, with only the method and the request id: the module renames it to
`{method} {target}` and adds the conventions' keys and the outcome while it is open. Where an
SDK traces MCP itself, it is the one source: the module adds the feature context and the metrics
and never wraps the same handlers or sends in a second span ([sdk-notes](../sdk-notes.md)). Otherwise the module's MCP adapter wraps the server's
message handling and the client's send, through the middleware or hooks the SDK offers.

## Tools that reach a model

- **Entry points:** a tool handler that can reach a model is an AI entry point. It is listed in
  the feature registry, so its traces are always sampled, and it declares
  `genai.feature(name, user, conversation, team)` after the server's own authentication
  ([who spent what](../signals/genai.md#who-spent-what)).
  - The user is the client's authenticated principal, such as the OAuth subject.
  - The conversation comes from `_meta` when the client sends one.
- **Nesting:** the tool's model calls nest under the server's span, so both tools show the
  call and its generations together.
- **Sampling requests:** for `sampling/createMessage`, the client runs the model for the server.
  The generation is a child of the client's `SERVER` span for that request, under the client's
  feature context.
- **Content:** tool arguments and results (`gen_ai.tool.call.arguments`,
  `gen_ai.tool.call.result`) and prompt arguments are content. They are recorded only through the
  content helper, at the content destination ([genai](../signals/genai.md#content)). Resource
  contents are never recorded.

## Metrics

Both sides record the duration where each message completes, so unsampled traffic counts too:

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `mcp_server_operation_duration_seconds` | histogram | `method`, `target`, `outcome` | each request or notification the server handles, from receipt to the result |
| `mcp_client_operation_duration_seconds` | histogram | `server`, `method`, `target`, `outcome` | each request the client sends, from sending to the response |

- **`method`:** the `mcp.method.name` value.
- **`target`:** the tool or prompt name from the server's registered list, `other` beyond it,
  and empty for methods without one. Never a resource URI or an argument.
- **`outcome`:**
  - `success`;
  - `error`: a failure on the receiving side, a tool result with `isError`, or a transport
    failure;
  - `rejected`: the caller's errors listed above;
  - `timeout`: client only.
- **`server`:** the name the client's configuration gives the server, from a fixed list.
- **Buckets:** the standard list, plus `120` and `300` for long tool calls
  ([metrics](../signals/metrics.md)).

## Ops path

A `tools/call` span carries `execute_tool`, so it is an AI span, and the ops allow-list keeps its
MCP keys ([genai](../signals/genai.md#the-ops-allow-list)). Other MCP spans keep their
attributes, except that `prompts/get` loses `gen_ai.prompt.name`: a span that isn't an AI call
carries no `gen_ai.*` key, so OneUptime doesn't count it as one. The span name keeps the prompt
name.

## Verification

Run a client against the server (the app's own client, or the SDK's example client) with the
receiver, then check the MCP line of `summary.md` and `/metrics`:

- `SERVER` and `CLIENT` spans per method, each server span parented by its client span;
- `tools/call` spans with `execute_tool` and the tool name, and one span per tool call where the
  agent's framework makes the tool span;
- the caller's errors UNSET on the server and ERROR on the client, and `isError` results as
  `tool_error`;
- the two metrics with bounded labels;
- for a stdio server, stdout carrying only JSON-RPC.

[Documentation index](../README.md)
