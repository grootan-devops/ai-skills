"""The local receiver: endpoints on free ports, payload files and the summary."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gzip
import http.client
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:  # the receiver needs grpcio, protobuf and opentelemetry-proto; plain `python3 -m unittest` may not have them
    import grpc
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc

    import receive
    from receiver.sink import Store
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False


def post(port, path, body, headers, chunked=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        if chunked:
            conn.request("POST", path, body=iter([body[:10], body[10:]]), headers=headers, encode_chunked=True)
        else:
            conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def frame(envelope: dict, events: list, encoding: str = "gzip") -> bytes:
    """One `<envelope JSON> \\n<payload>` frame as the recorder writes it."""
    data = json.dumps(events).encode()
    payload = gzip.compress(data) if encoding == "gzip" else data
    envelope = {**envelope, "payloadEncoding": encoding, "payloadBytes": len(payload)}
    return json.dumps(envelope).encode() + b" \n" + payload


def span(name, kind, sid, parent="", **attrs):
    return {"traceId": "5b8efff798038103d269b633813fc60c", "spanId": sid, "parentSpanId": parent, "name": name,
            "kind": kind, "startTimeUnixNano": str(1544712660000000000 + int(sid[-2:], 16)),
            "endTimeUnixNano": "1544712661000000000",
            "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in attrs.items()]}


def traces(*spans, service="orders"):
    return json.dumps({"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
        "scopeSpans": [{"spans": list(spans)}]}]}).encode()


@unittest.skipUnless(HAVE_DEPS, "needs grpcio, protobuf and opentelemetry-proto (run with uv, see README.md)")
class ReceiverRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "telemetry"
        with mock.patch.dict(os.environ, {"OBS_RECEIVER_REPLAY_ASSETS": "", "OBS_HARNESS_REPLAY_ASSETS": ""}):  # no recorder unless a test gives one
            self.receiver = receive.Receiver(self.out)
        self.endpoints = self.receiver.start()

    def tearDown(self):
        self.receiver.stop()
        self.tmp.cleanup()

    def port(self, key):
        return int(self.endpoints[key].rsplit(":", 1)[1])

    def test_endpoints_are_free_local_ports(self):
        for key in ("otlp_grpc", "otlp_http", "pyroscope"):
            self.assertTrue(self.endpoints[key].startswith("http://127.0.0.1:"), self.endpoints)
            self.assertNotIn(self.port(key), (4317, 4318, 4040))
        self.assertIn("Export requests: none", (self.out / "summary.md").read_text())

    def test_payloads_and_span_tree(self):
        body = traces(span("POST /api/orders/{id}", 2, "00000000000000a1", **{"http.route": "/api/orders/{id}"}),
                      span("OrderService.create", 1, "00000000000000a2", "00000000000000a1"),
                      span("pricing prices.get", 3, "00000000000000a3", "00000000000000a2",
                           **{"service.peer.name": "pricing", "url.full": "http://p/prices?api_key=REDACTED"}))
        status, _, _ = post(self.port("otlp_http"), "/v1/traces", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertTrue(self.receiver.flush())
        self.assertTrue((self.out / "otlp" / "0001-traces-http.json").is_file())
        summary = (self.out / "summary.md").read_text()
        self.assertIn("## Traces (1 traces, 3 spans)", summary)
        self.assertIn("- SERVER 'POST /api/orders/{id}' http.route=/api/orders/{id}", summary)
        self.assertIn("    - CLIENT 'pricing prices.get'", summary)
        self.assertIn("api_key=REDACTED", summary)
        self.assertNotIn("root is not a SERVER span", summary)
        self.assertFalse(self.receiver.flush(), "nothing new arrived")

    def test_flags_a_root_span_outside_a_request(self):
        post(self.port("otlp_http"), "/v1/traces", traces(span("warmUp", 1, "00000000000000b1")),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        self.assertIn("root is not a SERVER span", (self.out / "summary.md").read_text())

    def test_a_job_run_root_is_not_flagged(self):
        post(self.port("otlp_http"), "/v1/traces",
             traces(span("job price-refresh", 1, "00000000000000c1", **{"app.job.name": "price-refresh"}),
                    span("PriceService.refresh", 1, "00000000000000c2", "00000000000000c1")),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        self.assertNotIn("root is not a SERVER span", (self.out / "summary.md").read_text())

    def test_grpc_logs_show_severity_and_trace(self):
        request = logs_service_pb2.ExportLogsServiceRequest()
        record = request.resource_logs.add().scope_logs.add().log_records.add()
        record.severity_number = 17
        record.body.string_value = "request failed"
        record.trace_id = bytes.fromhex("5b8efff798038103d269b633813fc60c")
        with grpc.insecure_channel(f"127.0.0.1:{self.port('otlp_grpc')}") as channel:
            logs_service_pb2_grpc.LogsServiceStub(channel).Export(request, timeout=5)
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("logs/grpc 1", summary)
        self.assertIn("- ERROR trace=yes 'request failed'", summary)

    def test_session_replay_summary_without_event_content(self):
        from receiver import replay
        envelope = {"sessionId": "5f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5", "chunkIndex": 0, "eventCount": 2, "hasFullSnapshot": True,
                    "isFinal": False, "maskingMode": "MaskSensitiveInputsOnly", "url": "http://127.0.0.1:1/"}
        events = [{"type": 4, "data": {"href": "http://127.0.0.1:1/"}}, {"type": 2, "data": {"text": "typed-secret-value"}}]
        status, _, _ = post(self.port("otlp_http"), replay.CHUNK, frame(envelope, events),
                            {replay.TOKEN_HEADER: "t", replay.APP_HEADER: "orders-web"})
        self.assertEqual(status, 202)
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("## Session replay (1 sessions, 1 chunks)", summary)
        self.assertIn("| 5f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5 | 1 | yes | no | MaskSensitiveInputsOnly | 2 / 2 |", summary)
        self.assertIn(f"Chunks with {replay.TOKEN_HEADER}: 1/1", summary)
        chunks = json.loads((self.out / "replay" / "chunks.json").read_text())
        self.assertEqual((chunks[0]["events"], chunks[0]["token_ok"]), (2, None))
        for text in (summary, (self.out / "replay" / "chunks.json").read_text()):
            self.assertNotIn("typed-secret-value", text)

    def test_replay_recorder_needs_assets(self):
        from receiver import replay
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port("otlp_http"), timeout=5)
        conn.request("GET", replay.LOADER)
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()
        self.receiver.flush()
        self.assertIn(f"GET {replay.LOADER} 404 x1", (self.out / "summary.md").read_text())

    def test_rejections_are_listed(self):
        post(self.port("otlp_http"), "/v1/nothing", b"x", {"Content-Type": "application/x-protobuf"})
        self.receiver.flush()
        self.assertIn("## Rejected requests", (self.out / "summary.md").read_text())


def basic(pair: str) -> str:
    import base64
    return "Basic " + base64.b64encode(pair.encode()).decode()


def get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


LANGFUSE = "/api/public/otel/v1/traces"
KEYS = "pk-lf-1111:sk-lf-secret-canary-2222"


@unittest.skipUnless(HAVE_DEPS, "needs grpcio, protobuf and opentelemetry-proto (run with uv, see README.md)")
class LangfuseStandIn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out = root / "telemetry"
        (root / "keys").write_text(KEYS + "\n")
        (root / "prompts.json").write_text(json.dumps([
            {"name": "support-chat", "version": 3, "labels": ["production", "latest"], "type": "chat",
             "prompt": [{"role": "system", "content": "You help with orders."}], "config": {"model": "stub-gpt-mini"}}]))
        (root / "datasets.json").write_text(json.dumps([
            {"name": "golden", "items": [{"input": {"message": "Where is o-1001?"}, "expectedOutput": {"answer": "shipped"}},
                                         {"id": "item-2", "input": {"message": "Refunds?"}, "metadata": {"topic": "returns"}}]}]))
        with mock.patch.dict(os.environ, {"OBS_RECEIVER_REPLAY_ASSETS": "", "OBS_HARNESS_REPLAY_ASSETS": ""}):
            self.receiver = receive.Receiver(self.out, langfuse_keys=root / "keys", langfuse_prompts=root / "prompts.json",
                                             langfuse_datasets=root / "datasets.json")
        self.endpoints = self.receiver.start()
        self.port = int(self.endpoints["otlp_http"].rsplit(":", 1)[1])
        self.headers = {"Content-Type": "application/json", "Authorization": basic(KEYS),
                        "x-langfuse-ingestion-version": "4"}

    def tearDown(self):
        self.receiver.stop()
        self.tmp.cleanup()

    def everything(self) -> str:
        return "\n".join(p.read_text() for p in self.out.rglob("*") if p.is_file())

    def test_langfuse_traces_path_received_and_kept_apart(self):
        self.assertEqual(self.endpoints["langfuse"], self.endpoints["otlp_http"])
        chat = span("chat stub-gpt-mini", 3, "00000000000000d2", "00000000000000d1",
                    **{"gen_ai.operation.name": "chat", "gen_ai.request.model": "stub-gpt-mini",
                       "langfuse.trace.name": "support-chat", "user.id": "u-42", "gen_ai.conversation.id": "c-1"})
        status, _, body = post(self.port, LANGFUSE, traces(chat), self.headers)
        self.assertEqual((status, body), (200, b"{}"))
        post(self.port, "/v1/traces", traces(span("POST /api/chat", 2, "00000000000000d1")), {"Content-Type": "application/json"})
        self.receiver.flush()
        files = {json.loads(p.read_text())["destination"] for p in (self.out / "otlp").glob("*.json")}
        self.assertEqual(files, {"ops", "langfuse"})
        summary = (self.out / "summary.md").read_text()
        self.assertIn("traces/http 1, traces/langfuse 1", summary)
        self.assertIn("## Traces sent to Langfuse (1 traces, 1 spans)", summary)
        self.assertIn("Observations sent to Langfuse (1): generation 1", summary)
        self.assertIn("trace name 1/1, user 1/1, session 1/1, release 0/1", summary)

    def test_langfuse_auth_and_ingestion_header_as_booleans(self):
        post(self.port, LANGFUSE, traces(span("chat m", 3, "00000000000000e1", **{"gen_ai.operation.name": "chat"})), self.headers)
        wrong = {**self.headers, "Authorization": basic("pk-lf-1111:sk-lf-wrong"), "x-langfuse-ingestion-version": "3"}
        post(self.port, LANGFUSE, traces(span("chat m", 3, "00000000000000e2", **{"gen_ai.operation.name": "chat"})), wrong)
        self.receiver.flush()
        auths = sorted((json.loads(p.read_text())["auth"] for p in (self.out / "otlp").glob("*.json")), key=lambda a: not a["ok"])
        self.assertEqual([(a["scheme"], a["key_shape"], a["ok"], a["ingestion_v4"]) for a in auths],
                         [("basic", True, True, True), ("basic", True, False, False)])
        self.assertIn("Basic auth 2/2, key shape 2/2, expected keys 1/2, x-langfuse-ingestion-version: 4 on 1/2",
                      (self.out / "summary.md").read_text())
        self.assertNotIn("secret-canary", self.everything())

    def test_langfuse_metrics_and_logs_rejected(self):
        for path in ("/api/public/otel/v1/metrics", "/api/public/otel/v1/logs"):
            status, _, _ = post(self.port, path, b"{}", self.headers)
            self.assertEqual(status, 404)
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("langfuse /api/public/otel/v1/metrics: Langfuse's OTLP endpoint takes traces only", summary)

    def test_datasets_and_experiment_summary(self):
        status, body = get(self.port, "/api/public/v2/datasets/golden", self.headers)
        dataset = json.loads(body)
        self.assertEqual((status, dataset["name"], dataset["projectId"]), (200, "golden", "stand-in-project"))
        status, body = get(self.port, "/api/public/dataset-items?datasetName=golden&page=1&limit=1", self.headers)
        page = json.loads(body)
        self.assertEqual((status, len(page["data"]), page["meta"]["totalPages"], page["data"][0]["datasetId"]),
                         (200, 1, 2, dataset["id"]))
        status, body = get(self.port, "/api/public/dataset-items?datasetName=golden&page=2&limit=1", self.headers)
        self.assertEqual(json.loads(body)["data"][0]["id"], "item-2")
        self.assertEqual(get(self.port, "/api/public/v2/datasets/missing", self.headers)[0], 404)
        keys = {"langfuse.experiment.id": "e-1", "langfuse.experiment.name": "prompt v4 run",
                "langfuse.experiment.item.id": "i-1", "langfuse.experiment.item.root_observation_id": "00000000000000a1",
                "langfuse.environment": "experiment"}
        root = span("experiment orders-assistant", 1, "00000000000000a1", **keys, **{
            "langfuse.observation.input": '{"message": "CANARY-EXP-1"}', "langfuse.observation.output": '{"answer": "x"}',
            "langfuse.experiment.item.expected_output": '{"answer": "y"}'})
        child = span("chat m", 3, "00000000000000a2", "00000000000000a1", **keys, **{"gen_ai.operation.name": "chat"})
        post(self.port, LANGFUSE, traces(root, child), self.headers)
        post(self.port, "/v1/traces", traces(span("chat m", 3, "00000000000000a3", "00000000000000a1")),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("- prompt v4 run: 1 items in 1 traces; item roots 1 (without a parent 1, with input and output 1, "
                      "with expected output 1); spans with the item keys 2/2; environment experiment", summary)
        self.assertIn("Experiment spans in the ops stream: 1", summary)
        self.assertIn("Dataset fetches: golden (dataset) 200, golden (items page 1) 200, golden (items page 2) 200, "
                      "missing (dataset) 404", summary)
        self.assertNotIn("CANARY-EXP-1", summary)
        self.assertEqual(len(json.loads((self.out / "langfuse" / "datasets.json").read_text())), 4)

    def test_provider_messages_in_ops_status_descriptions(self):
        echo = {**span("embeddings m", 3, "00000000000000f5", **{"gen_ai.operation.name": "embeddings", "error.type": "APIError"}),
                "status": {"code": 2, "message": "Error code: 500. Input began: 'CANARY-ECHO-5151'"}}
        typed = {**span("chat m", 3, "00000000000000f6", **{"gen_ai.operation.name": "chat", "error.type": "RateLimitError"}),
                 "status": {"code": 2, "message": "RateLimitError"}}
        post(self.port, "/v1/traces", traces(echo, typed), {"Content-Type": "application/json"})
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("status descriptions other than the error type: 1", summary)
        self.assertIn("PROVIDER MESSAGES IN THE OPS STREAM", summary)
        self.assertNotIn("CANARY-ECHO-5151", summary)

    def test_genai_summary_without_values(self):
        canary = "CANARY-PROMPT-5150"
        post(self.port, LANGFUSE, traces(span("chat m", 3, "00000000000000f1", **{
            "gen_ai.operation.name": "chat", "gen_ai.input.messages": f'[{{"role":"user","content":"{canary}"}}]',
            "langfuse.observation.prompt.name": "support-chat", "langfuse.observation.prompt.version": "3",
            "gen_ai.prompt.name": "support-chat"})), self.headers)
        post(self.port, "/v1/traces", traces(span("retrieval faq", 3, "00000000000000f2", **{
            "gen_ai.operation.name": "retrieval", "gen_ai.retrieval.query.text": canary, "langfuse.trace.name": "x"})),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("Content keys in Langfuse spans: gen_ai.input.messages x1", summary)
        self.assertNotIn("gen_ai.prompt.name x", summary, "prompt name and version are not content")
        self.assertIn("Prompt links: support-chat@3 x1", summary)
        self.assertIn("Ops stream: 1 AI spans; content keys: gen_ai.retrieval.query.text x1; langfuse.* keys: langfuse.trace.name x1",
                      summary)
        self.assertIn("CONTENT IN THE OPS STREAM", summary)
        self.assertNotIn(canary, summary)

    def test_scores_and_prompts_stand_ins(self):
        score = {"id": "m-1:user_feedback", "name": "user_feedback", "value": 1, "dataType": "BOOLEAN",
                 "traceId": "5b8efff798038103d269b633813fc60c", "observationId": "00000000000000d2",
                 "comment": "secret comment text"}
        for _ in range(2):
            status, _, body = post(self.port, "/api/public/scores", json.dumps(score).encode(), self.headers)
            self.assertEqual((status, json.loads(body)["id"]), (200, "m-1:user_feedback"))
        batch = {"batch": [{"id": "e1", "type": "score-create", "body": {"name": "schema_valid", "value": 0,
                                                                          "dataType": "BOOLEAN", "traceId": "t"}},
                           {"id": "e2", "type": "trace-create", "body": {"name": "legacy"}}]}
        status, _, body = post(self.port, "/api/public/ingestion", json.dumps(batch).encode(), self.headers)
        self.assertEqual((status, len(json.loads(body)["errors"])), (207, 1))
        status, body = get(self.port, "/api/public/v2/prompts/support-chat?label=production", self.headers)
        self.assertEqual((status, json.loads(body)["version"]), (200, 3))
        self.assertEqual(get(self.port, "/api/public/v2/prompts/missing", self.headers)[0], 404)
        self.assertEqual(get(self.port, "/api/public/health")[0], 200)
        self.receiver.flush()
        summary = (self.out / "summary.md").read_text()
        self.assertIn("- user_feedback BOOLEAN value=1 on trace+observation id=yes comment=19 chars auth=basic", summary)
        self.assertIn("Scores sent again with the same id (upserts): 1", summary)
        self.assertIn("Prompt fetches: support-chat (label production) 200, missing (label production) 404", summary)
        self.assertIn("legacy ingestion event trace-create", summary)
        self.assertNotIn("secret comment text", self.everything())


@unittest.skipUnless(HAVE_DEPS, "needs grpcio, protobuf and opentelemetry-proto (run with uv, see README.md)")
class Cli(unittest.TestCase):
    def test_prints_endpoints_and_stops_after_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = receive.main(["--out", str(Path(tmp) / "t"), "--seconds", "0.5"])
            self.assertEqual(code, 0)
            first = json.loads(stdout.getvalue().splitlines()[0])
            self.assertEqual(set(first), {"otlp_grpc", "otlp_http", "pyroscope", "langfuse", "summary"})
            self.assertIn("received 0 export requests", stdout.getvalue())

    def test_empty_store_summary(self):
        self.assertIn("profile uploads: 0", receive.render_summary(Store()))


@unittest.skipUnless(HAVE_DEPS, "needs grpcio, protobuf and opentelemetry-proto (run with uv, see README.md)")
class CanaryScan(unittest.TestCase):
    CANARIES = {"typed-email": "Canary.Person@example.invalid", "callback-code": "CANARYCODE7781",
                "search": "canary search term", "cookie": "CANARYCOOKIE7787"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "canaries.json").write_text(json.dumps(self.CANARIES))
        self.out = root / "telemetry"
        with mock.patch.dict(os.environ, {"OBS_RECEIVER_REPLAY_ASSETS": "", "OBS_HARNESS_REPLAY_ASSETS": ""}):
            self.receiver = receive.Receiver(self.out, canaries=root / "canaries.json")
        self.endpoints = self.receiver.start()
        self.http = int(self.endpoints["otlp_http"].rsplit(":", 1)[1])

    def tearDown(self):
        self.receiver.stop()
        self.tmp.cleanup()

    def test_reports_where_each_canary_went_without_writing_values(self):
        from receiver import replay
        leaky = span("GET", 3, "00000000000000d1",
                     **{"url.full": "https://app.example.com/auth/callback?code=CANARYCODE7781"})
        post(self.http, "/v1/traces", traces(leaky), {"Content-Type": "application/json"})
        envelope = {"sessionId": "5f0c1d2e3a4b5c6d7e8f90a1b2c3d4e5", "chunkIndex": 0, "eventCount": 1,
                    "hasFullSnapshot": True, "isFinal": False}
        events = [{"type": 3, "data": {"text": "canary.person@example.invalid"}}]  # typed in lower case
        post(self.http, replay.CHUNK, frame(envelope, events), {replay.TOKEN_HEADER: "t", replay.APP_HEADER: "web"})
        self.receiver.flush()
        found = json.loads((self.out / "canaries.json").read_text())
        self.assertEqual(found, {"typed-email": ["replay events"], "callback-code": ["OTLP"], "search": [], "cookie": []})
        summary = (self.out / "summary.md").read_text()
        self.assertIn("## Canaries (4 checked; values are never written)", summary)
        self.assertIn("| callback-code | OTLP |", summary)
        self.assertIn("| typed-email | replay events |", summary)
        # The scan's own outputs never hold a value; the payload files and the span tree show what the app
        # sent, which is the leak being reported.
        scan_outputs = (summary[summary.index("## Canaries"):] + (self.out / "canaries.json").read_text()).lower()
        for value in self.CANARIES.values():
            self.assertNotIn(value.lower(), scan_outputs)

    def test_percent_encoded_values_count(self):
        post(self.http, "/v1/logs", json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [
            {"body": {"stringValue": "search failed"},
             "attributes": [{"key": "url.query", "value": {"stringValue": "q=canary%20search%20term"}}]}]}]}]}).encode(),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        self.assertEqual(json.loads((self.out / "canaries.json").read_text())["search"], ["OTLP"])

    def test_clean_run_says_none_found(self):
        post(self.http, "/v1/traces", traces(span("route.change", 1, "00000000000000e1", **{"app.route": "/users/:id"})),
             {"Content-Type": "application/json"})
        self.receiver.flush()
        self.assertIn("None found in any stream.", (self.out / "summary.md").read_text())


class CanaryFile(unittest.TestCase):
    def test_load_accepts_an_object_or_a_list_and_rejects_short_values(self):
        from receiver import canaries
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(["first-value", "second-value"]))
            self.assertEqual(canaries.load(path), {"canary-1": "first-value", "canary-2": "second-value"})
            path.write_text(json.dumps({"ok": "long-enough", "short": "abc"}))
            with self.assertRaises(ValueError):
                canaries.load(path)


if __name__ == "__main__":
    unittest.main()
