"""The session-replay stand-in: OneUptime's replay routes, its policy and framed chunk uploads.

The loader and recorder are third-party build outputs. The receiver never ships them: it serves
them from a folder given with --replay-assets (or OBS_RECEIVER_REPLAY_ASSETS).
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import os
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PREFIX = "/telemetry/session-replay"
LOADER = f"{PREFIX}/v1/recorder.js"
RECORDER = f"{PREFIX}/latest/recorder.js"
CONFIG = f"{PREFIX}/v1/config"
CHUNK = f"{PREFIX}/v1/chunk"
VALIDATE = f"{PREFIX}/v1/validate"
TOKEN_HEADER = "x-oneuptime-token"
APP_HEADER = "x-oneuptime-app-identifier"
CHUNK_REPLY = {"directive": "continue", "configEpoch": 0}
MAX_FRAMES = 8
ASSETS_ENV = "OBS_RECEIVER_REPLAY_ASSETS"
LEGACY_ASSETS_ENV = "OBS_HARNESS_REPLAY_ASSETS"
ASSET_FILES = ("loader.js", "recorder.js", "manifest.json")
# rrweb event types, for metadata that never shows event content.
EVENT_TYPES = {0: "DomContentLoaded", 1: "Load", 2: "FullSnapshot", 3: "IncrementalSnapshot", 4: "Meta",
               5: "Custom", 6: "Plugin"}
FULL_SNAPSHOT = 2


class ChunkError(ValueError):
    """A chunk upload that doesn't follow the framing."""


@dataclass
class Frame:
    """One `<envelope JSON> \\n<payload>` frame of an upload."""

    envelope: dict
    events: list | None  # decoded rrweb events; None when the recorder sent only the payload's size
    note: str = ""


def parse_chunk_body(body: bytes) -> list[Frame]:
    """Split an upload into its 1-8 frames and decode each payload (gzip or identity JSON events)."""
    if not body:
        raise ChunkError("empty body")
    frames: list[Frame] = []
    pos = 0
    while pos < len(body):
        index = len(frames)
        if index == MAX_FRAMES:
            raise ChunkError(f"more than {MAX_FRAMES} frames")
        newline = body.find(b"\n", pos)
        if newline < 0:
            raise ChunkError(f"frame {index}: no newline after the envelope")
        try:
            envelope = json.loads(body[pos:newline].decode("utf-8"))  # the recorder writes `<json> \n`
        except (UnicodeDecodeError, ValueError):
            raise ChunkError(f"frame {index}: the envelope is not JSON") from None
        if not isinstance(envelope, dict):
            raise ChunkError(f"frame {index}: the envelope is not a JSON object")
        size = envelope.get("payloadBytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ChunkError(f"frame {index}: payloadBytes is not a non-negative integer")
        start, end = newline + 1, newline + 1 + size
        payload = body[start:end]
        if len(payload) < size:
            if start == len(body):  # an over-limit chunk: the recorder sends the envelope and the size only
                frames.append(Frame(envelope, None, "payload omitted by the recorder (over its size limit)"))
                break
            raise ChunkError(f"frame {index}: the payload is shorter than payloadBytes")
        encoding = str(envelope.get("payloadEncoding") or "identity").lower()
        if encoding == "gzip":
            try:
                data = gzip.decompress(payload)
            except (OSError, EOFError, zlib.error):
                raise ChunkError(f"frame {index}: the gzip payload doesn't decompress") from None
        elif encoding == "identity":
            data = payload
        else:
            raise ChunkError(f"frame {index}: unknown payloadEncoding {encoding!r}")
        try:
            events = json.loads(data.decode("utf-8")) if data else []
        except (UnicodeDecodeError, ValueError):
            raise ChunkError(f"frame {index}: the payload is not JSON") from None
        if not isinstance(events, list):
            raise ChunkError(f"frame {index}: the payload is not a JSON array of events")
        frames.append(Frame(envelope, events))
        pos = end
    return frames


def has_full_snapshot(envelope: dict, events: list | None) -> bool:
    return envelope.get("hasFullSnapshot") is True or any(
        isinstance(e, dict) and e.get("type") == FULL_SNAPSHOT for e in events or [])


def event_type_counts(events: list | None) -> dict:
    """rrweb event types and how often each occurs: metadata only, never event content."""
    counts = Counter(EVENT_TYPES.get(e.get("type"), str(e.get("type"))) if isinstance(e, dict) else "invalid"
                     for e in events or [])
    return dict(sorted(counts.items()))


def envelope_urls(envelope: dict) -> list[str]:
    """The envelope's URL fields (`url`, `routes`, `meta.entryUrl` and any other `*url*` key)."""
    found: list[str] = []

    def walk(node, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and ("url" in key.lower() or key == "routes"):
            found.append(node)

    walk(envelope)
    return found


def resolve_assets_dir(option) -> Path | None:
    """--replay-assets, else OBS_RECEIVER_REPLAY_ASSETS (or OBS_HARNESS_REPLAY_ASSETS), else none."""
    if option:
        return Path(option).expanduser()
    env = (os.environ.get(ASSETS_ENV) or os.environ.get(LEGACY_ASSETS_ENV) or "").strip()
    return Path(env).expanduser() if env else None


class ReplayAssets:
    """loader.js, recorder.js and manifest.json from the assets folder, checked against the manifest."""

    def __init__(self, directory: Path | None):
        self.directory = directory
        self.loader = self.recorder = b""
        self.integrity = ""
        self.manifest: dict = {}
        self.error: str | None = None
        if directory is None:
            self.error = f"no replay assets given (--replay-assets <dir> or {ASSETS_ENV})"
            return
        missing = [name for name in ASSET_FILES if not (directory / name).is_file()]
        if missing:
            self.error = f"the replay assets folder {directory} lacks {', '.join(missing)}"
            return
        try:
            self.loader = (directory / "loader.js").read_bytes()
            self.recorder = (directory / "recorder.js").read_bytes()
            self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            self.integrity = str(self.manifest["files"]["recorder.js"]["integrity"])
        except (OSError, ValueError, KeyError, TypeError):
            self.error = "manifest.json has no files[\"recorder.js\"].integrity"
            return
        algorithm, _, digest = self.integrity.partition("-")
        try:
            actual = base64.b64encode(hashlib.new(algorithm, self.recorder).digest()).decode()
        except (ValueError, TypeError, binascii.Error):
            self.error = f"manifest.json names an unknown integrity algorithm ({algorithm or '-'})"
            return
        if actual != digest:
            self.error = "recorder.js doesn't match the integrity in manifest.json"

    @property
    def ok(self) -> bool:
        return self.error is None

    def facts(self) -> dict:
        return {"dir": str(self.directory) if self.directory else None, "ok": self.ok, "error": self.error,
                "recorder_version": self.manifest.get("recorderVersion"), "rrweb_version": self.manifest.get("rrwebVersion")}


def policy(assets: ReplayAssets | None) -> dict:
    """The replay policy the stand-in serves: OneUptime's defaults."""
    doc = {"enabled": True, "recorderVersion": "latest"}
    if assets is not None and assets.ok:
        doc["recorderIntegrity"] = assets.integrity
    doc.update({"sameOriginTracePropagation": True, "maskingMode": "MaskSensitiveInputsOnly"})
    return doc
