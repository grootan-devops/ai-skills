"""Minimal protobuf wire-format reader for pprof profiles and Pyroscope payloads.

Only what the checks need: Profile.string_table (6), Profile.sample (2) -> Sample.label (3) ->
Label.key (1) / Label.str (2), plus Profile.sample_type (1) for the metadata.
"""
from __future__ import annotations

import base64
import gzip
import json


class WireError(ValueError):
    """The bytes are not a well-formed protobuf message."""


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise WireError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise WireError("varint too long")


def fields(buf: bytes):
    """Yield (field number, wire type, value) for each field of one message."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = read_varint(buf, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise WireError("field number 0")
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 1:
            value, pos = int.from_bytes(buf[pos : pos + 8], "little"), pos + 8
        elif wire == 2:
            size, pos = read_varint(buf, pos)
            value, pos = bytes(buf[pos : pos + size]), pos + size
        elif wire == 5:
            value, pos = int.from_bytes(buf[pos : pos + 4], "little"), pos + 4
        else:
            raise WireError(f"unsupported wire type {wire}")
        if pos > end:
            raise WireError("truncated field")
        yield number, wire, value


def gunzip_if_needed(data: bytes) -> bytes:
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def _message(buf: bytes, wanted: dict) -> dict:
    """Pick scalar fields by number: {field number: default}."""
    out = dict(wanted)
    for number, _, value in fields(buf):
        if number in wanted:
            out[number] = value
    return out


def decode_profile(data: bytes) -> dict:
    """Summarise a pprof profile: sample types, sample count and sample label values."""
    data = gunzip_if_needed(data)
    strings, samples, sample_types = [], [], []
    for number, wire, value in fields(data):
        if wire != 2:
            continue
        if number == 6:
            strings.append(value.decode("utf-8", "replace"))
        elif number == 2:
            samples.append(value)
        elif number == 1:
            sample_types.append(value)
    if strings and strings[0] != "":
        raise WireError("string_table[0] must be empty")

    def text(index: int) -> str:
        return strings[index] if 0 <= index < len(strings) else ""

    labels: dict[str, set] = {}
    labelled = 0
    for sample in samples:
        has_label = False
        for number, wire, value in fields(sample):
            if number == 3 and wire == 2:
                label = _message(value, {1: 0, 2: 0})
                key = text(label[1])
                if key:
                    has_label = True
                    values = labels.setdefault(key, set())
                    if label[2]:
                        values.add(text(label[2]))
        labelled += has_label
    types = []
    for raw in sample_types:
        vt = _message(raw, {1: 0, 2: 0})
        types.append(f"{text(vt[1])}:{text(vt[2])}")
    return {
        "sample_types": types,
        "samples": len(samples),
        "labelled_samples": labelled,
        "labels": {k: sorted(v) for k, v in sorted(labels.items())},
    }


def decode_jfr_labels(data: bytes) -> dict:
    """Labels from a Java agent's `labels` part (LabelsSnapshot: contexts=1, strings=2 maps)."""
    data = gunzip_if_needed(data)
    strings: dict[int, str] = {}
    contexts = []
    for number, wire, value in fields(data):
        if wire != 2:
            continue
        if number == 2:
            entry = _message(value, {1: 0, 2: b""})
            strings[entry[1]] = entry[2].decode("utf-8", "replace") if isinstance(entry[2], bytes) else ""
        elif number == 1:
            entry = _message(value, {1: 0, 2: b""})
            if isinstance(entry[2], bytes):
                contexts.append(entry[2])
    labels: dict[str, set] = {}
    for context in contexts:
        for number, wire, value in fields(context):
            if number == 1 and wire == 2:
                pair = _message(value, {1: 0, 2: 0})
                key = strings.get(pair[1], "")
                if key:
                    labels.setdefault(key, set()).add(strings.get(pair[2], ""))
    return {k: sorted(v) for k, v in sorted(labels.items())}


def decode_push(data: bytes, json_body: bool = False) -> list[dict]:
    """Series of a Pyroscope PushRequest: [{"labels": {name: value}, "profiles": [bytes]}]."""
    series = []
    if json_body:
        doc = json.loads(data.decode("utf-8"))
        for item in doc.get("series", []):
            labels = {pair.get("name", ""): pair.get("value", "") for pair in item.get("labels", [])}
            raws = []
            for sample in item.get("samples", []):
                encoded = sample.get("rawProfile") or sample.get("raw_profile") or ""
                raws.append(base64.b64decode(encoded))
            series.append({"labels": labels, "profiles": raws})
        return series
    for number, wire, value in fields(data):
        if number != 1 or wire != 2:
            continue
        labels, raws = {}, []
        for inner, inner_wire, inner_value in fields(value):
            if inner_wire != 2:
                continue
            if inner == 1:
                pair = _message(inner_value, {1: b"", 2: b""})
                labels[pair[1].decode("utf-8", "replace")] = pair[2].decode("utf-8", "replace")
            elif inner == 2:
                raws.append(_message(inner_value, {1: b""})[1])
        series.append({"labels": labels, "profiles": raws})
    return series


# Encoder used by the tests to build tiny profiles with the same wire format.


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_field(number: int, value) -> bytes:
    if isinstance(value, int):
        return encode_varint(number << 3) + encode_varint(value)
    if isinstance(value, str):
        value = value.encode()
    return encode_varint(number << 3 | 2) + encode_varint(len(value)) + value
