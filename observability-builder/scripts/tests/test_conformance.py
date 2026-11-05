"""Data-model checks over captured telemetry (receiver/conformance.py); runs without the receiver's gRPC deps."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receiver import conformance  # noqa: E402
from receiver.capture import Exchange, Payload, ProfileUpload  # noqa: E402

SERVICE = {"service.name": "orders", "service.namespace": "shop", "service.version": "1.2.3",
           "deployment.environment.name": "dev"}
K8S = {"k8s.namespace.name": "shop-dev", "k8s.pod.name": "orders-7d9c-x2k8p", "k8s.node.name": "worker-a1"}
TRACE = "5b8efff798038103d269b633813fc60c"
SECOND = "6c9f0008a9149214e37ac744924ad71d"


def kv(attrs: dict) -> list:
    out = []
    for key, value in attrs.items():
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def span(name, kind, sid, parent="", trace=TRACE, status="", events=(), links=(), start=1000, **attrs):
    doc = {"traceId": trace, "spanId": sid, "parentSpanId": parent, "name": name, "kind": f"SPAN_KIND_{kind}",
           "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + 10), "attributes": kv(attrs)}
    if status:
        doc["status"] = {"code": f"STATUS_CODE_{status}"}
    if events:
        doc["events"] = [{"name": n, "attributes": kv(a)} for n, a in events]
    if links:
        doc["links"] = [{"traceId": t, "spanId": s} for t, s in links]
    return doc


def traces(*spans, resource=None, seq=1):
    data = {"resourceSpans": [{"resource": {"attributes": kv(SERVICE if resource is None else resource)},
                               "scopeSpans": [{"spans": list(spans)}]}]}
    return Payload(seq, "traces", "http", data, b"")


def logs(*records, resource=None, seq=2):
    out = []
    for body, event, attrs, trace in records:
        rec = {"severityNumber": "SEVERITY_NUMBER_INFO", "body": {"stringValue": body}, "attributes": kv(attrs)}
        if event:
            rec["eventName"] = event
        if trace:
            rec["traceId"], rec["spanId"] = trace, "00000000000000a1"
        out.append(rec)
    return Payload(seq, "logs", "http", {"resourceLogs": [{"resource": {"attributes": kv(SERVICE if resource is None else resource)},
                                                           "scopeLogs": [{"logRecords": out}]}]}, b"")


def rules(findings, rule=None):
    return [(f.severity, f.rule, f.message) for f in findings if rule is None or f.rule == rule]


def messages(findings, rule):
    return [f.message for f in findings if f.rule == rule]


ENTRY = {"http.request.method": "GET", "http.route": "/orders/{id}", "url.scheme": "http", "url.path": "/orders/7",
         "http.response.status_code": 200, "correlation.id": "c-1"}


class ResourceChecks(unittest.TestCase):
    def check(self, resource, platform=None):
        return conformance.check([traces(span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY),
                                         resource=resource)], platform=platform)

    def test_service_rows_always_required(self):
        found = self.check({"service.name": "orders"})
        self.assertEqual(sorted(messages(found, "resource-missing")), [
            "resource lacks deployment.environment.name (M)", "resource lacks service.namespace (M)",
            "resource lacks service.version (M)"])

    def test_recommended_and_optional_rows_pass(self):
        found = self.check({**SERVICE, "telemetry.sdk.language": "python", "telemetry.sdk.version": "1.39.0",
                            "process.runtime.name": "cpython", "os.type": "linux", "host.arch": "amd64"})
        self.assertEqual(rules(found), [])

    def test_kubernetes_rows_inferred_and_uid_prohibited(self):
        found = self.check({**SERVICE, "k8s.pod.name": "p", "k8s.pod.uid": "u"})
        self.assertEqual(sorted(messages(found, "resource-missing")), [
            "resource lacks k8s.namespace.name (C, Kubernetes)", "resource lacks k8s.node.name (C, Kubernetes)"])
        self.assertEqual(len(messages(found, "resource-prohibited")), 1)
        self.assertEqual(rules(self.check({**SERVICE, **K8S})), [])

    def test_platform_rows_per_platform(self):
        cases = {
            "ecs": ({"cloud.platform": "aws_ecs", "cloud.provider": "aws", "cloud.region": "eu-west-1"},
                    {"aws.ecs.cluster.arn", "aws.ecs.task.arn", "aws.ecs.launchtype"}),
            "lambda": ({"cloud.platform": "aws_lambda"}, {"cloud.provider", "cloud.region", "faas.name", "faas.version",
                                                         "faas.instance"}),
            "cloud-run-job": ({"cloud.platform": "gcp_cloud_run", "gcp.cloud_run.job.execution": "e"},
                              {"cloud.provider", "cloud.region", "faas.name", "gcp.cloud_run.job.task_index"}),
            "docker": ({"container.id": "3f1c"}, {"host.name"}),
            "cloud-vm": ({"host.name": "vm-1", "cloud.provider": "gcp"}, {"cloud.platform", "cloud.region"}),
        }
        for name, (extra, missing) in cases.items():
            found = self.check({**SERVICE, **extra})
            got = {m.split()[2] for m in messages(found, "resource-missing")}
            self.assertEqual(got, missing, name)
            self.assertEqual(conformance.infer_platform({**SERVICE, **extra}), name)

    def test_platform_passed_in(self):
        found = self.check(dict(SERVICE), platform="k8s")
        self.assertEqual(len(messages(found, "resource-missing")), 3)
        with self.assertRaises(ValueError):
            conformance.check([], platform="mainframe")

    def test_prohibited_and_per_request_keys(self):
        found = self.check({**SERVICE, "process.pid": 12, "process.command_line": "x", "process.executable.path": "/x",
                            "vcs.ref.head.revision": "abc", "service.instance.id": "i", "host.id": "h",
                            "deployment.id": "d", "user.id": "u-1", "correlation.id": "c-1"})
        self.assertEqual(len(messages(found, "resource-prohibited")), 7)
        self.assertEqual(len(messages(found, "resource-per-request")), 2)
        self.assertTrue(all(f.severity == "P1" for f in found if f.rule == "resource-per-request"))

    def test_signals_share_one_resource(self):
        found = conformance.check([traces(span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY)),
                                   logs(("ok", "", {}, ""), resource={**SERVICE, "service.version": "1.2.4"})])
        self.assertEqual(len(messages(found, "resource-mismatch")), 1)


class SpanChecks(unittest.TestCase):
    def check(self, *spans, platform=None):
        return conformance.check([traces(*spans)], platform=platform)

    def test_http_server_and_entry_rows(self):
        found = self.check(span("GET", "SERVER", "00000000000000a1", **{"http.method": "GET"}))
        self.assertEqual(sorted(messages(found, "span-missing"))[1:], [
            "HTTP server span lacks http.request.method (M)", "HTTP server span lacks http.route (M)",
            "HTTP server span lacks url.path (M)", "HTTP server span lacks url.scheme (M)"])
        self.assertEqual(sorted(messages(found, "span-missing"))[0],
                         "HTTP server span lacks correlation.id (M on the entry span)")
        self.assertEqual(rules(self.check(span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY))), [])

    def test_mandatory_rows_per_operation(self):
        root = span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY)
        children = [
            span("GET", "CLIENT", "00000000000000b1", "00000000000000a1", **{"http.request.method": "GET"}),
            span("SELECT orders", "CLIENT", "00000000000000b2", "00000000000000a1",
                 **{"db.system.name": "postgresql", "db.query.text": "SELECT * FROM orders WHERE id = ?"}),
            span("send orders", "PRODUCER", "00000000000000b3", "00000000000000a1", **{"messaging.system": "kafka"}),
            span("Get", "CLIENT", "00000000000000b4", "00000000000000a1", **{"rpc.system.name": "grpc"}),
            span("chat m", "CLIENT", "00000000000000b5", "00000000000000a1", **{"gen_ai.operation.name": "chat"}),
            span("execute_tool t", "INTERNAL", "00000000000000b6", "00000000000000a1",
                 **{"gen_ai.operation.name": "execute_tool"}),
            span("tools/call search", "CLIENT", "00000000000000b7", "00000000000000a1"),
            span("job nightly", "INTERNAL", "00000000000000b8", **{"app.job.name": "nightly"}),
            span("OrderService.create", "INTERNAL", "00000000000000b9", "00000000000000a1"),
            span("invoke_agent billing", "CLIENT", "00000000000000c1", "00000000000000a1",
                 **{"rpc.system.name": "jsonrpc", "rpc.method": "message/send"}),
            span("WS chat", "SERVER", "00000000000000c2", **{"correlation.id": "c-2"}),
        ]
        found = messages(self.check(root, *children), "span-missing")
        for expected in ("HTTP client span lacks url.full (M)", "HTTP client span lacks server.port (M)",
                         "HTTP client span lacks http.response.status_code (C, response received)",
                         "database span lacks db.operation.name (M, operation known)",
                         "messaging span lacks messaging.destination.name (M)",
                         "messaging span lacks messaging.operation.type (M)", "RPC span lacks rpc.method (M)",
                         "RPC span lacks rpc.response.status_code (C, response received)",
                         "GenAI span lacks gen_ai.provider.name (M)", "GenAI span lacks gen_ai.request.model (M)",
                         "GenAI span lacks gen_ai.tool.name (M)", "MCP span lacks mcp.method.name (M)",
                         "MCP span lacks gen_ai.tool.name (C, tools/call)", "job run span lacks app.job.trigger (M)",
                         "application function span lacks code.function.name (M)",
                         "A2A span lacks gen_ai.agent.name (M, client)", "A2A span lacks url.template (M, client)",
                         "WebSocket message span lacks app.websocket.message.type (M)",
                         "WebSocket message span has no link to its connection span (M)"):
            self.assertIn(expected, found)

    def test_failed_spans_need_error_type_and_status_rules(self):
        found = self.check(
            span("GET /orders/{id}", "SERVER", "00000000000000a1", status="OK", **{**ENTRY, "http.response.status_code": 503}),
            span("GET", "CLIENT", "00000000000000b1", "00000000000000a1", status="ERROR",
                 events=[("exception", {"exception.type": "Timeout"})],
                 **{"http.request.method": "GET", "server.address": "p", "server.port": 80, "url.full": "http://p/x",
                    "http.response.status_code": 503}))
        self.assertIn("HTTP client span lacks error.type (C, failed)", messages(found, "span-missing"))
        self.assertEqual(sorted(messages(found, "span-status")), [
            "HTTP server span with a 5xx response is not ERROR",
            "status OK; the status stays UNSET unless the span's own operation failed"])
        self.assertEqual(len(messages(found, "span-exception-event")), 1)

    def test_prohibited_rows(self):
        found = self.check(
            span("GET /orders/{id}", "SERVER", "00000000000000a1", **{**ENTRY, "request.id": "r-1"}),
            span("OrderService.create", "INTERNAL", "00000000000000b1", "00000000000000a1",
                 **{"code.function.name": "a.OrderService.create", "code.namespace": "a.OrderService",
                    "user.id": "u-1", "tenant.id": "t-1"}),
            span("chat m", "CLIENT", "00000000000000b2", "00000000000000a1",
                 **{"gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai", "gen_ai.request.model": "m",
                    "gen_ai.input.messages": "[]", "gen_ai.usage.cost": "0.01"}),
            span("SELECT orders", "CLIENT", "00000000000000b3", "00000000000000a1",
                 **{"db.system.name": "postgresql", "db.operation.name": "SELECT",
                    "db.query.text": "SELECT * FROM users WHERE email = 'a@b.co'"}),
            span("GET /orders/12345", "CLIENT", "00000000000000b4", "00000000000000a1",
                 **{"http.request.method": "GET", "server.address": "p", "server.port": 80, "http.response.status_code": 200,
                    "url.full": "https://p/x?api_key=abc123", "http.request.header.authorization": "Bearer x"}))
        got = {f.rule for f in found}
        self.assertTrue({"span-request-id", "span-code-namespace", "span-identity-child", "span-content-ops",
                         "span-prohibited", "span-name-unbounded", "span-secret"} <= got, got)
        self.assertEqual(len(messages(found, "span-identity-child")), 2)
        self.assertTrue(all(f.severity == "P0" for f in found if f.rule == "span-secret"))

    def test_mcp_server_child_may_carry_client_address(self):
        found = self.check(span("POST /mcp", "SERVER", "00000000000000a1", **{**ENTRY, "http.route": "/mcp",
                                                                              "url.path": "/mcp"}),
                           span("tools/call search", "SERVER", "00000000000000b1", "00000000000000a1",
                                **{"mcp.method.name": "tools/call", "gen_ai.tool.name": "search",
                                   "client.address": "10.0.0.1"}))
        self.assertEqual(rules(found), [])

    def test_unmasked_pii_on_spans(self):
        found = self.check(span("GET /orders/{id}", "SERVER", "00000000000000a1",
                                **{**ENTRY, "app.contact": "jane.doe@example.com", "app.masked": "j***@example.com",
                                   "app.phone": "+44 7700 900123", "app.phone_masked": "***0123"}))
        self.assertEqual(sorted(messages(found, "span-pii")), [
            "unmasked email in app.contact; personal data is written only through the masking helper",
            "unmasked phone in app.phone; personal data is written only through the masking helper"])


class IdentityPerRequest(unittest.TestCase):
    def entry(self, sid, trace, start, path, **attrs):
        return span("GET /orders/{id}", "SERVER", sid, trace=trace, start=start,
                    **{**ENTRY, "url.path": path, **attrs})

    def test_anonymous_authenticated_and_leaks(self):
        payload = traces(
            self.entry("00000000000000a1", TRACE, 1000, "/orders/1", **{"user.id": "u-2"}),
            self.entry("00000000000000a2", SECOND, 2000, "/orders/2", **{"user.id": "u-2", "correlation.id": "c-2"}),
            self.entry("00000000000000a3", "7" * 32, 3000, "/orders/3", **{"correlation.id": "c-3"}),
            self.entry("00000000000000a4", "8" * 32, 4000, "/orders/4", **{"correlation.id": "c-4"}))
        exchanges = [
            Exchange("1", "GET", "/orders/1", 200, status=200, started_ns=1000, ended_ns=1100, identity={"user.id": "u-1"}),
            Exchange("2", "GET", "/orders/2", 200, status=200, started_ns=2000, ended_ns=2100, identity={"user.id": "u-2"}),
            Exchange("3", "GET", "/orders/3", 200, status=200, started_ns=3000, ended_ns=3100, identity={"user.id": "u-3"}),
            Exchange("4", "GET", "/orders/4", 200, status=200, started_ns=4000, ended_ns=4100, identity={},
                     request_headers={"x-correlation-id": "client-corr-1"}),
        ]
        found = conformance.check([payload], exchanges=exchanges)
        self.assertEqual([f.location for f in found if f.rule == "identity-leak"],
                         ["traces: entry span 'GET /orders/{id}' for request #1 GET /orders/1"])
        self.assertEqual(messages(found, "identity-missing"), ["entry span lacks user.id (C, authenticated request)"])
        self.assertEqual(len(messages(found, "correlation-mismatch")), 1)
        self.assertEqual(rules(found, "identity-anonymous"), [])
        anon = traces(self.entry("00000000000000a5", TRACE, 1000, "/orders/5", **{"user.id": "u-guest-77"}))
        found = conformance.check([anon], exchanges=[Exchange("5", "GET", "/orders/5", 200, status=200, started_ns=1000,
                                                              ended_ns=1100, identity={})])
        self.assertEqual(len(messages(found, "identity-anonymous")), 1)
        self.assertNotIn("u-guest-77", " ".join(f.message for f in found))


class LogChecks(unittest.TestCase):
    def check(self, *records):
        return conformance.check([traces(span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY)), logs(*records)])

    def test_correlation_on_request_records(self):
        found = self.check(("loaded", "", {"correlation.id": "c-1"}, TRACE), ("loaded", "", {}, TRACE),
                           ("start-up", "", {}, ""), ("other", "", {"correlation.id": "c-9"}, TRACE))
        self.assertEqual(messages(found, "log-missing"),
                         ["app log lacks correlation.id (M, record written during a request)"])
        self.assertEqual(len(messages(found, "log-correlation-mismatch")), 1)

    def test_access_audit_and_error_records(self):
        found = self.check(("GET", "access_log", {"http.request.method": "GET", "correlation.id": "c-1"}, TRACE),
                           ("login", "audit.login", {"app.audit.action": "login", "correlation.id": "c-1"}, TRACE),
                           ("boom", "exception", {"correlation.id": "c-1"}, TRACE))
        missing = messages(found, "log-missing")
        for expected in ("access log lacks http.route (M, access log)", "access log lacks client.address (M, access log)",
                         "audit log lacks app.audit.outcome (M, audit log)", "audit log lacks user.id (M, audit log)",
                         "error log lacks exception.type (M, error log)"):
            self.assertIn(expected, missing)

    def test_secrets_pii_and_prohibited_fields(self):
        found = self.check(
            ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "", {"correlation.id": "c-1"}, TRACE),
            ("login", "", {"correlation.id": "c-1", "password": "[REDACTED]"}, TRACE),
            ("mail to jane.doe@example.com", "", {"correlation.id": "c-1", "app.auth.method": "password",
                                                   "client.address": "1.2.3.4", "request_id": "r"}, TRACE),
            ("mail to j***@example.com, call ***0123", "", {"correlation.id": "c-1"}, TRACE))
        self.assertEqual(len(messages(found, "log-secret")), 2)
        self.assertEqual(messages(found, "log-pii"), ["unmasked email in body; personal data is written only through "
                                                      "the masking helper"])
        self.assertEqual(len(messages(found, "log-prohibited")), 3)
        self.assertNotIn("jane", " ".join(f.message + f.location for f in found))


SCRAPE = """# TYPE http_server_request_duration_seconds histogram
http_server_request_duration_seconds_bucket{method="GET",route="/orders/{id}",status_code="200",le="0.1"} 1
http_server_request_duration_seconds_count{method="GET",route="/orders/123",status_code="200"} 1
http_server_requests_total{method="GET"} 3
http_client_request_duration_seconds_sum{service="pay",method="GET",route="/x",status_code="200",user_id="u-1"} 1
websocket_connections 2
websocket_messages_received_total{type="chat"} 4
gen_ai_client_tokens_total{provider="openai",model="m",token_type="cache_creation"} 4
gen_ai_client_tokens_total{provider="openai",model="m",token_type="cache_write"} 4
auth_attempts_total{method="password",result="success",provider="local"} 1
rpc_server_duration_seconds_count{method="Get",status_code="0",service="orders"} 1
app_orders_total{plan="pro",email="jane@example.com"} 1
process_cpu_seconds_total 3
"""


class MetricChecks(unittest.TestCase):
    def test_allow_lists_and_prohibited_labels(self):
        found = conformance.check([], scrape_text=SCRAPE)
        self.assertEqual(messages(found, "metric-label-missing"),
                         ["http_server_requests_total lacks the required label route"])
        self.assertEqual(messages(found, "metric-label-unknown"),
                         ["websocket_messages_received_total has label type, which is not in its allow-list"])
        self.assertEqual(sorted(messages(found, "metric-label-prohibited")), [
            "app_orders_total has label email: IDs, URLs, paths, SQL, messages and free text are never metric labels",
            "http_client_request_duration_seconds has label user_id: IDs, URLs, paths, SQL, messages and free text are "
            "never metric labels"])
        self.assertEqual(len(messages(found, "metric-label-unbounded")), 2)  # route with an ID, email value
        self.assertEqual(len(messages(found, "metric-label-value")), 1)  # cache_write is now cache_creation

    def test_otlp_points_get_the_prohibited_label_check(self):
        payload = Payload(1, "metrics", "http", {"resourceMetrics": [{"resource": {"attributes": kv(SERVICE)}, "scopeMetrics": [
            {"metrics": [{"name": "orders", "sum": {"dataPoints": [{"asInt": "1", "attributes": kv({"tenant.id": "t"})}]}}]}]}]},
            b"")
        self.assertEqual(len(messages(conformance.check([payload]), "metric-label-prohibited")), 1)


class ProfileChecks(unittest.TestCase):
    def upload(self, labels, sample=None, app="orders.cpu"):
        return ProfileUpload(1, "ingest", app, labels, sample_labels=sample or {})

    def test_static_labels_mirror_the_resource(self):
        resource = {**SERVICE, **K8S, "telemetry.sdk.language": "python"}
        good = {"service_namespace": "shop", "service_version": "1.2.3", "deployment_environment_name": "dev",
                "k8s_namespace_name": "shop-dev", "k8s_pod_name": "orders-7d9c-x2k8p", "k8s_node_name": "worker-a1"}
        spans = traces(span("GET /orders/{id}", "SERVER", "00000000000000a1", **ENTRY), resource=resource)
        self.assertEqual(rules(conformance.check([spans], [self.upload(good, {"controller": ["x"], "span_id": ["1"]})])), [])
        bad = {**good, "service_version": "9.9.9", "telemetry_sdk_language": "python", "trace_id": "t"}
        del bad["k8s_node_name"]
        found = conformance.check([spans], [self.upload(bad, {"user_id": ["u"]})])
        self.assertEqual(messages(found, "profile-label-mismatch"), ["static label service_version differs from the resource"])
        self.assertEqual(len(messages(found, "profile-label-missing")), 1)
        self.assertEqual(len(messages(found, "profile-label-prohibited")), 2)  # trace_id static, user_id sample
        self.assertEqual(len(messages(found, "profile-label-sdk-row")), 1)
        self.assertTrue(all(f.severity == "P2" for f in found if f.rule == "profile-label-sdk-row"))


class Summary(unittest.TestCase):
    def test_summary_lines(self):
        self.assertIn("No findings.", conformance.summary([], None))
        found = conformance.check([traces(span("GET", "SERVER", "00000000000000a1"), resource={"service.name": "x"})])
        text = "\n".join(conformance.summary(found, "k8s"))
        self.assertIn("## Data model checks", text)
        self.assertIn("[resource-missing]", text)


if __name__ == "__main__":
    unittest.main()
