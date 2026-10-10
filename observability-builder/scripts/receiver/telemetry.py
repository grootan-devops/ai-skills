"""Flat views over captured telemetry: spans, log records, metric points, stdout lines, the scrape."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime

from opentelemetry.proto.logs.v1 import logs_pb2

LEVELS = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "WARNING": 13, "ERROR": 17, "FATAL": 21, "CRITICAL": 21}

# The standard's plain line format (references/signals/app-logs.md) and its multiline rule.
PLAIN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) (?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL) "
    r"(?P<trace_id>[0-9a-f]{32}|-) (?P<span_id>[0-9a-f]{16}|-) (?P<trace_flags>[0-9a-f]{2}|-) (?P<logger>\S+) "
    r'"(?P<message>(?:[^"\\]|\\.)*)"(?: (?P<attributes>.*))?$'
)
RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def any_value(value: dict | None):
    """An OTLP AnyValue (MessageToDict form) as a Python value."""
    if not value:
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "arrayValue" in value:
        return [any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return attributes(value["kvlistValue"].get("values"))
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def attributes(items: list | None) -> dict:
    return {kv.get("key", ""): any_value(kv.get("value")) for kv in items or []}


def as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def severity_number(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value or None
    try:
        return logs_pb2.SeverityNumber.Value(value) or None
    except ValueError:
        return as_int(value) or None


@dataclass
class Span:
    name: str
    kind: str  # SERVER | CLIENT | INTERNAL | PRODUCER | CONSUMER | UNSPECIFIED
    trace_id: str
    span_id: str
    parent_id: str
    attrs: dict
    status: str  # UNSET | OK | ERROR
    start_ns: int
    end_ns: int
    resource: dict
    file: str
    scope: str = ""  # instrumentation scope name
    events: list = field(default_factory=list)  # {"name", "time_ns", "attrs"}
    status_message: str = ""  # the status description


@dataclass
class Record:
    """A log record from OTLP or a stdout line, in one shape."""

    source: str  # otlp | stdout
    location: str
    severity: int | None
    body: str
    event_name: str
    trace_id: str
    span_id: str
    attrs: dict
    time_ns: int = 0
    resource: dict = field(default_factory=dict)


@dataclass
class Point:
    name: str
    unit: str
    type: str  # sum | gauge | histogram | exponentialHistogram | summary
    attrs: dict
    value: float | None
    resource: dict
    file: str


@dataclass
class Sample:
    """One line of the /metrics scrape."""

    name: str
    labels: dict
    value: float
    line: int


RESOURCE_KEYS = {"traces": "resourceSpans", "metrics": "resourceMetrics", "logs": "resourceLogs"}


def resources(payload) -> list[dict]:
    return [attributes((r.get("resource") or {}).get("attributes")) for r in payload.data.get(RESOURCE_KEYS[payload.signal], [])]


def is_browser_resource(res: dict) -> bool:
    """RUM is classified by the browser.* resource attributes (or the user agent)."""
    return "user_agent.original" in res or any(str(key).startswith("browser.") for key in res)


def split_browser(payloads) -> tuple[list, list]:
    """(server payloads, browser payloads), split per resource; a mixed payload yields one of each."""
    server, browser = [], []
    for payload in payloads:
        key = RESOURCE_KEYS[payload.signal]
        groups = payload.data.get(key, [])
        flags = [is_browser_resource(attributes((g.get("resource") or {}).get("attributes"))) for g in groups]
        if not any(flags):
            server.append(payload)
        elif all(flags):
            browser.append(payload)
        else:  # never seen in practice: the browser's proxy forwards the browser's own requests
            for want, out in ((False, server), (True, browser)):
                data = {**payload.data, key: [g for g, flag in zip(groups, flags) if flag == want]}
                out.append(replace(payload, data=data, raw=json.dumps(data).encode()))
    return server, browser


def spans(payloads) -> list[Span]:
    out = []
    for payload in payloads:
        if payload.signal != "traces":
            continue
        for rs in payload.data.get("resourceSpans", []):
            resource = attributes((rs.get("resource") or {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                scope = str((ss.get("scope") or {}).get("name") or "")
                for sp in ss.get("spans", []):
                    out.append(Span(
                        name=sp.get("name", ""),
                        kind=str(sp.get("kind", "SPAN_KIND_UNSPECIFIED")).replace("SPAN_KIND_", ""),
                        trace_id=sp.get("traceId", ""),
                        span_id=sp.get("spanId", ""),
                        parent_id=sp.get("parentSpanId", ""),
                        attrs=attributes(sp.get("attributes")),
                        status=str((sp.get("status") or {}).get("code", "STATUS_CODE_UNSET")).replace("STATUS_CODE_", ""),
                        status_message=str((sp.get("status") or {}).get("message") or ""),
                        start_ns=as_int(sp.get("startTimeUnixNano")),
                        end_ns=as_int(sp.get("endTimeUnixNano")),
                        resource=resource,
                        file=payload.name,
                        scope=scope,
                        events=[{"name": ev.get("name", ""), "time_ns": as_int(ev.get("timeUnixNano")),
                                 "attrs": attributes(ev.get("attributes"))} for ev in sp.get("events", [])],
                    ))
    return out


def otlp_records(payloads) -> list[Record]:
    out = []
    for payload in payloads:
        if payload.signal != "logs":
            continue
        index = 0
        for rl in payload.data.get("resourceLogs", []):
            resource = attributes((rl.get("resource") or {}).get("attributes"))
            for sl in rl.get("scopeLogs", []):
                for lr in sl.get("logRecords", []):
                    attrs = attributes(lr.get("attributes"))
                    body = any_value(lr.get("body"))
                    text = (lr.get("severityText") or "").upper()
                    out.append(Record(
                        source="otlp",
                        location=f"{payload.name} record {index}",
                        severity=severity_number(lr.get("severityNumber")) or LEVELS.get(text),
                        body=body if isinstance(body, str) else (json.dumps(body) if body is not None else ""),
                        event_name=lr.get("eventName") or str(attrs.get("event.name") or ""),
                        trace_id=(lr.get("traceId") or "").lower(),
                        span_id=(lr.get("spanId") or "").lower(),
                        attrs=attrs,
                        time_ns=as_int(lr.get("timeUnixNano")) or as_int(lr.get("observedTimeUnixNano")),
                        resource=resource,
                    ))
                    index += 1
    return out


def metric_points(payloads) -> list[Point]:
    out = []
    for payload in payloads:
        if payload.signal != "metrics":
            continue
        for rm in payload.data.get("resourceMetrics", []):
            resource = attributes((rm.get("resource") or {}).get("attributes"))
            for sm in rm.get("scopeMetrics", []):
                for metric in sm.get("metrics", []):
                    for kind in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
                        if kind not in metric:
                            continue
                        for dp in metric[kind].get("dataPoints", []):
                            if kind in ("sum", "gauge"):
                                value = number(dp.get("asInt", dp.get("asDouble")))
                            else:
                                value = number(dp.get("count", 0))
                            out.append(Point(metric.get("name", ""), metric.get("unit", ""), kind,
                                             attributes(dp.get("attributes")), value, resource, payload.name))
    return out


# stdout


def _flatten(doc: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in doc.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, name + "."))
        else:
            out[name] = value
    return out


def _time_ns(text) -> int:
    if not isinstance(text, str) or not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return 0


def _hex(value) -> str:
    text = str(value or "").strip().lower()
    return "" if text == "-" else text


def logfmt(text: str) -> dict:
    """key=value pairs; quoted values may contain spaces and backslash escapes."""
    out, i, n = {}, 0, len(text)
    while i < n:
        while i < n and text[i] == " ":
            i += 1
        if i >= n:
            break
        start = i
        while i < n and text[i] not in "= ":
            i += 1
        key = text[start:i]
        value: object = ""
        if i < n and text[i] == "=":
            i += 1
            if i < n and text[i] == '"':
                i += 1
                chars = []
                while i < n and text[i] != '"':
                    if text[i] == "\\" and i + 1 < n:
                        i += 1
                    chars.append(text[i])
                    i += 1
                i += 1
                value = "".join(chars)
            else:
                start = i
                while i < n and text[i] != " ":
                    i += 1
                raw = text[start:i]
                value = int(raw) if re.fullmatch(r"-?\d+", raw) else (float(raw) if re.fullmatch(r"-?\d+\.\d+(e-?\d+)?", raw) else raw)
        if key:
            out[key] = value
    return out


def _json_record(doc: dict, name: str, line: int) -> Record:
    flat = _flatten(doc)
    level = str(flat.pop("level", "")).upper()
    message = flat.pop("message", "")
    flat.pop("logger", None)
    trace_id, span_id = _hex(flat.pop("trace_id", "")), _hex(flat.pop("span_id", ""))
    flat.pop("trace_flags", None)
    event = flat.pop("event_name", "") or flat.get("event.name", "")
    time_ns = _time_ns(flat.pop("time", ""))
    return Record("stdout", f"{name} line {line}", LEVELS.get(level), str(message), str(event or ""),
                  trace_id, span_id, flat, time_ns)


def _plain_record(match: re.Match, name: str, line: int) -> Record:
    attrs = logfmt(match.group("attributes") or "")
    event = attrs.pop("event_name", "") or attrs.get("event.name", "")
    message = re.sub(r"\\(.)", r"\1", match.group("message"))
    return Record("stdout", f"{name} line {line}", LEVELS.get(match.group("level")), message, str(event or ""),
                  _hex(match.group("trace_id")), _hex(match.group("span_id")), attrs, _time_ns(match.group("time")))


def parse_stdout(text: str, name: str = "stdout.txt") -> list[Record]:
    """Records from json lines and plain lines (with stack-trace continuation lines)."""
    records, current = [], None
    for number_, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                doc = json.loads(stripped)
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                records.append(_json_record(doc, name, number_))
                current = None
                continue
        match = PLAIN.match(line)
        if match:
            current = _plain_record(match, name, number_)
            records.append(current)
        elif current is not None and not RECORD_START.match(line):
            trace = current.attrs.get("exception.stacktrace")
            current.attrs["exception.stacktrace"] = f"{trace}\n{line}" if trace else line
        else:
            current = None
    return records


# /metrics

_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')


def parse_scrape(text: str) -> list[Sample]:
    """Samples of a Prometheus or OpenMetrics exposition."""
    out = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        match = re.match(r"[a-zA-Z_:][a-zA-Z0-9_:]*", line)
        if not match:
            continue
        rest, labels = line[match.end():], {}
        if rest.startswith("{"):
            i, quoted, escaped = 1, False, False
            while i < len(rest):
                ch = rest[i]
                if quoted:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        quoted = False
                elif ch == '"':
                    quoted = True
                elif ch == "}":
                    break
                i += 1
            labels = {k: re.sub(r"\\(.)", r"\1", v) for k, v in _LABEL.findall(rest[1:i])}
            rest = rest[i + 1:]
        tokens = rest.split("#", 1)[0].split()  # value, then an optional timestamp or exemplar
        value = number(tokens[0]) if tokens else None
        if value is not None:
            out.append(Sample(match.group(0), labels, value, line_no))
    return out


# HTTP attribute helpers, stable names first and the older ones as a fallback.


def http_method(attrs: dict) -> str:
    return str(attrs.get("http.request.method") or attrs.get("http.method") or "").upper()


def http_status(attrs: dict) -> int | None:
    value = attrs.get("http.response.status_code", attrs.get("http.status_code"))
    return as_int(value, None) if value is not None else None


def url_path(attrs: dict) -> str:
    path = attrs.get("url.path") or attrs.get("http.target") or ""
    return str(path).split("?", 1)[0]
