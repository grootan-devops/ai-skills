import base64
import json
import socket
import struct
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import llm_stub  # noqa: E402

ORDER_TOOLS = [
    {"type": "function", "function": {"name": "get_order", "parameters": {
        "type": "object", "properties": {"order_id": {"type": "string"}, "verbose": {"type": "boolean"}},
        "required": ["order_id"]}}},
    {"type": "function", "function": {"name": "cancel_order", "parameters": {
        "type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}},
]


def sse(text: str) -> list[tuple]:
    """(event name, data) for each server-sent event; data is parsed JSON, or the [DONE] marker."""
    events = []
    for frame in text.strip().split("\n\n"):
        name = data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        events.append((name, data if data == "[DONE]" else json.loads(data)))
    return events


class LlmStubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.stub = llm_stub.serve()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer sk-stub"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read().decode()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers, error.read().decode()

    def post(self, path: str, body: dict):
        status, headers, text = self.request(path, body)
        return status, headers, json.loads(text)

    def chat(self, **body):
        return self.post("/v1/chat/completions", {"model": "stub-gpt-mini", **body})

    def test_chat_answer_and_usage(self):
        status, headers, reply = self.chat(messages=[{"role": "system", "content": "Be brief."},
                                                     {"role": "user", "content": "Where is my order today please"}])
        self.assertEqual(status, 200)
        self.assertTrue(headers["x-request-id"].startswith("req_stub_"))
        self.assertEqual(reply["object"], "chat.completion")
        self.assertEqual(reply["model"], "stub-gpt-mini")
        choice = reply["choices"][0]
        self.assertEqual((choice["message"]["content"], choice["finish_reason"]),
                         ("Stub answer: Where is my order today please", "stop"))
        self.assertEqual({k: reply["usage"][k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
                         {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16})

    def test_tool_round_trip(self):
        ask = {"role": "user", "content": "[[tool:get_order]] where is my order"}
        _, _, reply = self.chat(messages=[ask], tools=ORDER_TOOLS)
        call = reply["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual((call["function"]["name"], json.loads(call["function"]["arguments"])), ("get_order", {"order_id": "stub"}))
        self.assertEqual(reply["choices"][0]["finish_reason"], "tool_calls")
        _, _, reply = self.chat(messages=[{"role": "user", "content": '[[tool:cancel_order]] [[args:{"order_id": "o-1"}]]'}],
                                tools=ORDER_TOOLS)
        call = reply["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual((call["function"]["name"], json.loads(call["function"]["arguments"])), ("cancel_order", {"order_id": "o-1"}))
        result = {"role": "tool", "tool_call_id": call["id"], "content": "status shipped yesterday"}
        _, _, reply = self.chat(messages=[ask, reply["choices"][0]["message"], result], tools=ORDER_TOOLS)
        self.assertEqual((reply["choices"][0]["message"]["content"], reply["choices"][0]["finish_reason"]),
                         ("Here is what I found: status shipped yesterday", "stop"))
        for body in ({"messages": [{"role": "user", "content": "[[no_tool]] hello"}], "tools": ORDER_TOOLS},
                     {"messages": [{"role": "user", "content": "hello"}], "tools": ORDER_TOOLS, "tool_choice": "none"}):
            _, _, reply = self.chat(**body)
            self.assertEqual(reply["choices"][0]["message"]["content"], "Stub answer: hello")

    def test_stream_with_usage(self):
        messages = [{"role": "user", "content": "stream this answer please"}]
        _, _, whole = self.chat(messages=messages)
        status, headers, text = self.request("/v1/chat/completions", {
            "model": "stub-gpt-mini", "messages": messages, "stream": True, "stream_options": {"include_usage": True}})
        self.assertEqual((status, headers["Content-Type"]), (200, "text/event-stream"))
        events = sse(text)
        self.assertEqual(events[-1], (None, "[DONE]"))
        chunks = [data for _, data in events[:-1]]
        self.assertEqual(chunks[0]["choices"][0]["delta"]["role"], "assistant")
        content = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
        self.assertEqual(content, whole["choices"][0]["message"]["content"])
        self.assertEqual(chunks[-2]["choices"][0]["finish_reason"], "stop")
        self.assertEqual((chunks[-1]["choices"], chunks[-1]["usage"]["completion_tokens"]),
                         ([], whole["usage"]["completion_tokens"]))
        _, _, text = self.request("/v1/chat/completions", {"model": "m", "messages": messages, "stream": True})
        self.assertTrue(all("usage" not in data for _, data in sse(text)[:-1]))

    def test_stream_tool_call(self):
        _, _, text = self.request("/v1/chat/completions", {
            "model": "m", "stream": True, "tools": ORDER_TOOLS, "messages": [{"role": "user", "content": "check"}]})
        chunks = [data for _, data in sse(text)[:-1]]
        call = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual((call["index"], call["function"]["name"]), (0, "get_order"))
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_json_output(self):
        schema = {"type": "object", "properties": {
            "total": {"type": "number"}, "items": {"type": "array", "items": {"type": "string"}},
            "currency": {"enum": ["EUR", "USD"]}, "note": {"type": ["null", "string"]}},
            "required": ["total", "items", "currency", "note"]}
        _, _, reply = self.chat(messages=[{"role": "user", "content": "extract"}],
                                response_format={"type": "json_schema", "json_schema": {"name": "invoice", "schema": schema}})
        self.assertEqual(json.loads(reply["choices"][0]["message"]["content"]),
                         {"total": 1.0, "items": ["stub"], "currency": "EUR", "note": "stub"})
        _, _, reply = self.chat(messages=[{"role": "user", "content": "extract"}], response_format={"type": "json_object"})
        self.assertEqual(json.loads(reply["choices"][0]["message"]["content"]), {"answer": "Stub answer: extract"})

    def test_truncation(self):
        _, _, reply = self.chat(messages=[{"role": "user", "content": "a long question with many words"}], max_tokens=3)
        self.assertEqual((reply["choices"][0]["message"]["content"], reply["choices"][0]["finish_reason"]),
                         ("Stub answer: a", "length"))
        _, _, reply = self.post("/v1/messages", {"model": "stub-claude", "max_tokens": 2,
                                                 "messages": [{"role": "user", "content": "a long question"}]})
        self.assertEqual((reply["content"][0]["text"], reply["stop_reason"]), ("Stub answer:", "max_tokens"))

    def test_usage_details(self):
        question = "[[cache]] [[reasoning]] one two three four five six seven"
        _, _, reply = self.chat(messages=[{"role": "user", "content": question}])
        usage = reply["usage"]
        self.assertEqual(usage["prompt_tokens"], 9)
        self.assertEqual(usage["prompt_tokens_details"]["cached_tokens"], 4)
        self.assertEqual(usage["completion_tokens_details"]["reasoning_tokens"], 5)
        self.assertEqual(usage["completion_tokens"], len(reply["choices"][0]["message"]["content"].split()) + 5)
        _, _, reply = self.post("/v1/messages", {"model": "stub-claude", "max_tokens": 100, "system": "Be brief.",
                                                 "messages": [{"role": "user", "content": "[[cache]] [[cache_write]] " + "w " * 10}]})
        usage = reply["usage"]
        self.assertEqual((usage["cache_read_input_tokens"], usage["cache_creation_input_tokens"]), (7, 3))
        self.assertEqual(usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"], 14)

    def test_embeddings(self):
        _, _, reply = self.post("/v1/embeddings", {"model": "stub-embed", "input": ["first text", "second one here"]})
        self.assertEqual([len(item["embedding"]) for item in reply["data"]], [8, 8])
        self.assertEqual(reply["usage"], {"prompt_tokens": 5, "total_tokens": 5})
        _, _, again = self.post("/v1/embeddings", {"model": "stub-embed", "input": "first text", "dimensions": 4})
        self.assertEqual(again["data"][0]["embedding"], reply["data"][0]["embedding"][:4])
        _, _, packed = self.post("/v1/embeddings", {"model": "stub-embed", "input": "first text", "encoding_format": "base64"})
        raw = base64.b64decode(packed["data"][0]["embedding"])
        self.assertEqual([round(v, 4) for v in struct.unpack("<8f", raw)], reply["data"][0]["embedding"])

    def test_anthropic_messages(self):
        _, headers, reply = self.post("/v1/messages", {"model": "stub-claude", "max_tokens": 100,
                                                       "messages": [{"role": "user", "content": [{"type": "text", "text": "hi there"}]}]})
        self.assertTrue(headers["request-id"].startswith("req_stub_"))
        self.assertEqual((reply["type"], reply["content"], reply["stop_reason"]),
                         ("message", [{"type": "text", "text": "Stub answer: hi there"}], "end_turn"))
        tools = [{"name": "get_order", "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}}}]
        ask = {"role": "user", "content": "where is it"}
        _, _, reply = self.post("/v1/messages", {"model": "stub-claude", "max_tokens": 100, "tools": tools, "messages": [ask]})
        self.assertEqual((reply["content"][0]["type"], reply["content"][0]["input"], reply["stop_reason"]),
                         ("tool_use", {"order_id": "stub"}, "tool_use"))
        result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": reply["content"][0]["id"], "content": "shipped"}]}
        _, _, final = self.post("/v1/messages", {"model": "stub-claude", "max_tokens": 100, "tools": tools,
                                                 "messages": [ask, {"role": "assistant", "content": reply["content"]}, result]})
        self.assertEqual(final["content"][0]["text"], "Here is what I found: shipped")

    def test_anthropic_stream(self):
        _, _, text = self.request("/v1/messages", {"model": "stub-claude", "max_tokens": 100, "stream": True,
                                                   "messages": [{"role": "user", "content": "say three words"}]})
        events = sse(text)
        names = [name for name, _ in events]
        self.assertEqual((names[:2], names[-3:]), (["message_start", "content_block_start"],
                                                   ["content_block_stop", "message_delta", "message_stop"]))
        deltas = "".join(data["delta"]["text"] for name, data in events if name == "content_block_delta")
        self.assertEqual(deltas, "Stub answer: say three words")
        self.assertEqual(events[-2][1]["usage"]["output_tokens"], 5)
        _, _, text = self.request("/v1/messages", {"model": "stub-claude", "max_tokens": 100, "stream": True,
                                                   "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
                                                   "messages": [{"role": "user", "content": "find"}]})
        kinds = [data["delta"]["type"] for name, data in sse(text) if name == "content_block_delta"]
        self.assertEqual(kinds, ["input_json_delta"])

    def test_error_triggers(self):
        status, _, reply = self.chat(messages=[{"role": "user", "content": "[[fail]] please"}])
        self.assertEqual((status, reply["error"]["type"]), (500, "server_error"))
        self.assertIn("Input began: '[[fail]] please'", reply["error"]["message"])  # like providers that echo input
        status, headers, reply = self.chat(messages=[{"role": "user", "content": "[[ratelimit]]"}])
        self.assertEqual((status, headers["retry-after"], reply["error"]["type"]), (429, "1", "rate_limit_error"))
        status, _, reply = self.post("/v1/messages", {"model": "c", "max_tokens": 5, "messages": [{"role": "user", "content": "[[fail]]"}]})
        self.assertEqual((status, reply["type"], reply["error"]["type"]), (500, "error", "api_error"))
        status, _, reply = self.post("/v1/embeddings", {"model": "e", "input": "[[ratelimit]] " + "x" * 100})
        self.assertEqual(status, 429)
        self.assertIn("Input began: '[[ratelimit]] xxx", reply["error"]["message"])
        self.assertNotIn("x" * 60, reply["error"]["message"])
        flaky = [{"role": "user", "content": f"[[flaky]] {time.time()}"}]
        self.assertEqual([self.chat(messages=flaky)[0] for _ in range(3)], [500, 200, 200])
        started = time.monotonic()
        self.assertEqual(self.chat(messages=[{"role": "user", "content": "[[slow:200]] wait"}])[0], 200)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)

    def test_request_summaries_hold_no_content(self):
        self.request("/stub/reset", {})
        request = urllib.request.Request(self.base + "/v1/chat/completions", method="POST", data=json.dumps({
            "model": "stub-gpt-mini", "messages": [{"role": "user", "content": "CANARY-PROMPT-31 [[reasoning]]"}],
            "tools": ORDER_TOOLS}).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer sk-stub",
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            "baggage": "user.id=CANARY-BAGGAGE-33, session.id=s-1"})
        urllib.request.urlopen(request, timeout=10).read()
        self.post("/v1/embeddings", {"model": "stub-embed", "input": "CANARY-EMBED-32"})
        _, _, text = self.request("/stub/requests")
        self.assertNotIn("CANARY", text)
        requests = json.loads(text)["requests"]
        self.assertEqual([(r["path"], r["status"], r["model"], r["auth"]) for r in requests],
                         [("/v1/chat/completions", 200, "stub-gpt-mini", True), ("/v1/embeddings", 200, "stub-embed", True)])
        self.assertEqual((requests[0]["triggers"], requests[0]["tools"], requests[0]["finish"]), (["reasoning"], 2, "tool_calls"))
        self.assertEqual((requests[0]["traceparent"], requests[0]["baggage_keys"]), (True, ["session.id", "user.id"]))
        self.assertEqual((requests[1]["traceparent"], requests[1]["baggage_keys"]), (False, []))
        _, _, models = self.request("/v1/models")
        self.assertLessEqual({"stub-gpt-mini", "stub-embed"}, {m["id"] for m in json.loads(models)["data"]})

    def test_unknown_route_and_bad_body(self):
        self.assertEqual(self.request("/v1/completions", {})[0], 404)
        request = urllib.request.Request(self.base + "/v1/chat/completions", data=b"{nope", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 400)


class CliTests(unittest.TestCase):
    def test_prints_urls_and_stops(self):
        done = subprocess.run([sys.executable, str(SCRIPTS / "llm_stub.py"), "--seconds", "0.5"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        urls = json.loads(done.stdout.splitlines()[0])
        self.assertEqual(urls["openai"], urls["base_url"] + "/v1")
        self.assertEqual(urls["anthropic"], urls["base_url"])

    def test_busy_port_exits_3(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            done = subprocess.run([sys.executable, str(SCRIPTS / "llm_stub.py"), "--port", str(port), "--seconds", "0.5"],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 3)


if __name__ == "__main__":
    unittest.main()
