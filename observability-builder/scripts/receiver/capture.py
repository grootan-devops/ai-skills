"""What one receiver run captured."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Exchange:
    """One scenario request and the app's response."""

    step: str
    method: str
    path: str  # path and query as sent; the query may hold a canary
    expected: int
    request_headers: dict = field(default_factory=dict)
    status: int | None = None
    headers: dict = field(default_factory=dict)
    body: str = ""
    error: str | None = None
    started_ns: int = 0
    ended_ns: int = 0

    @property
    def path_only(self) -> str:
        return self.path.split("?", 1)[0]

    @property
    def label(self) -> str:
        return f"#{self.step} {self.method} {self.path_only}"

    @property
    def ok(self) -> bool:
        return self.status == self.expected

    @property
    def seconds(self) -> float:
        return max(0, self.ended_ns - self.started_ns) / 1e9


@dataclass
class RunResult:
    """One start-drive-stop cycle of the app."""

    run: int
    attempted: bool = False
    skipped: str | None = None  # why the run didn't happen
    app_port: int | None = None
    prometheus_port: int | None = None
    start_error: str | None = None
    ready: bool = False
    ready_seconds: float | None = None
    ready_error: str | None = None
    exchanges: list[Exchange] = field(default_factory=list)
    scrape: dict | None = None
    exited_before_sigterm: bool = False
    exit_code: int | None = None
    exit_seconds: float | None = None
    killed: bool = False
    stdout_name: str = "stdout.txt"
    stdout_text: str = ""
    stderr_name: str = "stderr.txt"
    stderr_text: str = ""
    telemetry_ports_closed: bool | None = None

    def exchange(self, step: str) -> Exchange | None:
        return next((ex for ex in self.exchanges if ex.step == step), None)


@dataclass
class Payload:
    """One decoded OTLP export request."""

    seq: int
    signal: str  # traces | metrics | logs
    transport: str  # grpc | http
    data: dict  # MessageToDict form, trace and span ids as lowercase hex
    raw: bytes  # the request body as received, decompressed
    content_type: str = ""
    header_names: list = field(default_factory=list)
    received_at: float = 0.0
    token_headers: list = field(default_factory=list)  # names of the headers that carried the expected token
    destination: str = "ops"  # ops | langfuse (sent to the Langfuse OTLP path)
    auth: dict = field(default_factory=dict)  # Langfuse auth facts as booleans (genai.auth_facts)

    @property
    def name(self) -> str:
        return f"otlp/{self.seq:04d}-{self.signal}-{self.transport}.json"


@dataclass
class ReplayChunk:
    """One frame of a session-replay upload: its envelope and decoded rrweb events."""

    seq: int  # frame number across all uploads
    upload: int  # upload (request) number
    frame: int  # position of the frame in its upload
    envelope: dict
    events: list | None  # None when the recorder sent only the payload's size
    header_names: list = field(default_factory=list)
    token_ok: bool | None = None  # x-oneuptime-token equals the expected token; None without one
    app_identifier: bool = False  # x-oneuptime-app-identifier was present
    content_type: str = ""
    received_at: float = 0.0
    note: str = ""

    @property
    def session_id(self) -> str:
        return str(self.envelope.get("sessionId") or "")

    @property
    def is_final(self) -> bool:
        return self.envelope.get("isFinal") is True

    @property
    def event_count(self) -> int:
        return len(self.events or [])


@dataclass
class BrowserStep:
    """One step (B1-B6) of the browser scenario."""

    id: str
    ok: bool = False
    detail: str = ""  # never holds a canary value
    started_ns: int = 0
    ended_ns: int = 0
    facts: dict = field(default_factory=dict)


@dataclass
class PageRequest:
    """One request the page made, from the browser's network events."""

    seq: int
    url: str  # as sent; may hold a canary (redacted in artifacts)
    method: str
    resource_type: str
    step: str  # the browser step it was sent in
    same_origin: bool = False
    header_names: list = field(default_factory=list)
    traceparent: str | None = None  # same-origin /api/ requests only
    tracestate: str | None = None
    token_in_url: bool = False  # the expected backend token in the URL
    token_headers: list = field(default_factory=list)  # names of the headers whose value carried it
    token_in_body: bool | None = None  # document and script responses: the body carries it
    status: int | None = None
    failure: str | None = None
    started_ns: int = 0

    @property
    def path(self) -> str:
        """Path without query or fragment (never a canary from the query)."""
        return urlsplit(self.url).path or "/"


@dataclass
class BrowserRun:
    """The browser scenario: steps, the page's network requests and what went wrong."""

    attempted: bool = False
    started: bool = False  # a browser launched
    browser: str = ""
    error: str | None = None
    origin: str = ""
    started_ns: int = 0
    ended_ns: int = 0
    steps: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    page_errors: list = field(default_factory=list)  # redacted messages
    console: list = field(default_factory=list)  # redacted error and warning messages

    def step(self, step_id: str) -> BrowserStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def in_window(self, time_ns: int) -> bool:
        return bool(self.started_ns and time_ns and self.started_ns <= time_ns <= (self.ended_ns or time_ns))


@dataclass
class ProfileUpload:
    """One Pyroscope upload (ingest API or push API series)."""

    seq: int
    source: str  # ingest | push
    app_name: str
    labels: dict  # static labels from the name query or the series
    content_type: str = ""
    format: str = ""
    spy_name: str = ""
    raw: bytes = b""  # body as received, decompressed when Content-Encoding said so
    query: str = ""
    parts: list = field(default_factory=list)  # decoded pprof summaries, one per profile part
    sample_labels: dict = field(default_factory=dict)  # label key -> sorted values, all parts
    note: str = ""
    received_at: float = 0.0

    @property
    def name(self) -> str:
        return f"profiles/{self.seq:04d}.bin"


@dataclass
class Capture:
    """Everything one run recorded."""

    canaries: object
    contract: dict | None = None  # parsed app contract, when a caller sets one
    contract_error: str | None = None
    setup: list = field(default_factory=list)
    setup_failed: str | None = None
    config: dict | None = None  # observability.yml as written
    config_error: str | None = None
    env: dict = field(default_factory=dict)  # environment the app ran with
    prometheus_declared: bool = False
    run1: RunResult = field(default_factory=lambda: RunResult(1))
    run2: RunResult = field(default_factory=lambda: RunResult(2, stdout_name="stdout-run2.txt", stderr_name="stderr-run2.txt"))
    otlp: list[Payload] = field(default_factory=list)
    profiles: list[ProfileUpload] = field(default_factory=list)
    rejected: list = field(default_factory=list)  # sink requests that couldn't be decoded
    ignored: dict = field(default_factory=dict)  # telemetry from other services, not graded
    upstream: list = field(default_factory=list)  # requests the fake upstream received
    upstream_port: int | None = None
    audit: dict | None = None
    # RUM runs
    browser: BrowserRun | None = None
    browser_otlp: list[Payload] = field(default_factory=list)  # payloads of resources with browser.* attributes
    replay_chunks: list[ReplayChunk] = field(default_factory=list)
    replay_requests: list = field(default_factory=list)  # every request to the replay stand-in
    replay_assets: dict = field(default_factory=dict)  # ReplayAssets.facts()
    token_set: bool = False  # a backend token file was written for the yml's ${file:...} references
    telemetry_ports: dict = field(default_factory=dict)

    @property
    def helpers(self) -> list[str]:
        return list((self.contract or {}).get("helpers") or [])

    @property
    def rum(self) -> bool:
        return bool((self.contract or {}).get("rum"))
