"""Canary scan: which test values reached which stream. Values are compared, never printed or written.

The receiver keeps replay chunks as metadata only, so a value typed into a masked field could not be
looked for in the output folder afterwards; the scan runs over what the receiver holds in memory:
OTLP request bodies (raw and decoded), Langfuse payloads and requests, replay events and requests,
profile uploads and rejected requests.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from urllib.parse import quote


def load(path: Path) -> dict[str, str]:
    """A JSON object {label: value} or a JSON list of values (labelled by position)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        items = {str(k): v for k, v in doc.items()}
    elif isinstance(doc, list):
        items = {f"canary-{i + 1}": v for i, v in enumerate(doc)}
    else:
        raise ValueError(f"{path}: expected a JSON object or list of canary values")
    bad = [k for k, v in items.items() if not isinstance(v, str) or len(v) < 4]
    if bad:
        raise ValueError(f"{path}: canaries must be strings of at least 4 characters: {', '.join(bad)}")
    return items


def _forms(value: str) -> set[bytes]:
    """The value as an app or a transport may carry it: as is, percent-encoded and JSON-escaped; compared lowercase."""
    forms = {value, quote(value, safe=""), json.dumps(value)[1:-1]}
    return {f.lower().encode("utf-8") for f in forms if f}


def _blob(value) -> bytes:
    if isinstance(value, bytes):
        if value[:2] == b"\x1f\x8b":
            try:
                value = gzip.decompress(value)
            except OSError:
                pass
        return value.lower()
    return json.dumps(value, default=str, ensure_ascii=False).lower().encode("utf-8")


def streams(store) -> dict[str, list[bytes]]:
    """What the receiver holds, grouped by stream, as lowercase bytes."""
    with store.lock:
        otlp = list(store.otlp)
        chunks, replay_requests = list(store.replay_chunks), list(store.replay_requests)
        profiles, rejected = list(store.profiles), list(store.rejected)
        langfuse_requests = (list(store.langfuse_scores), list(store.langfuse_prompt_fetches),
                             list(store.langfuse_media), list(store.langfuse_dataset_fetches))
    out: dict[str, list[bytes]] = {"OTLP": [], "Langfuse": [], "replay events": [], "replay requests": [],
                                   "profiles": [], "rejected requests": []}
    for payload in otlp:
        name = "Langfuse" if payload.destination == "langfuse" else "OTLP"
        out[name] += [_blob(payload.raw), _blob(payload.data)]
    out["Langfuse"] += [_blob(items) for items in langfuse_requests if items]
    out["replay events"] += [_blob({"envelope": c.envelope, "events": c.events}) for c in chunks]
    out["replay requests"] += [_blob(replay_requests)] if replay_requests else []
    out["profiles"] += [_blob(p.raw) for p in profiles]
    out["rejected requests"] += [_blob(rejected)] if rejected else []
    return out


def scan(store, canaries: dict[str, str]) -> dict[str, list[str]]:
    """{label: [streams the value reached]}; an empty list means the canary appeared nowhere."""
    held = streams(store)
    found: dict[str, list[str]] = {}
    for label, value in canaries.items():
        forms = _forms(value)
        found[label] = sorted(name for name, blobs in held.items() if any(f in blob for blob in blobs for f in forms))
    return found


def summary(found: dict[str, list[str]]) -> list[str]:
    """The summary.md section: labels and stream names only."""
    if not found:
        return []
    leaked = {label: where for label, where in found.items() if where}
    lines = [f"## Canaries ({len(found)} checked; values are never written)", ""]
    if not leaked:
        return lines + ["None found in any stream.", ""]
    lines += ["| Canary | Found in |", "|---|---|"]
    lines += [f"| {label} | {', '.join(where)} |" for label, where in sorted(leaked.items())]
    lines.append(f"\n{len(found) - len(leaked)} canaries found nowhere.")
    return lines + [""]
