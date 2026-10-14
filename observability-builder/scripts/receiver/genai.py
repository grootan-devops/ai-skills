"""LLM observability in captured telemetry: content keys, Langfuse auth facts, scores and prompts.

The content-key families are the standard's list (references/signals/genai.md): attributes that
carry prompts, completions, tool arguments and results, retrieved documents or other model input
and output. Everything here reports keys, counts and sizes, never the values.
"""
from __future__ import annotations

import base64
import binascii
import re

LANGFUSE_OTEL = "/api/public/otel"
LANGFUSE_TRACES = LANGFUSE_OTEL + "/v1/traces"
LANGFUSE_SCORES = "/api/public/scores"
LANGFUSE_INGESTION = "/api/public/ingestion"
LANGFUSE_MEDIA = "/api/public/media"
LANGFUSE_PROMPTS = "/api/public/v2/prompts/"
LANGFUSE_DATASETS = "/api/public/v2/datasets/"
LANGFUSE_DATASET_ITEMS = "/api/public/dataset-items"
LANGFUSE_PROJECTS = "/api/public/projects"
LANGFUSE_HEALTH = "/api/public/health"
INGESTION_HEADER = "x-langfuse-ingestion-version"

CONTENT_EXACT = frozenset({
    # OpenTelemetry GenAI conventions
    "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions", "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments", "gen_ai.tool.call.result", "gen_ai.retrieval.documents",
    "gen_ai.retrieval.query.text", "gen_ai.memory.query.text", "gen_ai.memory.records",
    "gen_ai.evaluation.explanation", "gen_ai.prompt", "gen_ai.completion",
    # Langfuse
    "langfuse.observation.input", "langfuse.observation.output", "langfuse.trace.input", "langfuse.trace.output",
    "langfuse.experiment.item.expected_output",
    # OpenLLMetry
    "traceloop.entity.input", "traceloop.entity.output",
    # OpenInference
    "input.value", "output.value", "llm.prompt_template.variables",
    # Vercel AI SDK
    "ai.response.text", "ai.response.object", "ai.response.toolCalls", "ai.toolCall.args", "ai.toolCall.result",
})
CONTENT_PATTERN = re.compile(
    r"^(gen_ai\.prompt\.\d+\.|gen_ai\.completion\.|gen_ai\.prompt\.variable|llm\.(input|output)_messages\.|"
    r"llm\.prompts\.|llm\.tools\.|llm\.prompt_template\.template|retrieval\.documents\.|reranker\.|"
    r"embedding\.embeddings\.|ai\.prompt)"
)
# What Langfuse infers from gen_ai.operation.name (seen with Langfuse 4.55); `retrieval` and
# `invoke_workflow` stay plain spans, so the module sets their type explicitly.
TYPE_FROM_OPERATION = {
    "chat": "generation", "completion": "generation", "text_completion": "generation",
    "generate_content": "generation", "generate": "generation", "embeddings": "embedding",
    "invoke_agent": "agent", "create_agent": "agent", "execute_tool": "tool",
}
TRACE_CONTEXT = {
    "trace name": ("langfuse.trace.name",),
    "user": ("langfuse.user.id", "user.id"),
    "session": ("langfuse.session.id", "session.id", "gen_ai.conversation.id"),
    "release": ("langfuse.release",),
}
SCORE_TYPES = {"NUMERIC", "BOOLEAN", "CATEGORICAL"}
# A span the ops guard treats as an AI call (references/signals/genai.md, "The ops allow-list"); any other span that
# carries a key with an AI prefix counts as an extra AI call in OneUptime.
AI_MARKERS = ("gen_ai.operation.name", "langfuse.observation.type", "openinference.span.kind", "llm.request.type",
              "traceloop.span.kind")
AI_KEY_PREFIXES = ("gen_ai.", "llm.", "traceloop.")
# The GenAI conventions' own content keys: with the ops content destination, the only content AI spans keep there.
CONVENTION_CONTENT = frozenset({
    "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions", "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments", "gen_ai.tool.call.result", "gen_ai.retrieval.documents",
    "gen_ai.retrieval.query.text", "gen_ai.memory.query.text", "gen_ai.memory.records",
})
DESTINATIONS = ("langfuse", "ops")
IDENTITY = ("user.id", "team.id", "gen_ai.conversation.id")
# JSON-RPC errors a caller causes: an MCP server leaves them UNSET (references/workloads/mcp.md).
MCP_CALLER_ERRORS = frozenset({"-32700", "-32600", "-32601", "-32602", "-32002"})


def is_content_key(key: str) -> bool:
    return key in CONTENT_EXACT or CONTENT_PATTERN.match(key) is not None


def is_ai_span(attrs: dict, scope: str = "") -> bool:
    """A span describing AI work: a GenAI operation, a Langfuse observation type, an OpenInference span kind,
    or GenAI-only keys."""
    return ("gen_ai.operation.name" in attrs or "langfuse.observation.type" in attrs
            or "openinference.span.kind" in attrs or any(key.startswith("gen_ai.") for key in attrs)
            or scope.startswith("langfuse"))


def is_ai_call(attrs: dict) -> bool:
    """A span the ops guard treats as an AI call: the markers only, never a stray `gen_ai.*` key."""
    return any(key in attrs for key in AI_MARKERS)


def observation_type(attrs: dict) -> str:
    """The type Langfuse would give the span (its explicit type first, then the GenAI operation)."""
    explicit = str(attrs.get("langfuse.observation.type") or "")
    if explicit:
        return explicit
    operation = str(attrs.get("gen_ai.operation.name") or "")
    if operation in TYPE_FROM_OPERATION:
        return TYPE_FROM_OPERATION[operation]
    if "gen_ai.tool.name" in attrs or "gen_ai.tool.call.id" in attrs:
        return "tool"
    if any(k in attrs for k in ("gen_ai.request.model", "gen_ai.response.model", "langfuse.observation.model.name")):
        return "generation"
    return "span"


def value_chars(value) -> int:
    return len(value) if isinstance(value, str) else len(str(value))


def auth_facts(headers, expected_basic: str | None) -> dict:
    """What the Authorization and ingestion headers looked like, as booleans; never their values."""
    auth = headers.get("Authorization") or ""
    scheme, _, credential = auth.partition(" ")
    facts = {"scheme": scheme.lower() or "none", "key_shape": False, "ok": None,
             "ingestion_v4": (headers.get(INGESTION_HEADER) or "").strip() == "4",
             "sdk": bool((headers.get("x-langfuse-sdk-name") or "").strip())}
    if scheme.lower() == "basic":
        try:
            public, _, secret = base64.b64decode(credential.strip(), validate=True).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            public = secret = ""
        facts["key_shape"] = public.startswith("pk-lf-") and secret.startswith("sk-lf-")
        if expected_basic:
            facts["ok"] = credential.strip() == expected_basic
    elif scheme.lower() == "bearer":
        facts["key_shape"] = credential.strip().startswith("pk-lf-")
    return facts


def score_record(body: dict, auth: dict, seq: int, received_at: float) -> dict:
    """A score as recorded: name, type, targets and id; the value only for numeric, boolean and categorical."""
    data_type = str(body.get("dataType") or "NUMERIC").upper()
    comment = body.get("comment")
    return {
        "seq": seq, "name": str(body.get("name") or ""), "data_type": data_type,
        "id": str(body.get("id") or ""),
        "trace_id": str(body.get("traceId") or ""), "observation_id": str(body.get("observationId") or ""),
        "session_id_present": bool(body.get("sessionId")),
        "value": body.get("value") if data_type in SCORE_TYPES else None,
        "comment_chars": value_chars(comment) if comment else 0,
        "metadata_keys": sorted((body.get("metadata") or {}).keys()) if isinstance(body.get("metadata"), dict) else [],
        "config_id_present": bool(body.get("configId")), "environment": str(body.get("environment") or ""),
        "auth": auth, "received_at": received_at,
    }


PROMPT_DEFAULTS = {"type": "text", "config": {}, "labels": [], "tags": [], "commitMessage": None}


def find_prompt(prompts: list, name: str, label: str | None, version: str | None) -> dict | None:
    """Langfuse's resolution: a version wins; otherwise the label, `production` when none is given.
    The answer carries every field the SDKs require, with Langfuse's defaults for missing ones."""
    for prompt in prompts:
        if prompt.get("name") != name:
            continue
        if version:
            if str(prompt.get("version")) == str(version):
                return {**PROMPT_DEFAULTS, **prompt}
        elif (label or "production") in (prompt.get("labels") or []):
            return {**PROMPT_DEFAULTS, **prompt}
    return None


STAND_IN_TIME = "2026-01-01T00:00:00.000Z"


def find_dataset(datasets: list, name: str) -> dict | None:
    return next((dataset for dataset in datasets if dataset.get("name") == name), None)


def dataset_record(dataset: dict) -> dict:
    """A dataset as Langfuse returns it, with every field the SDKs require."""
    return {"id": dataset.get("id") or f"stand-in-{dataset['name']}", "name": dataset["name"],
            "description": dataset.get("description"), "metadata": dataset.get("metadata") or {},
            "inputSchema": None, "expectedOutputSchema": None, "projectId": "stand-in-project",
            "createdAt": STAND_IN_TIME, "updatedAt": STAND_IN_TIME}


def dataset_items_page(dataset: dict, page: int, limit: int) -> dict:
    """One page of a dataset's items in Langfuse's paginated form; an item without an id gets a stable one."""
    record = dataset_record(dataset)
    rows = [{"id": item.get("id") or f"{record['id']}-item-{n}", "status": item.get("status") or "ACTIVE",
             "input": item.get("input"), "expectedOutput": item.get("expectedOutput"), "metadata": item.get("metadata"),
             "sourceTraceId": None, "sourceObservationId": None, "datasetId": record["id"], "datasetName": record["name"],
             "createdAt": STAND_IN_TIME, "updatedAt": STAND_IN_TIME, "mediaReferences": []}
            for n, item in enumerate(dataset.get("items") or [], 1)]
    page, limit = max(1, page), max(1, limit)
    return {"data": rows[(page - 1) * limit:page * limit],
            "meta": {"page": page, "limit": limit, "totalItems": len(rows), "totalPages": max(1, -(-len(rows) // limit))}}


def experiment_lines(langfuse_spans: list, ops_spans: list) -> list[str]:
    """Each experiment seen on the Langfuse path: items, traces and the item roots' shape; its traces on the ops path."""
    runs: dict[str, list] = {}
    for span in langfuse_spans:
        if span.attrs.get("langfuse.experiment.id"):
            runs.setdefault(str(span.attrs["langfuse.experiment.id"]), []).append(span)
    if not runs:
        return []
    lines, traces_seen = ["Experiments:", ""], set()
    for _, spans in sorted(runs.items()):
        traces = {s.trace_id for s in spans}
        traces_seen |= traces
        roots = [s for s in spans if s.attrs.get("langfuse.experiment.item.root_observation_id") == s.span_id]
        items = {str(s.attrs["langfuse.experiment.item.id"]) for s in spans if s.attrs.get("langfuse.experiment.item.id")}
        name = next((str(s.attrs["langfuse.experiment.name"]) for s in spans if s.attrs.get("langfuse.experiment.name")), "?")
        environments = sorted({str(s.attrs.get("langfuse.environment") or "") for s in spans} - {""}) or ["unset"]
        lines.append(f"- {name}: {len(items)} items in {len(traces)} traces; item roots {len(roots)} (without a parent "
                     f"{sum(1 for s in roots if not s.parent_id)}, with input and output "
                     f"{sum(1 for s in roots if s.attrs.get('langfuse.observation.input') and s.attrs.get('langfuse.observation.output'))}"
                     f", with expected output {sum(1 for s in roots if s.attrs.get('langfuse.experiment.item.expected_output'))}); "
                     f"spans with the item keys {sum(1 for s in spans if s.attrs.get('langfuse.experiment.item.root_observation_id'))}"
                     f"/{len(spans)}; environment {', '.join(environments)}")
    lines += ["", f"Experiment spans in the ops stream: {sum(1 for s in ops_spans if s.trace_id in traces_seen)}", ""]
    return lines


def two_senders(langfuse_spans: list, langfuse_payloads: list) -> list[str]:
    """Services whose spans reached Langfuse both from a Langfuse SDK and from plain OTLP: one span sender each."""
    sdk_by_file = {p.name: bool(p.auth.get("sdk")) for p in langfuse_payloads}
    senders: dict[str, set[str]] = {}
    for span in langfuse_spans:
        if span.file in sdk_by_file:
            senders.setdefault(str(span.resource.get("service.name") or "-"), set()).add(
                "sdk" if sdk_by_file[span.file] else "otlp")
    both = sorted(name for name, kinds in senders.items() if len(kinds) > 1)
    return ([f"TWO LANGFUSE SPAN SENDERS: {', '.join(both)} sent spans from a Langfuse SDK and from plain OTLP; "
             "every observation arrives twice.", ""] if both else [])


def mcp_lines(spans: list) -> list[str]:
    """MCP spans per side and method, context through `params._meta`, tool calls traced twice, caller errors."""
    mcp = [s for s in spans if "mcp.method.name" in s.attrs]
    if not mcp:
        return []
    by_id = {(s.trace_id, s.span_id): s for s in spans}

    def parent(span):
        return by_id.get((span.trace_id, span.parent_id)) if span.parent_id else None

    per: dict[str, int] = {}
    for span in mcp:
        key = f"{span.kind} {span.attrs['mcp.method.name']}"
        per[key] = per.get(key, 0) + 1
    calls = [s for s in mcp if s.attrs["mcp.method.name"] == "tools/call"]
    tool_steps = sum(1 for s in calls if s.attrs.get("gen_ai.operation.name") == "execute_tool" and s.attrs.get("gen_ai.tool.name"))
    servers = [s for s in mcp if s.kind == "SERVER"]
    # The caller's MCP span: a CLIENT span, or the agent's execute_tool span carrying the MCP attributes.
    remote = sum(1 for s in servers if (up := parent(s)) and up.kind != "SERVER" and "mcp.method.name" in up.attrs)
    twice = sum(1 for s in calls if s.kind == "CLIENT" and (up := parent(s))
                and up.attrs.get("gen_ai.operation.name") == "execute_tool" and "mcp.method.name" not in up.attrs)
    caller = [s for s in servers if str(s.attrs.get("rpc.response.status_code", "")) in MCP_CALLER_ERRORS]
    lines = ["MCP spans: " + ", ".join(f"{k} {v}" for k, v in sorted(per.items())), "",
             f"MCP tool calls: {len(calls)}, with execute_tool and the tool name {tool_steps}; server spans parented by "
             f"the caller's MCP span {remote}/{len(servers)}; caller errors on the server {len(caller)}, "
             f"of which ERROR {sum(1 for s in caller if s.status == 'ERROR')}", ""]
    if twice:
        lines += [f"MCP TOOL CALLS TRACED TWICE: {twice} tools/call client spans under an execute_tool span; add the MCP "
                  "attributes to that span instead.", ""]
    return lines


def summary(langfuse_spans: list, ops_spans: list, scores: list, fetches: list, media: list,
            langfuse_payloads: list, dataset_fetches: tuple = (), destination: str = "langfuse") -> list[str]:
    """The GenAI section of summary.md: per destination, keys and counts only. `destination` is the service's
    genai.content.destination, which decides where content may appear."""
    if not (langfuse_spans or scores or fetches or media or dataset_fetches
            or any(is_ai_span(s.attrs, s.scope) or "mcp.method.name" in s.attrs for s in ops_spans)):
        return []
    lines = ["## LLM observability", ""]
    if langfuse_payloads:
        auth = [p.auth for p in langfuse_payloads]
        basic = sum(1 for a in auth if a.get("scheme") == "basic")
        shape = sum(1 for a in auth if a.get("key_shape"))
        ok = [a.get("ok") for a in auth if a.get("ok") is not None]
        v4 = sum(1 for a in auth if a.get("ingestion_v4"))
        sdk = sum(1 for a in auth if a.get("sdk"))
        lines += [f"Langfuse trace exports: {len(auth)}; Basic auth {basic}/{len(auth)}, key shape {shape}/{len(auth)}"
                  + (f", expected keys {sum(ok)}/{len(ok)}" if ok else "")
                  + f", {INGESTION_HEADER}: 4 on {v4}/{len(auth)}, from a Langfuse SDK {sdk}/{len(auth)}", ""]
        lines += two_senders(langfuse_spans, langfuse_payloads)
    if langfuse_spans:
        types: dict[str, int] = {}
        for span in langfuse_spans:
            kind = observation_type(span.attrs)
            types[kind] = types.get(kind, 0) + 1
        lines += [f"Observations sent to Langfuse ({len(langfuse_spans)}): "
                  + ", ".join(f"{k} {v}" for k, v in sorted(types.items())), ""]
        coverage = []
        for label, keys in TRACE_CONTEXT.items():
            have = sum(1 for s in langfuse_spans if any(s.attrs.get(k) for k in keys))
            coverage.append(f"{label} {have}/{len(langfuse_spans)}")
        lines += ["Trace context on Langfuse-bound spans: " + ", ".join(coverage), ""]
        content: dict[str, list[int]] = {}
        for span in langfuse_spans:
            for key, value in span.attrs.items():
                if is_content_key(key):
                    content.setdefault(key, []).append(value_chars(value))
        lines += ["Content keys in Langfuse spans: " + (", ".join(
            f"{k} x{len(v)} (max {max(v)} chars)" for k, v in sorted(content.items())) or "none"), ""]
        if content and destination == "ops":
            lines += ["CONTENT IN THE LANGFUSE STREAM: the content destination is ops, so Langfuse gets none.", ""]
        links: dict[str, int] = {}
        for span in langfuse_spans:
            name = span.attrs.get("langfuse.observation.prompt.name")
            if name:
                key = f"{name}@{span.attrs.get('langfuse.observation.prompt.version', '?')}"
                links[key] = links.get(key, 0) + 1
        lines += ["Prompt links: " + (", ".join(f"{k} x{v}" for k, v in sorted(links.items())) or "none"), ""]
    ai_ops = [s for s in ops_spans if is_ai_span(s.attrs, s.scope)]
    calls = [s for s in ops_spans if is_ai_call(s.attrs)]
    leaked: dict[str, int] = {}
    allowed: dict[str, int] = {}
    stamps: dict[str, int] = {}
    stray: dict[str, int] = {}
    for span in ops_spans:
        call = is_ai_call(span.attrs)
        for key in span.attrs:
            if is_content_key(key):
                # The ops destination lets the conventions' content keys stay on AI calls; nothing else.
                bucket = allowed if destination == "ops" and call and key in CONVENTION_CONTENT else leaked
                bucket[key] = bucket.get(key, 0) + 1
            elif key.startswith("langfuse."):
                stamps[key] = stamps.get(key, 0) + 1
            elif not call and key.startswith(AI_KEY_PREFIXES):
                stray[key] = stray.get(key, 0) + 1
        for event in span.events:
            if str(event.get("name", "")).startswith("gen_ai."):
                leaked[f"event {event['name']}"] = leaked.get(f"event {event['name']}", 0) + 1
    described = sum(1 for s in ai_ops if getattr(s, "status_message", "") not in ("", str(s.attrs.get("error.type") or "")))
    lines += [f"Ops stream: {len(ai_ops)} AI spans; content keys: "
              + (", ".join(f"{k} x{v}" for k, v in sorted(leaked.items())) or "none")
              + "; langfuse.* keys: " + (", ".join(f"{k} x{v}" for k, v in sorted(stamps.items())) or "none")
              + f"; status descriptions other than the error type: {described}", ""]
    if destination == "ops":
        lines += ["Content on AI calls in the ops stream (the ops destination): "
                  + (", ".join(f"{k} x{v}" for k, v in sorted(allowed.items())) or "none"), ""]
    if leaked:
        lines += [("CONTENT IN THE OPS STREAM: prompts or completions reached the ops destination." if destination != "ops"
                   else "CONTENT OUTSIDE AI CALLS IN THE OPS STREAM: content on other spans, in span events or in "
                        "other conventions' keys."), ""]
    if described:
        lines += ["PROVIDER MESSAGES IN THE OPS STREAM: AI spans keep status descriptions that can quote the input.", ""]
    if calls:
        lines += [f"Who spent what on the {len(calls)} AI calls in the ops stream: "
                  + ", ".join(f"{key} {sum(1 for s in calls if s.attrs.get(key))}/{len(calls)}" for key in IDENTITY), ""]
    if stray:
        lines += ["AI keys on spans that aren't AI calls: " + ", ".join(f"{k} x{v}" for k, v in sorted(stray.items())), "",
                  "AI KEYS ON OTHER SPANS: OneUptime counts each of these spans as an AI call.", ""]
    retrievals = [s for s in (ops_spans or langfuse_spans) if s.name.startswith("retrieval ")]
    if retrievals:
        typed = sum(1 for s in retrievals if s.attrs.get("gen_ai.operation.name") == "retrieval")
        lines += [f"Retrieval steps: {len(retrievals)}, with gen_ai.operation.name=retrieval {typed}", ""]
        if typed < len(retrievals):
            lines += ["RETRIEVAL STEPS WITHOUT THEIR OPERATION: LLM views list them as other calls.", ""]
    lines += mcp_lines(ops_spans)
    if scores:
        lines += ["Scores:", ""]
        for score in scores:
            targets = "+".join(t for t, present in (("trace", score["trace_id"]), ("observation", score["observation_id"]),
                                                     ("session", score["session_id_present"])) if present) or "none"
            value = "" if score["value"] is None else f" value={score['value']}"
            lines.append(f"- {score['name']} {score['data_type']}{value} on {targets} id={'yes' if score['id'] else 'no'}"
                         f" comment={score['comment_chars']} chars auth={score['auth'].get('scheme')}")
        seen, reused = set(), 0
        for score in scores:
            key = (score["id"], score["name"])
            if score["id"] and key in seen:
                reused += 1
            seen.add(key)
        lines += ["", f"Scores sent again with the same id (upserts): {reused}", ""]
    if fetches:
        lines += ["Prompt fetches: " + ", ".join(
            f"{f['name']} ({f['selector']}) {f['status']}" for f in fetches), ""]
    if dataset_fetches:
        lines += ["Dataset fetches: " + ", ".join(
            f"{f['name']} ({f['selector']}) {f['status']}" for f in dataset_fetches), ""]
    lines += experiment_lines(langfuse_spans, ops_spans)
    if media:
        lines += [f"Media uploads attempted: {len(media)}", ""]
    return lines
