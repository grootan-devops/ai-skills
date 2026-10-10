#!/usr/bin/env python3
"""A deterministic stand-in for LLM providers, so an instrumented app runs and is verified without a
real provider key (references/signals/genai.md, "Verification").

Usage: python3 llm_stub.py [--host 127.0.0.1] [--port 0] [--seconds N]

Serves, on one port:
  POST /v1/chat/completions   OpenAI-compatible chat: tools, JSON output (response_format),
                              max_tokens truncation, SSE streaming with stream_options.include_usage
  POST /v1/embeddings         OpenAI-compatible embeddings, float or base64, `dimensions` honoured
  POST /v1/messages           Anthropic Messages: tools, max_tokens truncation, SSE streaming
  GET  /v1/models             the models seen so far
  GET  /stub/requests         one summary per request, without content (baggage key names and whether a
                              traceparent arrived, never values); POST /stub/reset clears them

Point the app's provider client at it with a dummy key: the OpenAI base URL is <base>/v1, the
Anthropic base URL is <base>. Prints one JSON line with both, then serves until SIGINT, SIGTERM
or --seconds. Standard library only. Exit code 0, or 3 when the port is busy.

Answers are fixed: the start of the last user message, or of the tool result after a tool call.
Tokens are whitespace-separated words, at least one. When tools are offered and the last message
is the user's, the answer is a call to the first tool (or the one a trigger names), with
arguments built from its schema; after the tool result comes the text answer. Triggers in the
last user message change the response:

  [[fail]]          500 every time            [[ratelimit]]     429 with retry-after: 1
  [[flaky]]         500 the first time this request body is seen, then the answer
  [[slow:<ms>]]     wait before the first byte (time to first chunk when streaming)
  [[tool:<name>]]   call that tool            [[no_tool]]       answer without a tool call
  [[args:<json>]]   the tool call's arguments
  [[cache]], [[cache_write]], [[reasoning]]   report cached-input, cache-creation or reasoning tokens

Error bodies quote the start of the request's input, as some providers and AI gateways do: a service
that records provider messages verbatim (logs, span status descriptions) leaks content with them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import signal
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TRIGGER = re.compile(r"\[\[(\w+)(?::((?:(?!\]\]).)*))?\]\]")
CHUNK_DELAY = 0.01
ANSWER_WORDS = 12
ECHO_CHARS = 60  # of the input, quoted in error messages
REASONING_TOKENS = 5


def words(text: str) -> int:
    return max(1, len(text.split()))


def text_of(content) -> str:
    """The text in a message's content: a string, or the text of its parts and tool results."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(text_of(part.get("text", part.get("content", ""))) for part in content if isinstance(part, dict))
    return ""


def triggers(text: str) -> dict:
    return {name: value or "" for name, value in TRIGGER.findall(text)}


def instance(schema: dict, depth: int = 0):
    """A small value that fits a JSON schema: enums take their first value, objects their required keys."""
    if not isinstance(schema, dict):
        return "stub"
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        required = schema.get("required", list(properties))
        return {key: instance(properties.get(key, {}), depth + 1) for key in required} if depth < 4 else {}
    if kind == "array":
        return [instance(schema.get("items") or {}, depth + 1)] if depth < 4 else []
    return {"integer": 1, "number": 1.0, "boolean": True, "null": None}.get(kind, "stub")


def vector(text: str, dimensions: int) -> list[float]:
    """A deterministic unit-scale vector for text."""
    digest, values = b"", []
    while len(values) < dimensions:
        digest = hashlib.sha256(digest + text.encode()).digest()
        values += [round(byte / 127.5 - 1, 4) for byte in digest]
    return values[:dimensions]


class Stub:
    """The recorded request summaries and the counters behind ids and [[flaky]]; shared by handler threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.seen_bodies: set[str] = set()
        self.models: list[str] = []
        self.counter = 0

    def next_id(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    def record(self, summary: dict) -> None:
        with self.lock:
            self.requests.append(summary)
            model = summary.get("model")
            if model and model not in self.models:
                self.models.append(model)

    def first_time(self, body: bytes) -> bool:
        digest = hashlib.sha256(body).hexdigest()
        with self.lock:
            if digest in self.seen_bodies:
                return False
            self.seen_bodies.add(digest)
            return True


class Turn:
    """What one chat request asks for, read the same way for both APIs."""

    def __init__(self, messages: list, system: str, tools: list[dict], last_user: str, tool_result: str | None):
        self.flags = triggers(last_user)
        self.last_user = last_user
        self.prompt_tokens = words(" ".join([system] + [text_of(m.get("content")) for m in messages if isinstance(m, dict)]))
        self.tools = tools
        self.tool_result = tool_result
        clean = TRIGGER.sub("", tool_result if tool_result is not None else last_user).split()
        lead = "Here is what I found:" if tool_result is not None else "Stub answer:"
        self.text = " ".join([lead] + clean[:ANSWER_WORDS]) if clean else lead

    def tool_call(self) -> tuple[str, dict] | None:
        """The tool to call, or None: only when tools are offered and the last message is the user's."""
        if not self.tools or self.tool_result is not None or "no_tool" in self.flags:
            return None
        wanted = self.flags.get("tool")
        tool = next((t for t in self.tools if t["name"] == wanted), self.tools[0]) if wanted else self.tools[0]
        try:
            arguments = json.loads(self.flags["args"]) if "args" in self.flags else instance(tool["schema"])
        except ValueError:
            arguments = instance(tool["schema"])
        return tool["name"], arguments

    def usage(self, completion_text: str) -> dict:
        prompt = self.prompt_tokens
        cached = prompt // 2 if "cache" in self.flags else 0
        written = prompt // 4 if "cache_write" in self.flags else 0
        reasoning = REASONING_TOKENS if "reasoning" in self.flags else 0
        return {"prompt": prompt, "completion": words(completion_text) + reasoning, "cached": cached,
                "cache_write": written, "reasoning": reasoning}


def truncated(text: str, limit) -> tuple[str, bool]:
    """The answer cut to max_tokens words, and whether it was cut."""
    if isinstance(limit, int) and limit > 0 and len(text.split()) > limit:
        return " ".join(text.split()[:limit]), True
    return text, False


class Handler(BaseHTTPRequestHandler):
    server_version = "llm-stub/1"
    stub: Stub

    def log_message(self, format, *args):  # noqa: A002 - the base class's signature
        pass

    # ---- plumbing

    def _json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, events, headers: dict) -> None:
        """Server-sent events, flushed one by one; the connection closes at the end (no length)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        for name, data in events:
            frame = (f"event: {name}\n" if name else "") + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
            self.wfile.write(frame.encode())
            self.wfile.flush()
            time.sleep(CHUNK_DELAY)
        self.close_connection = True

    def _summary(self, body: dict, status: int, flags: dict, **extra) -> None:
        baggage = self.headers.get("baggage") or ""
        self.stub.record({
            "path": self.path.split("?")[0], "status": status, "model": body.get("model"),
            "stream": bool(body.get("stream")), "auth": bool(self.headers.get("Authorization") or self.headers.get("x-api-key")),
            "traceparent": bool(self.headers.get("traceparent")),
            "baggage_keys": sorted({item.split("=", 1)[0].strip() for item in baggage.split(",") if "=" in item}),
            "triggers": sorted(flags), "received_at": time.time(), **extra})

    def _failure(self, api: str, flags: dict, raw: bytes, echo: str = "") -> tuple[int, dict, dict] | None:
        """The error a trigger asks for: (status, body, headers) in the API's own error shape, quoting the input."""
        if "ratelimit" in flags:
            status, kind, message = 429, "rate_limit_error", "Rate limit reached by the stub on purpose."
        elif "fail" in flags or ("flaky" in flags and self.stub.first_time(raw)):
            status, kind, message = 500, "api_error" if api == "anthropic" else "server_error", "The stub failed this request on purpose."
        else:
            return None
        if echo:
            message += f" Input began: {echo[:ECHO_CHARS]!r}."
        headers = {"retry-after": "1"} if status == 429 else {}
        if api == "anthropic":
            return status, {"type": "error", "error": {"type": kind, "message": message}}, headers
        return status, {"error": {"message": message, "type": kind, "param": None, "code": kind}}, headers

    # ---- routes

    def do_GET(self):  # noqa: N802 - the base class's naming
        path = self.path.split("?")[0]
        if path == "/stub/requests":
            with self.stub.lock:
                self._json(200, {"requests": list(self.stub.requests)})
        elif path == "/v1/models":
            with self.stub.lock:
                self._json(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "stub"} for m in self.stub.models]})
        else:
            self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if path == "/stub/reset":
            with self.stub.lock:
                self.stub.requests.clear()
                self.stub.seen_bodies.clear()
            self._json(200, {"ok": True})
            return
        routes = {"/v1/chat/completions": self._chat, "/v1/embeddings": self._embeddings, "/v1/messages": self._messages}
        if path not in routes:
            self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._json(400, {"error": {"message": "request body is not JSON", "type": "invalid_request_error"}})
            return
        routes[path](body if isinstance(body, dict) else {}, raw)

    def _chat(self, body: dict, raw: bytes) -> None:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        last = messages[-1] if messages else {}
        tools = [{"name": t["function"]["name"], "schema": t["function"].get("parameters") or {}}
                 for t in body.get("tools") or [] if isinstance(t, dict) and isinstance(t.get("function"), dict)]
        if body.get("tool_choice") == "none":
            tools = []
        last_user = next((text_of(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
        turn = Turn(messages, "", tools, last_user, text_of(last.get("content")) if last.get("role") == "tool" else None)
        if self._fail_with(body, raw, turn, "openai", tools=len(tools), messages=len(messages)):
            return
        number, model, created = self.stub.next_id(), body.get("model") or "stub-model", int(time.time())
        call = turn.tool_call()
        response_format = body.get("response_format") or {}
        text = turn.text
        if not call and response_format.get("type") == "json_schema":
            text = json.dumps(instance((response_format.get("json_schema") or {}).get("schema") or {}))
        elif not call and response_format.get("type") == "json_object":
            text = json.dumps({"answer": turn.text})
        text, cut = truncated(text, body.get("max_completion_tokens") or body.get("max_tokens"))
        usage = turn.usage(json.dumps(call[1]) if call else text)
        finish = "tool_calls" if call else "length" if cut else "stop"
        message = {"role": "assistant", "content": None if call else text, "refusal": None}
        if call:
            message["tool_calls"] = [{"id": f"call_stub_{number}", "type": "function",
                                      "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        openai_usage = {
            "prompt_tokens": usage["prompt"], "completion_tokens": usage["completion"],
            "total_tokens": usage["prompt"] + usage["completion"],
            "prompt_tokens_details": {"cached_tokens": usage["cached"], "audio_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": usage["reasoning"], "audio_tokens": 0,
                                          "accepted_prediction_tokens": 0, "rejected_prediction_tokens": 0}}
        base = {"id": f"chatcmpl-stub-{number}", "created": created, "model": model, "system_fingerprint": "fp_stub"}
        headers = {"x-request-id": f"req_stub_{number}"}
        self._summary(body, 200, turn.flags, tools=len(tools), messages=len(messages), finish=finish)
        if not body.get("stream"):
            self._json(200, {**base, "object": "chat.completion", "usage": openai_usage, "service_tier": "default",
                             "choices": [{"index": 0, "message": message, "logprobs": None, "finish_reason": finish}]}, headers)
            return

        def chunk(delta, finish_reason=None):
            return None, {**base, "object": "chat.completion.chunk",
                          "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish_reason}]}

        events = [chunk({"role": "assistant", "content": "" if not call else None, "refusal": None})]
        if call:
            events.append(chunk({"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}))
        else:
            pieces = text.split(" ")
            events += [chunk({"content": piece if i == 0 else " " + piece}) for i, piece in enumerate(pieces)]
        events.append(chunk({}, finish))
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append((None, {**base, "object": "chat.completion.chunk", "choices": [], "usage": openai_usage}))
        events.append((None, "[DONE]"))
        self._sse(events, headers)

    def _embeddings(self, body: dict, raw: bytes) -> None:
        inputs = body.get("input")
        inputs = inputs if isinstance(inputs, list) else [inputs]
        texts = [" ".join(map(str, item)) if isinstance(item, list) else str(item) for item in inputs]
        flags = triggers(" ".join(texts))
        failure = self._failure("openai", flags, raw, " ".join(texts))
        if failure:
            self._summary(body, failure[0], flags, inputs=len(texts))
            self._json(*failure)
            return
        dimensions = body.get("dimensions") if isinstance(body.get("dimensions"), int) and body["dimensions"] > 0 else 8
        data = []
        for index, text in enumerate(texts):
            values = vector(text, dimensions)
            embedding = base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode() \
                if body.get("encoding_format") == "base64" else values
            data.append({"object": "embedding", "index": index, "embedding": embedding})
        tokens = sum(words(text) for text in texts)
        self._summary(body, 200, flags, inputs=len(texts))
        self._json(200, {"object": "list", "data": data, "model": body.get("model") or "stub-embedding",
                         "usage": {"prompt_tokens": tokens, "total_tokens": tokens}})

    def _messages(self, body: dict, raw: bytes) -> None:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        last = messages[-1] if messages else {}
        tools = [{"name": t["name"], "schema": t.get("input_schema") or {}}
                 for t in body.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        if (body.get("tool_choice") or {}).get("type") == "none":
            tools = []
        blocks = last.get("content") if isinstance(last.get("content"), list) else []
        results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        system = body.get("system")
        last_user = next((text_of(m.get("content")) for m in reversed(messages)
                          if m.get("role") == "user" and not any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                                  for b in (m.get("content") if isinstance(m.get("content"), list) else []))), "")
        turn = Turn(messages, text_of(system) if system else "", tools, last_user,
                    text_of(results[0].get("content")) if results else None)
        if self._fail_with(body, raw, turn, "anthropic", tools=len(tools), messages=len(messages)):
            return
        number, model = self.stub.next_id(), body.get("model") or "stub-model"
        call = turn.tool_call()
        text, cut = truncated(turn.text, body.get("max_tokens"))
        usage = turn.usage(json.dumps(call[1]) if call else text)
        stop = "tool_use" if call else "max_tokens" if cut else "end_turn"
        content = [{"type": "tool_use", "id": f"toolu_stub_{number}", "name": call[0], "input": call[1]}] if call \
            else [{"type": "text", "text": text}]
        # Anthropic counts cached input apart from input_tokens; the instrumentation adds them up.
        anthropic_usage = {"input_tokens": usage["prompt"] - usage["cached"] - usage["cache_write"],
                           "output_tokens": usage["completion"], "cache_read_input_tokens": usage["cached"],
                           "cache_creation_input_tokens": usage["cache_write"]}
        message = {"id": f"msg_stub_{number}", "type": "message", "role": "assistant", "model": model, "content": content,
                   "stop_reason": stop, "stop_sequence": None, "usage": anthropic_usage}
        headers = {"request-id": f"req_stub_{number}"}
        self._summary(body, 200, turn.flags, tools=len(tools), messages=len(messages), finish=stop)
        if not body.get("stream"):
            self._json(200, message, headers)
            return
        start = {**message, "content": [], "stop_reason": None, "usage": {**anthropic_usage, "output_tokens": 1}}
        events = [("message_start", {"type": "message_start", "message": start})]
        if call:
            events += [("content_block_start", {"type": "content_block_start", "index": 0,
                                                "content_block": {**content[0], "input": {}}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                "delta": {"type": "input_json_delta", "partial_json": json.dumps(call[1])}})]
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": 0,
                                                   "content_block": {"type": "text", "text": ""}}))
            events += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                "delta": {"type": "text_delta", "text": piece if i == 0 else " " + piece}})
                       for i, piece in enumerate(text.split(" "))]
        events += [("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                      "usage": {"output_tokens": usage["completion"]}}),
                   ("message_stop", {"type": "message_stop"})]
        self._sse(events, headers)

    def _fail_with(self, body: dict, raw: bytes, turn: Turn, api: str, **extra) -> bool:
        """Apply [[slow]] and the error triggers; True when an error response was sent."""
        slow = turn.flags.get("slow", "")
        if slow.isdigit():
            time.sleep(int(slow) / 1000)
        failure = self._failure(api, turn.flags, raw, turn.last_user)
        if not failure:
            return False
        self._summary(body, failure[0], turn.flags, **extra)
        self._json(*failure)
        return True


def serve(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, Stub]:
    """Start the stub on a thread; returns the server (call shutdown()) and its recorded state."""
    stub = Stub()
    handler = type("StubHandler", (Handler,), {"stub": stub})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, stub


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--seconds", type=float, help="stop after this many seconds")
    args = parser.parse_args(argv)
    try:
        server, _ = serve(args.host, args.port)
    except OSError as error:
        print(json.dumps({"error": f"port {args.port} is not available: {error.strerror}"}), file=sys.stderr)
        return 3
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    print(json.dumps({"base_url": base, "openai": f"{base}/v1", "anthropic": base}), flush=True)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    stop.wait(args.seconds)
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
