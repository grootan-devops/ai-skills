#!/usr/bin/env python3
"""Local telemetry receiver: check what an app really exports (references/acceptance.md, "Local receiver").

uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml \
  scripts/receive.py --out <dir> [--grpc-port N] [--http-port N] [--pyroscope-port N] [--seconds N]
                     [--replay-assets DIR] [--langfuse-keys FILE] [--langfuse-prompts FILE]
                     [--langfuse-datasets FILE] [--genai-content-destination langfuse|ops] [--canaries FILE]

Starts OTLP/gRPC, OTLP/HTTP and Pyroscope receivers on 127.0.0.1, on free ports unless ports are
given, and prints their endpoints as one JSON line. The OTLP/HTTP port also serves the
session-replay stand-in (OneUptime protocol; the recorder needs --replay-assets or
OBS_RECEIVER_REPLAY_ASSETS) and a Langfuse stand-in (base URL = the OTLP/HTTP endpoint): traces on
/api/public/otel/v1/traces, kept apart from the ops stream; scores on /api/public/scores and in
legacy batch ingestion; prompts from --langfuse-prompts (a JSON list of prompts); datasets for
experiment runners from --langfuse-datasets (a JSON list of {"name", "items": [{"input",
"expectedOutput", "metadata"}]}); auth compared with --langfuse-keys (a file holding
`pk-lf-...:sk-lf-...`) and recorded as booleans. --genai-content-destination names where the
service sends AI content (langfuse, the default, or ops), which decides where the summary expects
it. While it runs it keeps <dir> up to date:

  <dir>/otlp/NNNN-<signal>-<transport>.json   every decoded export request
  <dir>/profiles/NNNN.bin, uploads.json       profile uploads and their labels
  <dir>/replay/chunks.json, requests.json     replay chunk metadata (never event content) and requests
  <dir>/langfuse/scores.json, prompts.json,   scores (no text values), prompt and dataset fetches
    datasets.json, media.json                   and media upload attempts the Langfuse stand-in saw
  <dir>/canaries.json                         with --canaries: each canary's label and the streams
                                              it reached (OTLP, Langfuse, replay events and requests,
                                              profiles, rejected requests); never the values
  <dir>/summary.md                            resources, span trees, metrics, log records,
                                              profiles, session replay, LLM observability
                                              (keys and sizes only) and rejected requests

Stops on SIGINT or SIGTERM, or after --seconds. Header names are recorded, never their values.
Exit code 0, or 3 when a requested port is busy.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import threading
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from receiver import canaries as cn  # noqa: E402
from receiver import genai, replay  # noqa: E402
from receiver import telemetry as tm  # noqa: E402
from receiver.sink import PortBusyError, Sinks, Store  # noqa: E402


def chunk_metadata(chunk) -> dict:
    """A replay chunk without its events: the envelope, counts and headers."""
    return {"seq": chunk.seq, "upload": chunk.upload, "frame": chunk.frame, "session_id": chunk.session_id,
            "chunk_index": chunk.envelope.get("chunkIndex"), "is_final": chunk.is_final,
            "has_full_snapshot": replay.has_full_snapshot(chunk.envelope, chunk.events),
            "events": chunk.event_count if chunk.events is not None else None,
            "event_types": replay.event_type_counts(chunk.events), "envelope": chunk.envelope,
            "header_names": chunk.header_names, "token_ok": chunk.token_ok, "app_identifier": chunk.app_identifier,
            "content_type": chunk.content_type, "received_at": chunk.received_at, "note": chunk.note}

SPAN_KEYS = ("http.request.method", "http.route", "http.response.status_code", "url.full", "url.template",
             "server.address", "server.port", "service.peer.name", "code.function.name", "error.type",
             "db.system.name", "db.operation.name", "user.id", "tenant.id", "correlation.id",
             "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model", "gen_ai.tool.name",
             "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "langfuse.observation.type",
             "langfuse.observation.level")
RESOURCE_KEYS = ("service.name", "service.version", "deployment.environment.name")
SEVERITY = ((21, "FATAL"), (17, "ERROR"), (13, "WARN"), (9, "INFO"), (5, "DEBUG"), (1, "TRACE"))
MAX_TRACES, MAX_RECORDS = 60, 200


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _short(value, limit: int = 160) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


def _severity(number: int | None) -> str:
    return next((name for floor, name in SEVERITY if number and number >= floor), "UNSET")


def _span_line(span: tm.Span) -> str:
    attrs = " ".join(f"{k}={_short(span.attrs[k], 120)}" for k in SPAN_KEYS if k in span.attrs)
    status = "" if span.status == "UNSET" else f" [{span.status}]"
    return f"{span.kind} {span.name!r}{status} {attrs}".rstrip()


def _job_root(span: tm.Span) -> bool:
    """A job run's root span (references/workloads/batch.md): `job {name}` carrying `app.job.name`."""
    return span.kind == "INTERNAL" and span.name.startswith("job ") and bool(span.attrs.get("app.job.name"))


def render_traces(spans: list[tm.Span], title: str = "Traces") -> list[str]:
    by_trace: dict[str, list[tm.Span]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)
    lines = [f"## {title} ({len(by_trace)} traces, {len(spans)} spans)", ""]
    ordered = sorted(by_trace.items(), key=lambda item: min(s.start_ns for s in item[1]))
    for trace_id, members in ordered[:MAX_TRACES]:
        ids = {s.span_id for s in members}
        children: dict[str, list[tm.Span]] = defaultdict(list)
        for span in members:
            children[span.parent_id if span.parent_id in ids else ""].append(span)
        roots = sorted(children[""], key=lambda s: s.start_ns)
        notes = []
        if any(s.kind != "SERVER" and not s.parent_id and not _job_root(s) for s in roots):
            notes.append("root is not a SERVER span: work outside a request?")
        if any(s.parent_id for s in roots):
            notes.append("parent not received")
        lines.append(f"### {trace_id} ({len(members)} spans{'; ' + '; '.join(notes) if notes else ''})")

        def walk(span: tm.Span, depth: int) -> None:
            lines.append(f"{'  ' * depth}- {_span_line(span)}")
            for child in sorted(children[span.span_id], key=lambda s: s.start_ns):
                walk(child, depth + 1)

        for root in roots:
            walk(root, 0)
        lines.append("")
    if len(ordered) > MAX_TRACES:
        lines += [f"({len(ordered) - MAX_TRACES} more traces in otlp/)", ""]
    return lines


def render_metrics(points: list[tm.Point]) -> list[str]:
    groups: dict[tuple, dict] = {}
    for point in points:
        group = groups.setdefault((point.name, point.type, point.unit), {"keys": set(), "points": 0})
        group["keys"].update(point.attrs)
        group["points"] += 1
    lines = [f"## Metrics ({len(groups)} instruments, {len(points)} data points)", "",
             "| Name | Type | Unit | Attribute keys | Points |", "|---|---|---|---|---|"]
    for (name, kind, unit), group in sorted(groups.items()):
        lines.append(f"| {name} | {kind} | {unit or '-'} | {', '.join(sorted(group['keys'])) or '-'} | {group['points']} |")
    return lines + [""]


def render_logs(records: list[tm.Record]) -> list[str]:
    lines = [f"## Logs ({len(records)} records)", ""]
    for record in records[:MAX_RECORDS]:
        event = f" event={record.event_name}" if record.event_name else ""
        trace = "trace=yes" if record.trace_id.strip("0") else "trace=no"
        keys = ",".join(sorted(record.attrs)) or "-"
        lines.append(f"- {_severity(record.severity)}{event} {trace} {_short(record.body)!r} attrs: {keys}")
    if len(records) > MAX_RECORDS:
        lines.append(f"- ({len(records) - MAX_RECORDS} more in otlp/)")
    return lines + [""]


def render_replay(chunks: list, requests: list) -> list[str]:
    """Sessions, chunks, full snapshots, masking modes and event counts; never event content."""
    sessions: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        sessions[chunk.session_id or "-"].append(chunk)
    lines = [f"## Session replay ({len(sessions)} sessions, {len(chunks)} chunks)", ""]
    if requests:
        seen = Counter(f"{r['method']} {r['path']} {r['status']}" for r in requests)
        lines += ["Requests: " + "; ".join(f"{k} x{n}" for k, n in sorted(seen.items())), ""]
    else:
        lines += ["No replay requests.", ""]
    if chunks:
        token = sum(1 for c in chunks if replay.TOKEN_HEADER in c.header_names)
        app = sum(1 for c in chunks if c.app_identifier)
        lines += [f"Chunks with {replay.TOKEN_HEADER}: {token}/{len(chunks)}; with {replay.APP_HEADER}: {app}/{len(chunks)}", "",
                  "| Session | Chunks | Full snapshot | Final | Masking mode | Events (envelope / decoded) |",
                  "|---|---|---|---|---|---|"]
        for session, members in sessions.items():
            full = "yes" if any(replay.has_full_snapshot(c.envelope, c.events) for c in members) else "no"
            final = "yes" if any(c.is_final for c in members) else "no"
            modes = ", ".join(sorted({str(c.envelope.get("maskingMode") or "-") for c in members}))
            declared = sum(c.envelope.get("eventCount") or 0 for c in members if isinstance(c.envelope.get("eventCount"), int))
            decoded = sum(c.event_count for c in members)
            lines.append(f"| {session} | {len(members)} | {full} | {final} | {modes} | {declared} / {decoded} |")
        lines.append("")
    return lines


def render_summary(store: Store, canaries: dict[str, str] | None = None, content_destination: str = "langfuse") -> str:
    with store.lock:
        payloads, profiles, rejected = list(store.otlp), list(store.profiles), list(store.rejected)
        chunks, replay_requests = list(store.replay_chunks), list(store.replay_requests)
        scores, fetches, media = list(store.langfuse_scores), list(store.langfuse_prompt_fetches), list(store.langfuse_media)
        dataset_fetches = list(store.langfuse_dataset_fetches)
    langfuse = [p for p in payloads if p.destination == "langfuse"]
    payloads = [p for p in payloads if p.destination != "langfuse"]
    counts = Counter(f"{p.signal}/{p.transport}" for p in payloads)
    if langfuse:
        counts["traces/langfuse"] = len(langfuse)
    lines = ["# Received telemetry", "",
             "Export requests: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")
             + f"; profile uploads: {len(profiles)}; rejected: {len(rejected)}", ""]
    resources = Counter()
    for payload in payloads:
        for res in tm.resources(payload):
            resources[(payload.signal,) + tuple(str(res.get(k, "-")) for k in RESOURCE_KEYS)] += 1
    lines += ["## Resources", "", "| Signal | " + " | ".join(RESOURCE_KEYS) + " | Requests |",
              "|---|---|---|---|---|"]
    lines += [f"| {' | '.join(key)} | {count} |" for key, count in sorted(resources.items())] + [""]
    ops_spans, langfuse_spans = tm.spans(payloads), tm.spans(langfuse)
    lines += render_traces(ops_spans)
    if langfuse_spans:
        lines += render_traces(langfuse_spans, "Traces sent to Langfuse")
    lines += genai.summary(langfuse_spans, ops_spans, scores, fetches, media, langfuse, dataset_fetches,
                           content_destination)
    lines += render_metrics(tm.metric_points(payloads))
    lines += render_logs(tm.otlp_records(payloads))
    lines += [f"## Profiles ({len(profiles)} uploads)", ""]
    for upload in profiles:
        labels = "; ".join(f"{k}={','.join(map(str, v[:5]))}" for k, v in sorted(upload.sample_labels.items()))
        lines.append(f"- {upload.app_name or '-'} ({upload.source}/{upload.format or 'pprof'}) "
                     f"static labels: {upload.labels or '-'}; sample labels: {labels or '-'}")
    lines += ([""] if profiles else []) + render_replay(chunks, replay_requests)
    if rejected:
        lines += ["", "## Rejected requests", ""]
        lines += [f"- {r['sink']} {r['path']}: {r['reason']}" for r in rejected]
    if canaries:
        lines += ["", *cn.summary(cn.scan(store, canaries))]
    return "\n".join(lines) + "\n"


class Receiver:
    """The sinks plus a writer that mirrors what they receive into the output directory."""

    def __init__(self, out: Path, grpc_port: int = 0, http_port: int = 0, pyroscope_port: int = 0,
                 replay_assets: Path | None = None, langfuse_keys: Path | None = None,
                 langfuse_prompts: Path | None = None, langfuse_datasets: Path | None = None,
                 canaries: Path | None = None, content_destination: str = "langfuse"):
        self.out = out
        self.content_destination = content_destination  # the service's genai.content.destination
        self.store = Store()
        self.canaries = cn.load(canaries) if canaries else {}  # label -> value; values never leave memory
        if langfuse_keys:  # read once; only its base64 form is kept, for comparison
            import base64
            self.store.expected_langfuse_basic = base64.b64encode(
                langfuse_keys.read_text(encoding="utf-8").strip().encode()).decode()
        if langfuse_prompts:
            self.store.langfuse_prompts = json.loads(langfuse_prompts.read_text(encoding="utf-8"))
        if langfuse_datasets:
            self.store.langfuse_datasets = json.loads(langfuse_datasets.read_text(encoding="utf-8"))
        self.replay_assets = replay.ReplayAssets(replay.resolve_assets_dir(replay_assets))
        self.sinks = Sinks(self.store, grpc_port, http_port, pyroscope_port, replay_assets=self.replay_assets)
        self._payloads_written = 0
        self._profiles_written = 0
        self._rejected = 0
        self._replay_seen = 0
        self._langfuse_seen = 0

    def start(self) -> dict:
        for sub in ("otlp", "profiles", "replay", "langfuse"):
            shutil.rmtree(self.out / sub, ignore_errors=True)
        (self.out / "canaries.json").unlink(missing_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.sinks.start()
        self.flush(force=True)
        ports = self.sinks.ports
        return {"otlp_grpc": f"http://127.0.0.1:{ports['otlp_grpc']}",
                "otlp_http": f"http://127.0.0.1:{ports['otlp_http']}",
                "pyroscope": f"http://127.0.0.1:{ports['pyroscope']}",
                "langfuse": f"http://127.0.0.1:{ports['otlp_http']}",
                "summary": str(self.out / "summary.md")}

    def flush(self, force: bool = False) -> bool:
        """Write payloads that arrived since the last flush; rewrite the summary when anything did."""
        with self.store.lock:
            payloads = self.store.otlp[self._payloads_written:]
            profiles = list(self.store.profiles)
            rejected = len(self.store.rejected)
            chunks, replay_requests = list(self.store.replay_chunks), list(self.store.replay_requests)
            langfuse = {"scores": list(self.store.langfuse_scores), "prompts": list(self.store.langfuse_prompt_fetches),
                        "datasets": list(self.store.langfuse_dataset_fetches), "media": list(self.store.langfuse_media)}
        new_profiles = profiles[self._profiles_written:]
        replay_seen = len(chunks) + len(replay_requests)
        langfuse_seen = sum(len(v) for v in langfuse.values())
        if not (force or payloads or new_profiles or rejected != self._rejected or replay_seen != self._replay_seen
                or langfuse_seen != self._langfuse_seen):
            return False
        if langfuse_seen:
            for name, items in langfuse.items():
                _write_json(self.out / "langfuse" / f"{name}.json", items)
        self._langfuse_seen = langfuse_seen
        if replay_requests:
            _write_json(self.out / "replay" / "chunks.json", [chunk_metadata(c) for c in chunks])
            _write_json(self.out / "replay" / "requests.json", replay_requests)
        self._replay_seen = replay_seen
        for payload in payloads:
            _write_json(self.out / payload.name, {
                "signal": payload.signal, "transport": payload.transport, "destination": payload.destination,
                "auth": payload.auth, "content_type": payload.content_type,
                "header_names": payload.header_names, "received_at": payload.received_at, "payload": payload.data})
        for upload in new_profiles:
            (self.out / upload.name).parent.mkdir(parents=True, exist_ok=True)
            (self.out / upload.name).write_bytes(upload.raw)
        if new_profiles:
            meta = []
            for upload in profiles:
                info = asdict(upload)
                info.pop("raw")
                info["body_file"], info["body_bytes"] = upload.name, len(upload.raw)
                meta.append(info)
            _write_json(self.out / "profiles" / "uploads.json", meta)
        self._payloads_written += len(payloads)
        self._profiles_written = len(profiles)
        self._rejected = rejected
        if self.canaries:
            _write_json(self.out / "canaries.json", cn.scan(self.store, self.canaries))
        (self.out / "summary.md").write_text(render_summary(self.store, self.canaries, self.content_destination),
                                             encoding="utf-8")
        return True

    def stop(self) -> None:
        self.sinks.stop()
        self.flush(force=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Receive an app's OTLP and Pyroscope exports locally and summarise them.")
    parser.add_argument("--out", type=Path, required=True, help="directory for the received payloads and summary.md")
    parser.add_argument("--grpc-port", type=int, default=0, help="OTLP/gRPC port (default: a free port)")
    parser.add_argument("--http-port", type=int, default=0, help="OTLP/HTTP port (default: a free port)")
    parser.add_argument("--pyroscope-port", type=int, default=0, help="Pyroscope port (default: a free port)")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (default: until SIGINT/SIGTERM)")
    parser.add_argument("--replay-assets", type=Path,
                        help="folder with the OneUptime session-replay loader.js, recorder.js and manifest.json "
                             f"(default: ${replay.ASSETS_ENV})")
    parser.add_argument("--langfuse-keys", type=Path,
                        help="file holding the expected `pk-lf-...:sk-lf-...` pair; requests are compared, never stored")
    parser.add_argument("--langfuse-prompts", type=Path, help="JSON list of prompts the Langfuse stand-in serves")
    parser.add_argument("--langfuse-datasets", type=Path,
                        help="JSON list of datasets (name, items with input, expectedOutput, metadata) for experiment runners")
    parser.add_argument("--genai-content-destination", choices=genai.DESTINATIONS, default="langfuse",
                        help="the service's genai.content.destination: where content may appear (default: langfuse)")
    parser.add_argument("--canaries", type=Path,
                        help="JSON {label: value} (or a list) of test values that must reach no stream; "
                             "the summary and canaries.json name the labels and streams, never the values")
    args = parser.parse_args(argv)
    receiver = Receiver(args.out.resolve(), args.grpc_port, args.http_port, args.pyroscope_port, args.replay_assets,
                        args.langfuse_keys, args.langfuse_prompts, args.langfuse_datasets, args.canaries,
                        args.genai_content_destination)
    try:
        endpoints = receiver.start()
    except PortBusyError as exc:
        print(f"receiver error: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(endpoints), flush=True)
    if args.replay_assets and not receiver.replay_assets.ok:
        print(f"receiver warning: {receiver.replay_assets.error}; the replay recorder routes answer 404", file=sys.stderr)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    deadline = args.seconds or None
    waited = 0.0
    while not stop.wait(0.5):
        receiver.flush()
        waited += 0.5
        if deadline and waited >= deadline:
            break
    receiver.stop()
    with receiver.store.lock:
        total, uploads, chunks = len(receiver.store.otlp), len(receiver.store.profiles), len(receiver.store.replay_chunks)
    print(f"received {total} export requests, {uploads} profile uploads and {chunks} replay chunks; "
          f"summary in {receiver.out / 'summary.md'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
