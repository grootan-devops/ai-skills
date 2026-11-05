"""Data-model checks over captured telemetry (references/data-model.md).

Every attribute, field and label has a class per location: M, C, R, O or P. These checks assert the M, C and
P rows the receiver can see; R and O rows are never asserted, so an SDK's `telemetry.sdk.*`, `process.runtime.*`
or `os.type` pass. C rows are asserted only where the condition is visible in the capture (a platform in the
resource, a failed span, an MCP method, a scenario request marked authenticated or anonymous).

Findings use the static audit's shape and severities (scripts/audit.py, references/safe-capture.md): P0 a secret
reached telemetry, P1 a mandatory row is missing or content, IDs or unbounded values sit where the rules don't
allow them, P2 a prohibited row that leaks nothing. Messages name keys, labels and counts, never values.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit import Finding  # noqa: E402  (one finding shape for static and dynamic checks)

from . import genai  # noqa: E402
from . import telemetry as tm  # noqa: E402

# Resource

SERVICE_ROWS = ("service.name", "service.namespace", "service.version", "deployment.environment.name")
CLOUD_ROWS = ("cloud.provider", "cloud.platform", "cloud.region")
FAAS_ROWS = CLOUD_ROWS + ("faas.name", "faas.version", "faas.instance")
PLATFORM_ROWS = {
    "k8s": ("k8s.namespace.name", "k8s.pod.name", "k8s.node.name"),
    "ecs": CLOUD_ROWS + ("aws.ecs.cluster.arn", "aws.ecs.task.arn", "aws.ecs.launchtype"),
    "lambda": FAAS_ROWS,
    "cloud-run": FAAS_ROWS,
    "cloud-run-job": CLOUD_ROWS + ("faas.name", "gcp.cloud_run.job.execution", "gcp.cloud_run.job.task_index"),
    "vm": ("host.name",),
    "cloud-vm": ("host.name",) + CLOUD_ROWS,
    "docker": ("host.name", "container.id"),
    "local": (),  # a plain process on the developer's machine: the four service rows only
}
PLATFORMS = tuple(PLATFORM_ROWS)
PLATFORM_LABEL = {"k8s": "Kubernetes", "ecs": "ECS/Fargate", "lambda": "Lambda/FaaS", "cloud-run": "Cloud Run service",
                  "cloud-run-job": "Cloud Run job", "vm": "VM", "cloud-vm": "cloud VM", "docker": "plain Docker"}
# M and C rows: what a profile's static labels mirror. k8s.cluster.name is C (several clusters, one backend).
MC_RESOURCE = set(SERVICE_ROWS).union(*PLATFORM_ROWS.values()) | {"k8s.cluster.name"}
# R and O rows: allowed on the resource, never profile labels.
RO_RESOURCE = {
    "k8s.deployment.name", "k8s.statefulset.name", "k8s.daemonset.name", "k8s.cronjob.name", "k8s.job.name",
    "k8s.replicaset.name", "k8s.container.name", "cloud.availability_zone", "cloud.account.id", "cloud.resource_id",
    "aws.ecs.task.family", "aws.ecs.task.revision", "aws.ecs.task.id", "aws.ecs.container.arn", "aws.eks.cluster.arn",
    "aws.log.group.names", "aws.log.stream.names", "gcp.gce.instance.name", "host.type", "host.arch", "container.name",
    "container.image.name", "container.image.tags", "telemetry.sdk.language", "telemetry.sdk.version",
    "telemetry.sdk.name", "process.runtime.name", "process.runtime.version", "process.runtime.description",
    "os.type", "os.description", "os.name", "os.version",
}
RO_PREFIXES = ("telemetry.", "process.runtime.", "os.")
# Not added to the resource: process IDs and command lines, VCS data, release IDs, instance and UID keys.
RESOURCE_PROHIBITED = {"process.pid", "process.parent_pid", "process.command", "process.command_line",
                       "process.command_args", "service.instance.id", "k8s.pod.uid", "k8s.node.uid", "host.id",
                       "deployment.id"}
RESOURCE_PROHIBITED_PREFIXES = ("process.executable.", "vcs.")
K8S_PLATFORMS = {"aws_eks", "aws_openshift", "gcp_kubernetes_engine", "azure_aks", "azure_openshift"}
VM_PLATFORMS = {"aws_ec2", "gcp_compute_engine", "azure_vm", "alibaba_cloud_ecs", "tencent_cloud_cvm"}
FAAS_PLATFORMS = {"aws_lambda", "gcp_cloud_functions", "azure_functions", "azure_container_apps"}

# Request and identity context (core rows, then the reference catalogue).
CORE_IDENTITY = ("user.id", "tenant.id", "session.id")
IDENTITY_ROWS = CORE_IDENTITY + (
    "correlation.id", "user.roles", "app.user.type", "app.tenant.tier", "app.subscription.plan", "tenant.name",
    "app.organization.name", "app.organization.id", "app.account.id", "app.customer.id", "app.auth.method",
    "app.auth.provider", "app.auth.result", "http.route", "client.address", "user_agent.original", "app.client.name",
    "app.client.version", "enduser.id")
PER_REQUEST = set(IDENTITY_ROWS) - {"http.route", "client.address", "user_agent.original"} | {
    "http.route", "url.path", "url.full", "url.query", "client.address", "request.id", "app.request.id",
    "trace_id", "trace.id", "traceid", "span_id", "span.id", "spanid", "gen_ai.conversation.id", "mcp.session.id",
    "session.previous_id"}
REQUEST_ID_KEYS = {"request.id", "app.request.id", "request_id", "requestid", "http.request.header.x-request-id",
                   "http.request.header.x_request_id", "http.response.header.x-request-id"}
CORRELATION_HEADER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Secrets and personal data, in values and key names.
SECRET_KEYS = {"authorization", "proxy-authorization", "proxy_authorization", "cookie", "set-cookie", "set_cookie",
               "x-api-key", "x_api_key", "api-key", "api_key", "apikey", "password", "passwd", "secret",
               "client_secret", "access_token", "refresh_token", "id_token", "token", "private_key", "card_number",
               "cvv", "x-auth-token", "x_auth_token"}
SECRET_VALUE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|\b[Bb]earer\s+[A-Za-z0-9._~+/-]{16,}=*"
    r"|\b[Bb]asic\s+[A-Za-z0-9+/]{16,}={0,2}(?![\w+/])|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-lf-[0-9a-f-]{8,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}|\bgh[pousr]_[A-Za-z0-9]{36}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}")
SECRET_QUERY = re.compile(
    r"[?&;](?:access[-_]?token|id[-_]?token|refresh[-_]?token|token|api[-_]?key|apikey|key|secret|client[-_]?secret"
    r"|password|passwd|sig|signature|x-amz-signature|x-goog-signature|x-amz-security-token|code)=(?!(?:REDACTED|"
    r"\[REDACTED\]|%5BREDACTED%5D|\*+|x+)?(?:[&#;]|$))([^&#;\s]+)", re.IGNORECASE)
URL_USERINFO = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s:]+:(?!REDACTED@)[^/@\s]+@", re.IGNORECASE)
URL_KEYS = ("url.full", "url.query", "http.url", "http.target", "url.original", "mcp.resource.uri", "server.address")
REDACTED = re.compile(r"^\s*(?:\[?redacted\]?|<redacted>|\[?filtered\]?|\*+|x{3,}|)\s*$", re.IGNORECASE)
CARD = re.compile(r"(?<![\d-])[3-6]\d{3}(?:[ -]?\d{4}){2}[ -]?\d{3,4}(?![\d-])")
EMAIL = re.compile(r"(?<![\w.%+*-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
PHONE = re.compile(r"(?<![\w*+])(?:\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}|\(\d{3}\) ?\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4})(?![\w-])")

# Operations (data-model.md "Operations"): the keys each operation's M rows require.
MCP_NAME = re.compile(r"^(?:tools|resources|prompts|completion|logging|sampling|roots|notifications)/\w+|^initialize\b")
A2A_METHODS = {"message/send", "message/stream", "tasks/get", "tasks/cancel", "tasks/resubscribe",
               "tasks/pushNotificationConfig/set", "tasks/pushNotificationConfig/get"}
CODE_NAME = re.compile(r"^[A-Za-z_$][\w$]*(?:(?:\.|::|#)[A-Za-z_$][\w$]*)+$")
MODEL_OPS = {"chat", "text_completion", "embeddings", "generate_content", "completion"}
GENAI_M = {"invoke_agent": ("gen_ai.agent.name",), "create_agent": ("gen_ai.agent.name",),
           "execute_tool": ("gen_ai.tool.name",), "retrieval": ("gen_ai.data_source.id",),
           "invoke_workflow": ("gen_ai.workflow.name",)}
CACHE_SYSTEMS = {"redis", "memcached", "valkey", "hazelcast", "aerospike"}
OP_M = {
    "http-server": ("http.request.method", "http.route", "url.scheme", "url.path"),
    "http-client": ("http.request.method", "server.address", "server.port", "url.full"),
    "service-call": ("service.peer.name", "server.address"),
    "db": ("db.system.name",),
    "messaging": ("messaging.system", "messaging.destination.name", "messaging.operation.type",
                  "messaging.operation.name"),
    "rpc": ("rpc.system.name", "rpc.method"),
    "graphql": ("graphql.operation.type",),
    "ws-connection": ("url.path", "client.address"),
    "ws-message": ("app.websocket.message.type",),
    "mcp": ("mcp.method.name",),
    "a2a": ("rpc.system.name", "rpc.method"),
    "job": ("app.job.name", "app.job.trigger"),
    "function": ("code.function.name",),
    "guardrail": ("app.genai.guardrail.name", "app.genai.guardrail.result"),
    "evaluation": ("gen_ai.evaluation.name",),
}
OP_LABEL = {"http-server": "HTTP server", "http-client": "HTTP client", "service-call": "service call", "db": "database",
            "messaging": "messaging", "rpc": "RPC", "graphql": "GraphQL", "ws-connection": "WebSocket connection",
            "ws-message": "WebSocket message", "mcp": "MCP", "a2a": "A2A", "job": "job run",
            "function": "application function", "guardrail": "guardrail", "evaluation": "evaluation", "genai": "GenAI"}
# Identity-context keys an operation's own rows allow on a span that isn't the entry span.
OP_ALLOWED = {"http-server": {"http.route", "client.address", "user_agent.original"}, "mcp": {"client.address"},
              "ws-connection": {"user.id", "tenant.id", "client.address"}, "ws-message": {"user.id", "tenant.id"}}
SPAN_PROHIBITED = {  # key prefix -> what it is
    "http.request.body": "a request body", "http.response.body": "a response body",
    "messaging.message.body": "a message payload", "messaging.message.payload": "a message payload",
    "db.query.parameter.": "query parameter values", "db.statement.parameter": "query parameter values",
    "graphql.variables": "GraphQL variables", "rpc.request.metadata.": "RPC metadata values",
    "rpc.response.metadata.": "RPC metadata values", "rpc.grpc.request.metadata.": "RPC metadata values",
    "rpc.grpc.response.metadata.": "RPC metadata values", "faas.event": "the event payload",
}
SQL_LITERAL = re.compile(r"'(?:[^']|'')+'|(?<![\w$?:.])(?:=|<|>|\bin\s*\(|,)\s*-?\d{2,}\b", re.IGNORECASE)
NAME_UNBOUNDED = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/\d{2,}(?=/|$|\s)|[0-9a-f]{16,}|\?\w+=|@\w",
    re.IGNORECASE)

# Logs (data-model.md "Logs, errors and trace correlation").
ACCESS_M = ("http.request.method", "http.route", "http.response.status_code", "client.address", "correlation.id")
AUDIT_M = ("app.audit.action", "app.audit.outcome", "app.audit.target.type", "correlation.id", "user.id",
           "client.address")
ERROR_M = ("exception.type", "exception.message")
HEADER_PREFIXES = ("http.request.header.", "http.response.header.", "http.request.cookie.")

# Metrics (data-model.md "Metrics"): Prometheus name -> (required labels, optional labels).
METRICS = {
    "http_server_request_duration_seconds": ({"method", "route", "status_code"}, set()),
    "http_server_requests_total": ({"method", "route"}, set()),
    "http_server_active_requests": ({"method"}, set()),
    "http_client_request_duration_seconds": ({"service", "method", "route", "status_code"}, set()),
    "http_client_active_requests": ({"service", "method", "route"}, set()),
    "rpc_server_duration_seconds": ({"method", "status_code"}, {"service"}),
    "rpc_client_duration_seconds": ({"method", "status_code"}, {"service"}),
    "messaging_process_duration_seconds": ({"system", "destination", "operation"}, {"outcome"}),
    "messaging_messages_total": ({"system", "destination", "operation"}, {"outcome"}),
    "db_pool_connections*": (set(), {"pool"}),
    "db_pool_wait_duration_seconds": (set(), {"pool"}),
    "websocket_connections": (set(), set()),
    "websocket_connections_total": (set(), set()),
    "websocket_disconnections_total": (set(), set()),
    "websocket_messages_received_total": (set(), set()),
    "websocket_messages_sent_total": (set(), set()),
    "websocket_send_errors_total": (set(), set()),
    "websocket_receive_errors_total": (set(), set()),
    "job_runs_total": ({"job_name", "outcome"}, set()),
    "job_duration_seconds": ({"job_name"}, set()),
    "gen_ai_client_operation_duration_seconds": ({"provider", "model", "operation", "outcome"}, set()),
    "gen_ai_client_tokens_total": ({"provider", "model", "token_type"}, set()),
    "gen_ai_client_time_to_first_chunk_seconds": ({"provider", "model"}, set()),
    "mcp_server_operation_duration_seconds": ({"method", "target", "outcome"}, set()),
    "mcp_client_operation_duration_seconds": ({"method", "target", "outcome", "server"}, set()),
    "a2a_client_request_duration_seconds": ({"agent", "method", "outcome"}, set()),
    "a2a_server_request_duration_seconds": ({"agent", "method", "outcome"}, set()),
    "auth_attempts_total": ({"method", "result"}, {"provider"}),  # C: only when the service authenticates
    "app_health_probe_failures_total": ({"probe", "dependency"}, set()),
}
# Fixed value lists the receiver can check (signals/metrics.md).
LABEL_VALUES = {("gen_ai_client_tokens_total", "token_type"): {"input", "output", "cache_read", "cache_creation",
                                                               "reasoning"}}
SAMPLE_SUFFIXES = ("_bucket", "_count", "_sum", "_created", "_gcount", "_gsum")
EXPOSITION_LABELS = {"le", "quantile"}
ID_LABEL = re.compile(
    r"(?:^|_)(?:enduser|user|tenant|org|organization|account|customer|session|request|correlation|trace|span"
    r"|conversation|order|message|invocation)_?(?:id|uuid)$"
    r"|^(?:app_)?(?:user|tenant|org|organization|account|customer|session|request|correlation|trace|span)$"
    r"|^(?:url|url_full|url_path|url_query|full_url|path|raw_path|uri|http_url|http_target|sql|query|statement"
    r"|db_statement|db_query_text|error_message|exception_message|message|body|email|user_email|enduser_email|text"
    r"|prompt|completion)$")
GENAI_COST = "gen_ai.usage.cost"

# Profiles (data-model.md "Profiling").
PROFILE_SUFFIX = re.compile(r"\.(?:cpu|wall|itimer|alloc_\w+|inuse_\w+|alloc|lock_\w+|lock|mutex_\w+|mutex"
                            r"|block_\w+|block|goroutines?)$")
PROFILE_SDK_LABELS = {"service_name", "pyroscope_spy", "__name__"}
PROFILE_VCS = {"service_git_ref", "service_repository", "service_root_path"}
PROFILE_ID = re.compile(r"(?:^|_)(?:trace|user|tenant|session|request|correlation|org|account|customer)_?id$|^route$")


class Collector:
    """Findings grouped by severity, rule and message: one line per kind of problem, with a count."""

    def __init__(self) -> None:
        self.items: dict[tuple, list] = {}

    def add(self, severity: str, rule: str, location: str, message: str) -> None:
        entry = self.items.setdefault((severity, rule, message), [location, 0])
        entry[1] += 1

    def findings(self) -> list[Finding]:
        out = []
        for (severity, rule, message), (location, count) in self.items.items():
            more = f" ({count} occurrences)" if count > 1 else ""
            out.append(Finding(severity, rule, location, message + more))
        order = {"P0": 0, "P1": 1, "P2": 2}
        return sorted(out, key=lambda f: (order[f.severity], f.rule, f.location, f.message))


# Helpers


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    return "" if value is None else str(value)


def _last(key: str) -> str:
    return key.rsplit(".", 1)[-1].lower()


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0 and len(set(digits)) > 1


def secret_in(key: str, value) -> str | None:
    """What makes this key or value a secret, or None. Presence counts: secrets are never written, not even masked."""
    last = _last(key)
    if last in SECRET_KEYS or (key.lower().startswith(HEADER_PREFIXES) and last in SECRET_KEYS):
        return f"{key} (a secret by name{', masked' if REDACTED.match(_text(value)) else ''})"
    text = _text(value)
    if not text:
        return None
    if SECRET_VALUE.search(text):
        return f"{key} (token- or key-shaped value)"
    if key in URL_KEYS or key.endswith((".url", ".uri")):
        if SECRET_QUERY.search(text):
            return f"{key} (secret query parameter)"
        if URL_USERINFO.search(text):
            return f"{key} (credentials in the URL)"
    for match in CARD.finditer(text):
        if _luhn(re.sub(r"\D", "", match.group(0))):
            return f"{key} (card-number-shaped value)"
    return None


def pii_in(value) -> str | None:
    """'email' or 'phone' when the value holds one unmasked; masked forms (j***@example.com, ***0123) pass."""
    text = _text(value)
    if "@" in text:
        for match in EMAIL.finditer(text):
            if "*" not in match.group(1):
                return "email"
    for match in PHONE.finditer(text):
        if 8 <= len(re.sub(r"\D", "", match.group(0))) <= 15:
            return "phone"
    return None


def infer_platform(resource: dict) -> str | None:
    """The platform the resource describes, from cloud.platform and the platform keys present."""
    platform = str(resource.get("cloud.platform") or "")
    keys = set(resource)
    if platform in K8S_PLATFORMS or any(k.startswith("k8s.") for k in keys):
        return "k8s"
    if platform == "aws_ecs" or any(k.startswith("aws.ecs.") for k in keys):
        return "ecs"
    if any(k.startswith("gcp.cloud_run.job.") for k in keys):
        return "cloud-run-job"
    if platform == "gcp_cloud_run":
        return "cloud-run"
    if platform in FAAS_PLATFORMS or any(k.startswith("faas.") for k in keys):
        return "lambda"
    if platform in VM_PLATFORMS:
        return "cloud-vm"
    if "container.id" in keys:
        return "docker"
    if "host.name" in keys:
        return "cloud-vm" if "cloud.provider" in keys else "vm"
    return None


def required_resource(resource: dict, platform: str | None) -> tuple[str, ...]:
    platform = platform or infer_platform(resource)
    if platform == "vm" and "cloud.provider" in resource:
        platform = "cloud-vm"
    return SERVICE_ROWS + PLATFORM_ROWS.get(platform or "local", ())


def _service(resource: dict) -> str:
    return str(resource.get("service.name") or "?")


# Resource


def check_resources(resources: list[tuple[str, str, dict]], platform: str | None, out: Collector) -> None:
    """resources: (signal, location, attributes) for every OTLP resource received."""
    seen: dict[str, dict[str, set]] = {}
    for signal, location, resource in resources:
        where = f"{signal}: resource of {_service(resource)} ({location})"
        detected = platform or infer_platform(resource)
        for key in required_resource(resource, platform):
            if resource.get(key) in (None, ""):
                cls = "M" if key in SERVICE_ROWS else f"C, {PLATFORM_LABEL.get(detected, detected)}"
                out.add("P1", "resource-missing", where, f"resource lacks {key} ({cls})")
        for key in resource:
            lower = key.lower()
            if lower in PER_REQUEST:
                out.add("P1", "resource-per-request", where, f"{key} is a per-request value and never a resource "
                        "attribute; it belongs on the entry span")
            elif lower in RESOURCE_PROHIBITED or lower.startswith(RESOURCE_PROHIBITED_PREFIXES):
                out.add("P2", "resource-prohibited", where, f"{key} is not added to the resource (process IDs, "
                        "command lines, VCS data, release IDs, instance IDs and UIDs are left out)")
        per_key = seen.setdefault(_service(resource), {})
        for key in MC_RESOURCE:
            if key in resource:
                per_key.setdefault(key, set()).add(_text(resource[key]))
    for service, per_key in seen.items():
        for key, values in sorted(per_key.items()):
            if len(values) > 1:
                out.add("P1", "resource-mismatch", f"resources of {service}", f"{key} differs between exports of one "
                        f"service ({len(values)} values); traces, logs, metrics and profiles share one resource")


# Spans


def _index(spans: list) -> dict:
    return {(s.trace_id, s.span_id): s for s in spans}


def is_entry(span, by_id: dict) -> bool:
    """The service's local root: no parent, a parent not received, or a parent in another service."""
    if not span.parent_id:
        return True
    parent = by_id.get((span.trace_id, span.parent_id))
    return parent is None or _service(parent.resource) != _service(span.resource)


def operation(span) -> str | None:
    a = span.attrs
    if "mcp.method.name" in a or MCP_NAME.match(span.name):
        return "mcp"
    if any(k.startswith("app.a2a.") for k in a) or (
            a.get("rpc.system.name") == "jsonrpc" and str(a.get("rpc.method", "")) in A2A_METHODS):
        return "a2a"
    if "gen_ai.operation.name" in a:
        return "genai"
    if "app.genai.guardrail.name" in a or span.name.startswith("guardrail "):
        return "guardrail"
    if "gen_ai.evaluation.name" in a or span.name.startswith("evaluate "):
        return "evaluation"
    if any(k.startswith("app.websocket.") for k in a) or span.name.startswith("WS "):
        return "ws-message" if "app.websocket.message.type" in a or not span.name.startswith("WS /") else "ws-connection"
    if any(k.startswith("messaging.") for k in a):
        return "messaging"
    if "db.system.name" in a or "db.system" in a:
        return "db"
    if any(k.startswith("graphql.") for k in a):
        return "graphql"
    if "rpc.system.name" in a or "rpc.system" in a:
        return "rpc"
    if "http.request.method" in a or "http.method" in a:
        return "http-server" if span.kind == "SERVER" else "http-client"
    if span.kind == "CLIENT" and "service.peer.name" in a:
        return "service-call"
    if "app.job.name" in a or (span.kind == "INTERNAL" and span.name.startswith("job ") and not span.parent_id):
        return "job"
    if span.kind == "INTERNAL" and CODE_NAME.match(span.name):
        return "function"
    return None


def mandatory(span, op: str | None, entry: bool, platform: str | None) -> list[str]:
    """The M (and visible C) keys this span must carry, each with its class note."""
    a, need = span.attrs, []
    if op == "genai":
        name = str(a.get("gen_ai.operation.name"))
        keys = ("gen_ai.provider.name", "gen_ai.request.model") if name in MODEL_OPS else GENAI_M.get(name, ())
        need += [(k, "M") for k in keys]
    elif op == "db":
        need.append(("db.system.name", "M"))
        system = str(a.get("db.system.name") or a.get("db.system") or "")
        if system in CACHE_SYSTEMS or "db.query.summary" in a or "db.query.text" in a:
            need.append(("db.operation.name", "M, operation known"))
    elif op == "a2a":
        need += [(k, "M") for k in OP_M["a2a"]]
        if span.kind == "CLIENT":
            need += [(k, "M, client") for k in ("gen_ai.operation.name", "gen_ai.agent.name", "server.address",
                                                 "server.port", "url.template")]
    elif op == "mcp":
        need.append(("mcp.method.name", "M"))
        method = str(a.get("mcp.method.name") or span.name.split(" ", 1)[0])
        if method == "tools/call":
            need.append(("gen_ai.tool.name", "C, tools/call"))
        elif method == "prompts/get":
            need.append(("gen_ai.prompt.name", "C, prompts/get"))
        elif method.startswith("resources/") and method != "resources/list":
            need.append(("mcp.resource.uri", "C, resources/*"))
    elif op in OP_M:
        need += [(k, "M") for k in OP_M[op]]
    if op == "rpc" and str(a.get("rpc.system.name") or a.get("rpc.system")) == "grpc" \
            and not _no_response(a):
        need.append(("rpc.response.status_code", "C, response received"))
    if op == "http-client" and not _no_response(a):
        need.append(("http.response.status_code", "C, response received"))
    if entry and span.kind in ("SERVER", "CONSUMER") and op != "mcp":
        need.append(("correlation.id", "M on the entry span"))
        if platform in ("lambda", "cloud-run"):
            need.append(("faas.trigger", "M, serverless invocation"))
    if span.status == "ERROR":
        need.append(("error.type", "C, failed"))
    return need


def _no_response(attrs: dict) -> bool:
    """An error.type that isn't a status code means no response came back."""
    error = attrs.get("error.type")
    return error not in (None, "") and not re.fullmatch(r"-?\d+", str(error))


def check_spans(spans: list, platform: str | None, out: Collector) -> None:
    by_id = _index(spans)
    for span in spans:
        where = f"traces: {span.kind} span {span.name!r} ({span.file})"
        a, op = span.attrs, operation(span)
        entry = is_entry(span, by_id)
        span_platform = platform or infer_platform(span.resource)
        for key, cls in mandatory(span, op, entry, span_platform):
            if a.get(key) in (None, ""):
                what = OP_LABEL.get(op, "entry") if op else "entry"
                out.add("P1", "span-missing", where, f"{what} span lacks {key} ({cls})")
        if op == "ws-message" and not span.links:
            out.add("P1", "span-missing", where, "WebSocket message span has no link to its connection span (M)")
        if op == "graphql" and ("graphql.field.name" in a or "graphql.field.path" in a):
            out.add("P2", "span-prohibited", where, "GraphQL resolver span; resolver spans are off (tracing scope keeps "
                    "the operation and client calls)")
        if span.status == "OK":
            out.add("P2", "span-status", where, "status OK; the status stays UNSET unless the span's own operation failed")
        status = tm.http_status(a)
        if op == "http-server" and status is not None:
            if status >= 500 and span.status != "ERROR":
                out.add("P2", "span-status", where, "HTTP server span with a 5xx response is not ERROR")
            elif 400 <= status < 500 and span.status == "ERROR":
                out.add("P2", "span-status", where, "HTTP server span with a 4xx response is ERROR; client errors stay UNSET")
        if op == "http-client" and status is not None and status >= 400 and span.status != "ERROR":
            out.add("P2", "span-status", where, "HTTP client span with a 4xx/5xx response is not ERROR")
        if NAME_UNBOUNDED.search(span.name):
            out.add("P1", "span-name-unbounded", where, "span name holds an ID, a raw path or a query; names stay "
                    "low-cardinality (route templates, operation names)")
        if not entry:
            allowed = OP_ALLOWED.get(op, set())
            for key in IDENTITY_ROWS:
                if key in a and key not in allowed:
                    out.add("P2", "span-identity-child", where, f"{key} on a child span; request and identity context "
                            "go on the entry span only")
        for key, value in a.items():
            _span_key(key, value, op, where, out)
        for event in span.events:
            if event.get("name") == "exception":
                out.add("P2", "span-exception-event", where, "exception span event; exceptions go to the one error "
                        "log record, the span keeps error.type and its status")
            for key, value in event["attrs"].items():
                if genai.is_content_key(key):
                    out.add("P1", "span-content-ops", where, f"{key} in span event {event['name']!r}; AI content goes "
                            "to Langfuse only")
                _values(key, value, "traces", where, out, event=event["name"])
            if str(event.get("name", "")).startswith("gen_ai.") and event["name"] != "gen_ai.evaluation.result":
                out.add("P1", "span-content-ops", where, f"span event {event['name']!r} on the ops path; AI content "
                        "goes to Langfuse only")


def _span_key(key: str, value, op: str | None, where: str, out: Collector) -> None:
    lower = key.lower()
    if lower in REQUEST_ID_KEYS:
        out.add("P2", "span-request-id", where, f"{key} is not used; correlation.id covers it")
    if lower == "code.namespace":
        out.add("P2", "span-code-namespace", where, "code.namespace is deprecated; code.function.name carries the "
                "fully qualified name")
    if genai.is_content_key(key):
        out.add("P1", "span-content-ops", where, f"{key} on the ops path; AI content goes to Langfuse only")
    if lower == GENAI_COST:
        out.add("P2", "span-prohibited", where, f"{key} on the ops path; cost lives in Langfuse")
    for prefix, what in SPAN_PROHIBITED.items():
        if lower.startswith(prefix) and not lower.endswith(".size"):
            out.add("P1", "span-prohibited", where, f"{key} records {what}")
    if lower in ("db.query.text", "db.statement") and SQL_LITERAL.search(_text(value)):
        out.add("P1", "span-prohibited", where, f"{key} holds literal values; record the parameterised query")
    _values(key, value, "traces", where, out)


def _values(key: str, value, signal: str, where: str, out: Collector, event: str = "") -> None:
    rule = "span" if signal == "traces" else "log"
    in_event = f" in span event {event!r}" if event else ""
    secret = secret_in(key, value)
    if secret:
        out.add("P0", f"{rule}-secret", where, f"secret in {secret}{in_event}; secrets are never written, not even masked")
        return
    pii = pii_in(value)
    if pii:
        out.add("P1", f"{rule}-pii", where, f"unmasked {pii} in {key}{in_event}; personal data is written only "
                "through the masking helper")


# Identity per request (needs the scenario's exchanges)


def _match(exchange, servers: list) -> object | None:
    best, best_gap = None, None
    for span in servers:
        if tm.http_method(span.attrs) != exchange.method.upper() or tm.url_path(span.attrs) != exchange.path_only:
            continue
        if exchange.started_ns and span.start_ns and not (
                exchange.started_ns - 2e9 <= span.start_ns <= (exchange.ended_ns or exchange.started_ns) + 2e9):
            continue
        gap = abs(span.start_ns - exchange.started_ns)
        if best is None or gap < best_gap:
            best, best_gap = span, gap
    return best


def check_identity(spans: list, records: list, exchanges: list, out: Collector) -> None:
    """Anonymous requests carry no identity; authenticated ones carry their own, never another request's."""
    by_id = _index(spans)
    servers = [s for s in spans if s.kind == "SERVER" and is_entry(s, by_id) and operation(s) == "http-server"]
    access = {}
    for record in records:
        if record.event_name == "access_log" and record.trace_id:
            access.setdefault(record.trace_id, []).append(record)
    matched = []
    for exchange in exchanges:
        span = _match(exchange, servers)
        if span is None:
            continue
        servers = [s for s in servers if s is not span]  # one span per request
        matched.append((exchange, span))
    expected = {key: {ex.identity[key] for ex, _ in matched if ex.identity and ex.identity.get(key)}
                for key in CORE_IDENTITY}
    sent = [((ex.request_headers or {}).get("x-correlation-id"), sp) for ex, sp in matched]
    for exchange, span in matched:
        where = f"traces: entry span {span.name!r} for request {exchange.label}"
        carriers = [("entry span", span.attrs)] + [("access log", r.attrs) for r in access.get(span.trace_id, [])]
        if exchange.status is not None and tm.http_status(span.attrs) is None:
            out.add("P1", "span-missing", where, "HTTP server span lacks http.response.status_code (C, response sent)")
        identity = exchange.identity
        for what, attrs in carriers:
            if identity is None:
                continue
            for key in CORE_IDENTITY:
                value = attrs.get(key)
                want = identity.get(key)
                if not want:
                    if value not in (None, ""):
                        out.add("P1", "identity-anonymous", where, f"{key} on the {what} of a request without that "
                                "identity; anonymous requests carry none, never a fabricated or leftover value")
                elif value in (None, ""):
                    out.add("P1", "identity-missing", where, f"{what} lacks {key} (C, authenticated request)")
                elif value != want:
                    other = value in expected[key]
                    out.add("P1", "identity-leak" if other else "identity-mismatch", where,
                            f"{key} on the {what} is " + ("another request's value: context leaks between concurrent "
                                                          "requests" if other else "not the authenticated value"))
        header = (exchange.request_headers or {}).get("x-correlation-id")
        value = span.attrs.get("correlation.id")
        if header and value:
            if CORRELATION_HEADER.match(header) and value != header:
                out.add("P1", "correlation-mismatch", where, "correlation.id is not the valid x-correlation-id the "
                        "request sent")
            elif not CORRELATION_HEADER.match(header) and value == header:
                out.add("P1", "correlation-mismatch", where, "correlation.id kept an invalid x-correlation-id; invalid "
                        "values are replaced by a generated id")
        if value and any(v is not span and v.attrs.get("correlation.id") == value and not (header and h == header)
                         for h, v in sent):
            out.add("P1", "identity-leak", where, "correlation.id is shared with another request that didn't send it")


# Logs


def log_kind(record) -> str:
    if record.event_name == "access_log":
        return "access"
    if record.event_name == "exception":
        return "error"
    if record.event_name.startswith("audit."):
        return "audit"
    return "app"


def check_logs(records: list, spans: list, out: Collector) -> None:
    by_id = _index(spans)
    correlation = {}
    request_traces = set()
    for span in spans:
        if span.kind in ("SERVER", "CONSUMER") and is_entry(span, by_id):
            request_traces.add(span.trace_id)
            if span.attrs.get("correlation.id"):
                correlation.setdefault(span.trace_id, span.attrs["correlation.id"])
    for record in records:
        where = f"logs: {record.location}" + (f" event {record.event_name}" if record.event_name else "")
        kind, a = log_kind(record), record.attrs
        if record.severity is None:
            out.add("P2", "log-missing", where, "record has no severity (M)")
        in_request = bool(record.trace_id.strip("0")) and (record.trace_id in request_traces or not spans)
        need: list[tuple[str, str]] = []
        if kind == "access":
            need += [(k, "M, access log") for k in ACCESS_M]
            if not record.trace_id.strip("0"):
                out.add("P1", "log-missing", where, "access log has no native traceId/spanId (M)")
        elif kind == "audit":
            need += [(k, "M, audit log") for k in AUDIT_M]
        elif kind == "error":
            need += [(k, "M, error log") for k in ERROR_M]
        if kind in ("app", "error") and in_request:
            need.append(("correlation.id", "M, record written during a request"))
        for key, cls in need:
            if a.get(key) in (None, ""):
                out.add("P1", "log-missing", where, f"{kind} log lacks {key} ({cls})")
        if kind == "app" and "exception.type" in a:
            out.add("P2", "log-missing", where, "record carries exception.type without eventName exception (M on "
                    "error logs)")
        if kind == "app" and "http.request.method" in a and "http.response.status_code" in a:
            out.add("P2", "log-missing", where, "record looks like an access log without eventName access_log (M)")
        expected = correlation.get(record.trace_id)
        if expected and a.get("correlation.id") not in (None, "", expected):
            out.add("P1", "log-correlation-mismatch", where, "correlation.id differs from the entry span's in the same "
                    "trace")
        for key, value in a.items():
            _log_key(key, kind, where, out)
            _values(key, value, "logs", where, out)
        if record.body:
            _values("body", record.body, "logs", where, out)


def _log_key(key: str, kind: str, where: str, out: Collector) -> None:
    lower = key.lower()
    if lower in REQUEST_ID_KEYS:
        out.add("P2", "log-prohibited", where, f"{key} is not used; correlation.id covers it")
    elif lower == "code.namespace":
        out.add("P2", "log-prohibited", where, "code.namespace is deprecated; use code.function.name")
    elif genai.is_content_key(key):
        out.add("P1", "log-content", where, f"{key} in a log record; AI content goes to Langfuse only")
    elif lower.startswith(HEADER_PREFIXES) and kind != "access":
        out.add("P2", "log-prohibited", where, f"{key} in a {kind} log; allow-listed headers go on the access log only")
    elif lower.startswith("app.audit.") and kind != "audit":
        out.add("P2", "log-prohibited", where, f"{key} in a {kind} log; audit fields go on audit records only")
    elif lower.startswith("app.auth.") and kind in ("app", "error"):
        out.add("P2", "log-prohibited", where, f"{key} in a {kind} log; auth fields go on the access log and on "
                "audit records of auth events")
    elif lower in ("tenant.name", "app.organization.name") and kind in ("app", "access"):
        out.add("P2", "log-prohibited", where, f"{key} in a {kind} log; display names are personal-ish, log the ID")
    elif lower in ("user_agent.original", "client.address") and kind == "app":
        out.add("P2", "log-prohibited", where, f"{key} in an app log; it goes on the entry span and the access log")


# Metrics


def metric_base(name: str) -> str:
    if name in METRICS:
        return name
    for suffix in SAMPLE_SUFFIXES:
        if name.endswith(suffix) and name[:-len(suffix)] in METRICS:
            return name[:-len(suffix)]
    if name.startswith("db_pool_connections"):
        return "db_pool_connections*"
    return name


def _label_value_problem(label: str, value: str) -> str | None:
    if label == "route" and value not in ("", "unmatched") and NAME_UNBOUNDED.search(value):
        return "holds an ID or a raw path, not a route template"
    if pii_in(value) == "email":
        return "holds an email address"
    return None


def check_metrics(samples: list, points: list, out: Collector) -> None:
    for sample in samples:
        if sample.name == "target_info":
            continue
        where = f"metrics: /metrics line {sample.line}"
        base = metric_base(sample.name)
        labels = {k for k in sample.labels if k not in EXPOSITION_LABELS}
        _id_labels(base, labels, where, out)
        if base in METRICS:
            required, optional = METRICS[base]
            for label in sorted(required - labels):
                out.add("P1", "metric-label-missing", where, f"{base} lacks the required label {label}")
            for label in sorted(labels - required - optional):
                if not ID_LABEL.search(label):
                    out.add("P2", "metric-label-unknown", where, f"{base} has label {label}, which is not in its "
                            "allow-list")
        for label, value in sample.labels.items():
            allowed = LABEL_VALUES.get((base, label))
            if allowed and value not in allowed:
                out.add("P2", "metric-label-value", where, f"label {label} of {base} has a value outside its fixed list "
                        f"({', '.join(sorted(allowed))})")
            problem = _label_value_problem(label, value)
            if problem:
                out.add("P1", "metric-label-unbounded", where, f"label {label} of {base} {problem}")
    for point in points:
        where = f"metrics: OTLP {point.name} ({point.file})"
        _id_labels(point.name, {k.replace(".", "_").lower() for k in point.attrs}, where, out)
        if point.name.replace(".", "_").startswith("gen_ai_usage_cost") or "cost" in point.attrs:
            out.add("P1", "metric-label-prohibited", where, "cost as a metric on the ops path; cost lives in Langfuse")
        for key, value in point.attrs.items():
            problem = _label_value_problem("route" if key == "http.route" else key, _text(value))
            if problem:
                out.add("P1", "metric-label-unbounded", where, f"attribute {key} of {point.name} {problem}")


def _id_labels(name: str, labels: set, where: str, out: Collector) -> None:
    for label in sorted(labels):
        if ID_LABEL.search(label):
            out.add("P1", "metric-label-prohibited", where, f"{name} has label {label}: IDs, URLs, paths, SQL, messages "
                    "and free text are never metric labels")


# Profiles


def _profile_app(upload) -> str:
    name = upload.app_name or upload.labels.get("service_name", "")
    return PROFILE_SUFFIX.sub("", name)


def check_profiles(uploads: list, resources: list[tuple[str, str, dict]], out: Collector) -> None:
    by_service: dict[str, dict] = {}
    for _signal, _location, resource in resources:
        by_service.setdefault(_service(resource), resource)
    seen = set()
    for upload in uploads:
        app = _profile_app(upload)
        static = {k: str(v) for k, v in upload.labels.items() if not k.startswith("__") and k not in PROFILE_SDK_LABELS}
        key = (app, tuple(sorted(static.items())), tuple(sorted(upload.sample_labels)))
        if key in seen:
            continue
        seen.add(key)
        where = f"profiles: {upload.name} ({app or 'no app name'})"
        resource = by_service.get(app)
        expected = {k.replace(".", "_"): _text(v) for k, v in (resource or {}).items()
                    if k in MC_RESOURCE and k != "service.name" and v not in (None, "")}
        for label in ("service_namespace", "service_version", "deployment_environment_name"):
            expected.setdefault(label, None)  # M whatever the resource holds
        for label, value in sorted(expected.items()):
            if label not in static:
                out.add("P1", "profile-label-missing", where, f"static label {label} is missing; static labels mirror "
                        "the resource's M and C rows")
            elif value is not None and static[label] != value:
                out.add("P1", "profile-label-mismatch", where, f"static label {label} differs from the resource")
        if resource is not None and not app:
            out.add("P1", "profile-label-missing", where, "no application name; it is service.name")
        for label in sorted(static):
            dotted = label.replace("_", ".")
            ro = any(label == k.replace(".", "_") for k in RO_RESOURCE) or dotted.startswith(RO_PREFIXES)
            prohibited = any(label == k.replace(".", "_") for k in RESOURCE_PROHIBITED) or label in PROFILE_VCS \
                or label.startswith(("process_executable_", "vcs_"))
            if label in ("trace_id", "span_id") or PROFILE_ID.search(label):
                out.add("P1", "profile-label-prohibited", where, f"static label {label}: IDs and hand-set trace or span "
                        "ids are never profile labels")
            elif prohibited:
                out.add("P2", "profile-label-prohibited", where, f"static label {label} is a prohibited resource row; "
                        "profiles carry identity labels only")
            elif ro:
                out.add("P2", "profile-label-sdk-row", where, f"static label {label} is an R/O resource row; not a "
                        "profile label unless the profiling SDK adds it itself (then name it in the report as an "
                        "exception)")
        for label in sorted(upload.sample_labels):
            if label == "trace_id" or PROFILE_ID.search(label):
                out.add("P1", "profile-label-prohibited", where, f"sample label {label}: IDs are never profile labels "
                        "(span_id comes only from a span-profile integration or the module's heavy-section helper)")


# Entry point


# Browser RUM: Grafana Faro batches (data-model.md "Browser RUM (Faro)").
FARO_META_REQUIRED = (("app", "name"), ("app", "version"), ("app", "environment"), ("sdk", "name"))
FARO_USER_PROHIBITED = ("email", "username", "fullName", "attributes")
FARO_URL_KEY = re.compile(r"(?:url|uri|href|filename|toUrl|fromUrl|blockedURI|documentURI|sourceFile)$", re.IGNORECASE)
FARO_SPAN_USER = {"user.email", "user.name", "user.username", "user.full_name"}


def _walk_strings(node, key=""):
    if isinstance(node, dict):
        attr = node.get("key") if isinstance(node.get("key"), str) and "value" in node else None
        for k, v in node.items():
            yield from _walk_strings(v, attr if attr and k == "value" else k)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v, key)
    elif isinstance(node, str):
        yield key, node


def _span_attr_keys(traces) -> set:
    keys = set()
    for rs in (traces or {}).get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                keys.update(a.get("key") for a in sp.get("attributes", []))
    return keys


def check_faro(batches, out: Collector) -> None:
    views = set()
    for batch in batches:
        data, where = batch.data, batch.name
        meta = data.get("meta", {})
        for section, field_name in FARO_META_REQUIRED:
            if not (meta.get(section) or {}).get(field_name):
                out.add("P1", "faro-meta-missing", where, f"meta.{section}.{field_name} (M) missing")
        user = meta.get("user") or {}
        for field_name in FARO_USER_PROHIBITED:
            if user.get(field_name):
                out.add("P1", "faro-user-pii", where, f"meta.user.{field_name} (P) sent; pass the opaque user id only")
        if meta.get("app"):  # a batch without metas (a hand-made probe) says nothing about views
            views.add((meta.get("view") or {}).get("name") or "default")
        bad = FARO_SPAN_USER & _span_attr_keys(data.get("traces"))
        for key in sorted(bad):
            out.add("P1", "faro-span-user-pii", where, f"browser span attribute {key} (P)")
        for key, value in _walk_strings(data):
            secret = secret_in(key, value)
            if secret:
                out.add("P0", "faro-secret", where, f"secret in a Faro field: {secret}")
            pii = pii_in(value)
            if pii:
                out.add("P1", "faro-pii", where, f"unmasked {pii} in Faro field {key}")
            if FARO_URL_KEY.search(key) and re.search(r"[?#].", value):
                out.add("P2", "faro-url-query", where, f"URL with a query or fragment in Faro field {key}; only "
                        "allow-listed query parameters may stay")
    if batches and views == {"default"}:
        out.add("P2", "faro-view-default", batches[0].name, "every record has view.name=default; set the view to the "
                "route template on each route change")


def otlp_resources(payloads) -> list[tuple[str, str, dict]]:
    return [(p.signal, p.name, res) for p in payloads if p.destination != "langfuse" for res in tm.resources(p)]


def check(payloads, profiles=(), platform: str | None = None, exchanges=(), scrape_text: str | None = None,
          stdout_text: str | None = None, faro=()) -> list[Finding]:
    """All data-model checks over one capture. payloads: the OTLP exports; profiles: Pyroscope uploads; platform:
    one of PLATFORMS, else inferred per resource; exchanges: the scenario's requests (capture.Exchange) for the
    per-request identity checks; scrape_text: the app's /metrics; stdout_text: the app's stdout log lines; faro: the
    Grafana Faro batches (browser RUM)."""
    if platform is not None and platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; one of {', '.join(PLATFORMS)}")
    ops = [p for p in payloads if p.destination != "langfuse"]
    out = Collector()
    resources = otlp_resources(ops)
    spans = tm.spans(ops)
    records = tm.otlp_records(ops) + (tm.parse_stdout(stdout_text) if stdout_text else [])
    check_resources(resources, platform, out)
    check_spans(spans, platform, out)
    check_logs(records, spans, out)
    if exchanges:
        check_identity(spans, records, list(exchanges), out)
    check_metrics(tm.parse_scrape(scrape_text) if scrape_text else [], tm.metric_points(ops), out)
    check_profiles(list(profiles), resources, out)
    check_faro(list(faro), out)
    return out.findings()


def summary(findings: list[Finding], platform: str | None) -> list[str]:
    """The summary.md section: rule, location and message per kind of problem; never a value."""
    lines = [f"## Data model checks ({len(findings)} findings; platform: {platform or 'inferred per resource'})", "",
             "M, C and P rows of references/data-model.md; R and O rows are not asserted.", ""]
    if not findings:
        return lines + ["No findings.", ""]
    lines += [f"- [{f.severity}] [{f.rule}] {f.location}: {f.message}" for f in findings]
    return lines + [""]
