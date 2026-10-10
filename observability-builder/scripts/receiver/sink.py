"""The receiver as collector: OTLP/gRPC, OTLP/HTTP and Pyroscope sinks."""
from __future__ import annotations

import base64
import binascii
import gzip
import json
import re
import socket
import socketserver
import sys
import threading
import time
import zlib
from concurrent import futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2, metrics_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

from . import genai, pprof, replay
from .capture import Payload, ProfileUpload, ReplayChunk

REQUESTS = {
    "traces": trace_service_pb2.ExportTraceServiceRequest,
    "metrics": metrics_service_pb2.ExportMetricsServiceRequest,
    "logs": logs_service_pb2.ExportLogsServiceRequest,
}
RESPONSES = {
    "traces": trace_service_pb2.ExportTraceServiceResponse,
    "metrics": metrics_service_pb2.ExportMetricsServiceResponse,
    "logs": logs_service_pb2.ExportLogsServiceResponse,
}
HTTP_PATHS = {"/v1/traces": "traces", "/v1/metrics": "metrics", "/v1/logs": "logs"}
ID_KEYS = {"traceId", "spanId", "parentSpanId", "trace_id", "span_id", "parent_span_id"}
HEX = re.compile(r"^(?:[0-9a-fA-F]{2})+$")
PUSH_PATH = "/push.v1.PusherService/Push"


class PortBusyError(RuntimeError):
    """A standard telemetry port is already taken."""


# Decoding


def _convert_ids(node, convert) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ID_KEYS and isinstance(value, str):
                node[key] = convert(value)
            else:
                _convert_ids(value, convert)
    elif isinstance(node, list):
        for value in node:
            _convert_ids(value, convert)


def _hex_to_b64(value: str) -> str:
    # OTLP/JSON encodes ids as hex; protobuf's JSON parser expects base64.
    return base64.b64encode(bytes.fromhex(value)).decode() if HEX.match(value) else value


def _b64_to_hex(value: str) -> str:
    try:
        return base64.b64decode(value).hex()
    except (binascii.Error, ValueError):
        return value


def decode_otlp(signal: str, body: bytes, content_type: str = "application/x-protobuf"):
    """Parse an export request body (protobuf or OTLP/JSON) into its protobuf message."""
    message = REQUESTS[signal]()
    if "json" in content_type.lower():
        doc = json.loads(body.decode("utf-8"))
        _convert_ids(doc, _hex_to_b64)
        ParseDict(doc, message, ignore_unknown_fields=True)
    else:
        message.ParseFromString(body)
    return message


def to_canonical(message) -> dict:
    """MessageToDict form with trace and span ids as lowercase hex, as OTLP/JSON writes them."""
    data = MessageToDict(message)
    _convert_ids(data, _b64_to_hex)
    return data


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Request body (Content-Length or chunked), decompressed per Content-Encoding."""
    if "chunked" in handler.headers.get("Transfer-Encoding", "").lower():
        chunks = []
        while True:
            size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while handler.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                break
            chunks.append(handler.rfile.read(size))
            handler.rfile.readline()
        body = b"".join(chunks)
    else:
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
    encoding = handler.headers.get("Content-Encoding", "").strip().lower()
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)
    return body


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """multipart/form-data parts by field name."""
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        return {}
    boundary = b"--" + match.group(1).encode()
    parts = {}
    for chunk in body.split(boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk.lstrip(b"\n")
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            head, _, data = chunk.partition(b"\n\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = re.search(rb'name="([^"]*)"', head)
        if name:
            parts[name.group(1).decode("utf-8", "replace")] = data
    return parts


def parse_app_name(name: str) -> tuple[str, dict]:
    """Pyroscope `name` query: app.cpu{k=v,k2=v2} -> ("app.cpu", {k: v})."""
    match = re.match(r"^([^{]*)(?:\{(.*)\})?$", name.strip())
    if not match:
        return name, {}
    labels = {}
    for pair in (match.group(2) or "").split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            labels[key.strip()] = value.strip()
    return match.group(1).strip(), labels


def _summarise(part: str, data: bytes) -> dict:
    try:
        summary = pprof.decode_profile(data)
    except (pprof.WireError, OSError, EOFError, ValueError) as exc:
        return {"part": part, "error": f"not a pprof profile ({type(exc).__name__})"}
    summary["part"] = part
    return summary


def _merge_labels(parts: list[dict], extra: dict | None = None) -> dict:
    merged: dict[str, set] = {}
    for part in parts:
        for key, values in part.get("labels", {}).items():
            merged.setdefault(key, set()).update(values)
    for key, values in (extra or {}).items():
        merged.setdefault(key, set()).update(values)
    return {k: sorted(v) for k, v in sorted(merged.items())}


def ingest_upload(seq: int, query: str, content_type: str, body: bytes) -> ProfileUpload:
    """POST /ingest: raw pprof, gzipped pprof, multipart with a profile part, or a text format."""
    params = parse_qs(query, keep_blank_values=True)
    app_name, labels = parse_app_name(params.get("name", [""])[0])
    fmt = params.get("format", [""])[0].lower()
    upload = ProfileUpload(
        seq=seq, source="ingest", app_name=app_name, labels=labels, content_type=content_type,
        format=fmt, spy_name=params.get("spyName", [""])[0], raw=body, query=query, received_at=time.time(),
    )
    jfr_labels = None
    if content_type.lower().startswith("multipart/form-data"):
        form = parse_multipart(content_type, body)
        for key in ("profile", "prev_profile"):
            if key in form and form[key]:
                upload.parts.append(_summarise(key, form[key]))
        if "labels" in form and form["labels"]:
            try:
                jfr_labels = pprof.decode_jfr_labels(form["labels"])
            except (pprof.WireError, OSError, EOFError, ValueError):
                upload.note = "labels part could not be decoded"
        if "jfr" in form:
            upload.note = (upload.note + "; " if upload.note else "") + "JFR body recorded, not decoded"
        if not form:
            upload.note = "multipart body without parts"
    elif fmt in ("", "pprof"):
        upload.parts.append(_summarise("body", body))
    else:
        upload.note = f"{fmt} format: no sample labels; labels come from the name query"
    upload.sample_labels = _merge_labels(upload.parts, jfr_labels)
    return upload


def token_headers(items, token: str | None) -> list[str]:
    """Names of the headers whose value carries the expected token; values are never kept."""
    if not token:
        return []
    return sorted({str(name).lower() for name, value in items if token in str(value)})


def decode_push_body(content_type: str, body: bytes) -> list[dict]:
    """The series of a connect-protocol PushRequest, protobuf or JSON."""
    return pprof.decode_push(pprof.gunzip_if_needed(body), json_body="json" in content_type.lower())


def push_uploads(start_seq: int, series: list[dict], content_type: str, body: bytes) -> list[ProfileUpload]:
    """POST /push.v1.PusherService/Push: one upload per series."""
    uploads = []
    for offset, item in enumerate(series):
        labels = item["labels"]
        upload = ProfileUpload(
            seq=start_seq + offset, source="push", app_name=labels.get("service_name", ""), labels=labels,
            content_type=content_type, format="pprof", raw=body, received_at=time.time(),
        )
        upload.parts = [_summarise(f"sample{i}", raw) for i, raw in enumerate(item["profiles"])]
        upload.sample_labels = _merge_labels(upload.parts)
        uploads.append(upload)
    return uploads


class Store:
    """Thread-safe, in-memory record of everything the sinks received."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.otlp: list[Payload] = []
        self.profiles: list[ProfileUpload] = []
        self.rejected: list[dict] = []
        self.replay_chunks: list[ReplayChunk] = []
        self.replay_requests: list[dict] = []
        self.expected_token: str | None = None  # a token to compare x-oneuptime-token with; compared, never stored
        self.expected_langfuse_basic: str | None = None  # base64(public:secret) to compare with; never stored
        self.langfuse_prompts: list = []  # prompts the stand-in serves
        self.langfuse_datasets: list = []  # datasets the stand-in serves, each with its items
        self.langfuse_dataset_fetches: list[dict] = []
        self.langfuse_scores: list[dict] = []
        self.langfuse_prompt_fetches: list[dict] = []
        self.langfuse_media: list[dict] = []
        self._otlp_seq = 0
        self._profile_seq = 0
        self._upload_seq = 0

    def add_otlp(self, signal: str, transport: str, message, raw: bytes, content_type: str, header_names,
                 token_headers=(), destination: str = "ops", auth: dict | None = None) -> Payload:
        data = to_canonical(message)
        with self.lock:
            self._otlp_seq += 1
            payload = Payload(self._otlp_seq, signal, transport, data, raw, content_type,
                              sorted(set(header_names)), time.time(), sorted(set(token_headers)),
                              destination, dict(auth or {}))
            self.otlp.append(payload)
        return payload

    def add_score(self, body: dict, auth: dict) -> dict:
        with self.lock:
            record = genai.score_record(body, auth, len(self.langfuse_scores) + 1, time.time())
            self.langfuse_scores.append(record)
        return record

    def add_prompt_fetch(self, name: str, selector: str, status: int, auth: dict) -> None:
        with self.lock:
            self.langfuse_prompt_fetches.append({"seq": len(self.langfuse_prompt_fetches) + 1, "name": name,
                                                 "selector": selector, "status": status, "auth": auth,
                                                 "received_at": time.time()})

    def add_dataset_fetch(self, name: str, selector: str, status: int, auth: dict) -> None:
        with self.lock:
            self.langfuse_dataset_fetches.append({"seq": len(self.langfuse_dataset_fetches) + 1, "name": name,
                                                  "selector": selector, "status": status, "auth": auth,
                                                  "received_at": time.time()})

    def add_media(self, content_type: str, length: int, auth: dict) -> None:
        with self.lock:
            self.langfuse_media.append({"seq": len(self.langfuse_media) + 1, "content_type": content_type,
                                        "bytes": length, "auth": auth, "received_at": time.time()})

    def replay_token_ok(self, value: str | None) -> bool | None:
        """Whether x-oneuptime-token equals the expected token; None when none was set."""
        if not self.expected_token:
            return None
        return value is not None and value.strip() == self.expected_token

    def add_replay_upload(self, frames: list, header_names, token_ok: bool | None, app_identifier: bool,
                          content_type: str) -> list[ReplayChunk]:
        with self.lock:
            self._upload_seq += 1
            chunks = [ReplayChunk(len(self.replay_chunks) + i + 1, self._upload_seq, i, frame.envelope, frame.events,
                                  sorted(set(header_names)), token_ok, app_identifier, content_type, time.time(), frame.note)
                      for i, frame in enumerate(frames)]
            self.replay_chunks.extend(chunks)
        return chunks

    def add_replay_request(self, method: str, path: str, status: int, header_names, token_ok: bool | None,
                           app_identifier: bool, frames: int = 0) -> None:
        with self.lock:
            self.replay_requests.append({
                "seq": len(self.replay_requests) + 1, "method": method, "path": path, "status": status,
                "header_names": sorted(set(header_names)), "token_ok": token_ok, "app_identifier": app_identifier,
                "frames": frames, "received_at": time.time()})

    def next_profile_seq(self, count: int = 1) -> int:
        with self.lock:
            first = self._profile_seq + 1
            self._profile_seq += count
            return first

    def add_profiles(self, uploads: list[ProfileUpload]) -> None:
        with self.lock:
            self.profiles.extend(uploads)

    def reject(self, sink: str, path: str, reason: str) -> None:
        with self.lock:
            self.rejected.append({"sink": sink, "path": path, "reason": reason, "at": time.time()})


# Servers


def _otlp_servicer(base, signal: str, store: Store):
    class Servicer(base):
        def Export(self, request, context):  # noqa: N802 (gRPC method name)
            metadata = list(context.invocation_metadata())
            names = [key for key, _ in metadata]
            store.add_otlp(signal, "grpc", request, request.SerializeToString(), "application/grpc", names,
                           token_headers(metadata, store.expected_token))
            return RESPONSES[signal]()

    return Servicer()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store: Store = None  # set per server class

    def log_message(self, *args) -> None:  # keep the receiver output quiet
        pass

    def reply(self, status: int, body: bytes = b"", content_type: str = "text/plain", cors: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


CORS_HEADERS = f"content-type, {replay.TOKEN_HEADER}, {replay.APP_HEADER}, x-oneuptime-user-ref"


class OtlpHttpHandler(_Handler):
    """POST /v1/{traces,metrics,logs} with protobuf or JSON bodies, gzip accepted; the replay stand-in."""

    assets: replay.ReplayAssets | None = None  # set per server class

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/")
        sink = "replay" if path.startswith(replay.PREFIX) else "langfuse" if path.startswith("/api/public") else "otlp-http"
        try:
            body = read_body(self)
        except (OSError, ValueError, EOFError, zlib.error):
            self.store.reject(sink, path, "unreadable body")
            return self.reply(400, b"unreadable body")
        if sink == "replay":
            return self._replay_post(path, body)
        destination, auth = "ops", None
        if sink == "langfuse":
            auth = genai.auth_facts(self.headers, self.store.expected_langfuse_basic)
            if path != genai.LANGFUSE_TRACES:
                return self._langfuse_post(path, body, auth)
            destination, signal = "langfuse", "traces"
        else:
            signal = HTTP_PATHS.get(path)
        if signal is None:
            self.store.reject("otlp-http", path, "unknown path")
            return self.reply(404, b"unknown path")
        content_type = self.headers.get("Content-Type", "application/x-protobuf")
        try:
            message = decode_otlp(signal, body, content_type)
        except Exception as exc:  # any decode failure is the client's
            self.store.reject(sink, path, f"decode failed ({type(exc).__name__})")
            return self.reply(400, b"cannot decode payload")
        self.store.add_otlp(signal, "http", message, body, content_type, list(self.headers.keys()),
                            token_headers(self.headers.items(), self.store.expected_token), destination, auth)
        if "json" in content_type.lower():
            self.reply(200, b"{}", "application/json")
        else:
            self.reply(200, RESPONSES[signal]().SerializeToString(), "application/x-protobuf")

    def _langfuse_post(self, path: str, body: bytes, auth: dict) -> None:
        """The Langfuse stand-in beside its OTLP path: scores, legacy batch ingestion, media."""
        if path.startswith(genai.LANGFUSE_OTEL):
            self.store.reject("langfuse", path, "Langfuse's OTLP endpoint takes traces only")
            return self.reply(404, b"traces only")
        try:
            doc = json.loads(body or b"{}")
        except ValueError:
            self.store.reject("langfuse", path, "not JSON")
            return self.reply(400, b"not JSON")
        if path == genai.LANGFUSE_SCORES and isinstance(doc, dict):
            record = self.store.add_score(doc, auth)
            return self.reply(200, json.dumps({"id": record["id"] or f"score-{record['seq']}"}).encode(), "application/json")
        if path == genai.LANGFUSE_INGESTION and isinstance(doc, dict):
            successes, errors = [], []
            for event in doc.get("batch") or []:
                if isinstance(event, dict) and event.get("type") == "score-create" and isinstance(event.get("body"), dict):
                    self.store.add_score(event["body"], auth)
                    successes.append({"id": event.get("id"), "status": 201})
                else:
                    self.store.reject("langfuse", path, f"legacy ingestion event {event.get('type') if isinstance(event, dict) else '?'}")
                    errors.append({"id": event.get("id") if isinstance(event, dict) else None, "status": 400,
                                   "message": "only score-create; send traces over OTLP"})
            return self.reply(207, json.dumps({"successes": successes, "errors": errors}).encode(), "application/json")
        if path == genai.LANGFUSE_MEDIA and isinstance(doc, dict):
            self.store.add_media(str(doc.get("contentType") or ""), int(doc.get("contentLength") or 0), auth)
            return self.reply(200, json.dumps({"mediaId": f"stand-in-{len(self.store.langfuse_media)}",
                                               "uploadUrl": None}).encode(), "application/json")
        self.store.reject("langfuse", path, "unknown Langfuse path")
        return self.reply(404, b"unknown path")

    def _langfuse_get(self, path: str, query: str) -> None:
        auth = genai.auth_facts(self.headers, self.store.expected_langfuse_basic)
        if path == genai.LANGFUSE_HEALTH:
            return self.reply(200, b'{"status":"OK","version":"stand-in"}', "application/json")
        if path == genai.LANGFUSE_PROJECTS:
            return self.reply(200, json.dumps({"data": [{"id": "stand-in-project", "name": "stand-in", "metadata": {},
                                                          "organization": {"id": "stand-in-org", "name": "stand-in"}}]}).encode(),
                              "application/json")
        if path.startswith(genai.LANGFUSE_PROMPTS):
            name = unquote(path[len(genai.LANGFUSE_PROMPTS):])
            params = parse_qs(query)
            label, version = (params.get("label") or [None])[0], (params.get("version") or [None])[0]
            selector = f"version {version}" if version else f"label {label or 'production'}"
            prompt = genai.find_prompt(self.store.langfuse_prompts, name, label, version)
            status = 200 if prompt else 404
            self.store.add_prompt_fetch(name, selector, status, auth)
            if prompt is None:
                return self.reply(404, b'{"message":"prompt not found"}', "application/json")
            return self.reply(200, json.dumps(prompt).encode(), "application/json")
        if path.startswith(genai.LANGFUSE_DATASETS) or path == genai.LANGFUSE_DATASET_ITEMS:
            return self._dataset_get(path, parse_qs(query), auth)
        self.store.reject("langfuse", path, "unknown Langfuse path")
        return self.reply(404, b"unknown path")

    def _dataset_get(self, path: str, params: dict, auth: dict) -> None:
        """A dataset, or a page of its items, from --langfuse-datasets, as the SDKs' get_dataset reads them."""
        items = path == genai.LANGFUSE_DATASET_ITEMS
        name = (params.get("datasetName") or [""])[0] if items else unquote(path[len(genai.LANGFUSE_DATASETS):])
        dataset = genai.find_dataset(self.store.langfuse_datasets, name)
        try:
            page, limit = int((params.get("page") or ["1"])[0]), int((params.get("limit") or ["50"])[0])
        except ValueError:
            page, limit = 1, 50
        self.store.add_dataset_fetch(name, f"items page {page}" if items else "dataset", 200 if dataset else 404, auth)
        if dataset is None:
            return self.reply(404, b'{"message":"dataset not found"}', "application/json")
        body = genai.dataset_items_page(dataset, page, limit) if items else genai.dataset_record(dataset)
        return self.reply(200, json.dumps(body).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        path = url.path
        if path.startswith("/api/public"):
            return self._langfuse_get(path.rstrip("/"), url.query)
        if not path.startswith(replay.PREFIX):
            return self.reply(404, b"unknown path")
        assets = self.assets
        if path in (replay.LOADER, replay.RECORDER):
            if assets is None or not assets.ok:
                status, body, content_type = 404, b"replay assets not configured", "text/plain"
            else:
                body = assets.loader if path == replay.LOADER else assets.recorder
                status, content_type = 200, "application/javascript; charset=utf-8"
        elif path == replay.CONFIG:
            status, body, content_type = 200, json.dumps(replay.policy(assets)).encode(), "application/json"
        elif path == replay.VALIDATE:
            status, body, content_type = 200, b'{"valid":true}', "application/json"
        else:
            self.store.reject("replay", path, "unknown path")
            status, body, content_type = 404, b"unknown path", "text/plain"
        self._record_replay("GET", path, status)
        self.reply(status, body, content_type, cors=True)

    def do_OPTIONS(self) -> None:  # noqa: N802 (CORS preflight from a browser that calls the sink directly)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", CORS_HEADERS)
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _record_replay(self, method: str, path: str, status: int, frames: int = 0) -> None:
        self.store.add_replay_request(method, path, status, list(self.headers.keys()),
                                      self.store.replay_token_ok(self.headers.get(replay.TOKEN_HEADER)),
                                      self.headers.get(replay.APP_HEADER) is not None, frames)

    def _replay_post(self, path: str, body: bytes) -> None:
        if path != replay.CHUNK:
            self.store.reject("replay", path, "unknown path")
            self._record_replay("POST", path, 404)
            return self.reply(404, b"unknown path", cors=True)
        try:
            frames = replay.parse_chunk_body(body)
        except replay.ChunkError as exc:
            self.store.reject("replay", path, f"malformed chunk: {exc}")
            self._record_replay("POST", path, 400)
            return self.reply(400, json.dumps({"error": "malformed chunk"}).encode(), "application/json", cors=True)
        self.store.add_replay_upload(frames, list(self.headers.keys()),
                                     self.store.replay_token_ok(self.headers.get(replay.TOKEN_HEADER)),
                                     self.headers.get(replay.APP_HEADER) is not None,
                                     self.headers.get("Content-Type", ""))
        self._record_replay("POST", path, 202, len(frames))
        self.reply(202, json.dumps(replay.CHUNK_REPLY).encode(), "application/json", cors=True)


class PyroscopeHandler(_Handler):
    """POST /ingest (Pyroscope ingest API) and the connect-protocol Push API."""

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        try:
            body = read_body(self)
        except (OSError, ValueError, EOFError, zlib.error):
            self.store.reject("pyroscope", url.path, "unreadable body")
            return self.reply(400, b"unreadable body")
        content_type = self.headers.get("Content-Type", "")
        if url.path == "/ingest":
            self.store.add_profiles([ingest_upload(self.store.next_profile_seq(), url.query, content_type, body)])
            return self.reply(200)
        if url.path == PUSH_PATH:
            try:
                series = decode_push_body(content_type, body)
                uploads = push_uploads(self.store.next_profile_seq(max(1, len(series))), series, content_type, body)
            except Exception as exc:
                self.store.reject("pyroscope", url.path, f"push request not decoded ({type(exc).__name__})")
                return self.reply(400, b"cannot decode push request")
            self.store.add_profiles(uploads)
            if "json" in content_type.lower():
                return self.reply(200, b"{}", "application/json")
            return self.reply(200, b"", "application/proto")
        self.store.reject("pyroscope", url.path, "unsupported path")
        self.reply(404, b"unsupported path")

    def do_GET(self) -> None:  # noqa: N802
        self.reply(404, b"")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:  # skip HTTPServer's reverse DNS lookup
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]

    def handle_error(self, request, client_address) -> None:
        """A client that hangs up between requests is normal for exporters and test clients; print anything else."""
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class _Server6(_Server):
    address_family = socket.AF_INET6


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True when something accepts connections on host:port."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


class Sinks:
    """The three telemetry sinks on 127.0.0.1 (and ::1 when the ports are fixed); replay on the OTLP/HTTP one."""

    def __init__(self, store: Store, grpc_port: int = 4317, http_port: int = 4318, pyroscope_port: int = 4040,
                 replay_assets: replay.ReplayAssets | None = None):
        self.store = store
        self.ports = {"otlp_grpc": grpc_port, "otlp_http": http_port, "pyroscope": pyroscope_port}
        self.replay_assets = replay_assets
        self._grpc = None
        self._http: list[ThreadingHTTPServer] = []

    def start(self) -> None:
        fixed = [p for p in self.ports.values() if p]
        for port in fixed:
            if port_open(port) or port_open(port, "::1"):
                raise PortBusyError(f"port {port} is already in use")
        try:
            self._start_grpc()
            self._start_http("otlp_http", OtlpHttpHandler)
            self._start_http("pyroscope", PyroscopeHandler)
        except OSError as exc:
            self.stop()
            raise PortBusyError(f"cannot bind a telemetry port: {exc}") from None

    def _start_grpc(self) -> None:
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=16),
            options=[("grpc.so_reuseport", 0), ("grpc.max_receive_message_length", 64 * 1024 * 1024)],
        )
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            _otlp_servicer(trace_service_pb2_grpc.TraceServiceServicer, "traces", self.store), server)
        metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(
            _otlp_servicer(metrics_service_pb2_grpc.MetricsServiceServicer, "metrics", self.store), server)
        logs_service_pb2_grpc.add_LogsServiceServicer_to_server(
            _otlp_servicer(logs_service_pb2_grpc.LogsServiceServicer, "logs", self.store), server)
        requested = self.ports["otlp_grpc"]
        try:
            bound = server.add_insecure_port(f"127.0.0.1:{requested}")
        except RuntimeError:
            bound = 0
        if not bound:
            raise OSError(f"gRPC sink could not bind 127.0.0.1:{requested}")
        self.ports["otlp_grpc"] = bound
        if requested:  # also on ::1, for apps that resolve localhost to IPv6
            try:
                server.add_insecure_port(f"[::1]:{bound}")
            except RuntimeError:
                pass
        server.start()
        self._grpc = server

    def _start_http(self, key: str, handler: type) -> None:
        handler_class = type(handler.__name__, (handler,), {"store": self.store, "assets": self.replay_assets})
        requested = self.ports[key]
        server = _Server(("127.0.0.1", requested), handler_class)
        self.ports[key] = server.server_address[1]
        servers = [server]
        if requested:
            try:
                servers.append(_Server6(("::1", requested), handler_class))
            except OSError:
                pass
        for srv in servers:
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
            self._http.append(srv)

    def stop(self) -> None:
        if self._grpc is not None:
            self._grpc.stop(grace=1).wait()
            self._grpc = None
        for server in self._http:
            server.shutdown()
            server.server_close()
        self._http = []

    def ports_closed(self) -> bool:
        return not any(port_open(p) or port_open(p, "::1") for p in self.ports.values())
