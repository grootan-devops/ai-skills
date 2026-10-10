#!/usr/bin/env python3
"""Record an app's API responses before a change and compare them after it.

Usage:
  python3 contract.py record  --base http://127.0.0.1:3000 --requests requests.json --out before.json
  python3 contract.py compare --base http://127.0.0.1:3000 --requests requests.json --against before.json

requests.json is a JSON list; each item has `method`, `path` and optionally `name`, `headers`,
`body` (a string or JSON value), and `ignore` (JSON keys whose values change between runs, such
as timestamps). Status, content type and body must match: bodies compare as JSON when both
parse, otherwise byte for byte. Header values are never printed. Standard library only.
Exit codes: 0 identical, 1 differences, 2 bad input or the app unreachable.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_requests(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("method") and i.get("path") for i in items):
        raise ValueError(f"{path}: expected a JSON list of objects with method and path")
    for index, item in enumerate(items):
        item.setdefault("name", f"#{index + 1} {item['method'].upper()} {item['path'].split('?')[0]}")
    return items


def send(base: str, item: dict, timeout: float) -> dict:
    body = item.get("body")
    data = None if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
    headers = dict(item.get("headers") or {})
    if data is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base.rstrip("/") + item["path"], data=data, headers=headers,
                                     method=item["method"].upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, ctype, raw = response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        status, ctype, raw = error.code, error.headers.get("Content-Type", ""), error.read()
    return {"status": status, "content_type": ctype.split(";")[0].strip().lower(),
            "body": raw.decode("utf-8", errors="replace")}


def _strip(value, ignore: set):
    if isinstance(value, dict):
        return {k: _strip(v, ignore) for k, v in value.items() if k not in ignore}
    if isinstance(value, list):
        return [_strip(v, ignore) for v in value]
    return value


def same_body(before: str, after: str, ignore: set) -> bool:
    try:
        return _strip(json.loads(before), ignore) == _strip(json.loads(after), ignore)
    except ValueError:
        return before == after


def record(base: str, items: list[dict], timeout: float) -> dict:
    return {item["name"]: send(base, item, timeout) for item in items}


def compare(base: str, items: list[dict], before: dict, timeout: float) -> dict[str, list[str]]:
    """Differences per request name; requests that match are left out."""
    problems: dict[str, list[str]] = {}
    for item in items:
        name, found = item["name"], []
        if name not in before:
            problems[name] = ["not in the recording"]
            continue
        old, new = before[name], send(base, item, timeout)
        if old["status"] != new["status"]:
            found.append(f"status {old['status']} -> {new['status']}")
        if old["content_type"] != new["content_type"]:
            found.append(f"content type {old['content_type']!r} -> {new['content_type']!r}")
        if not same_body(old["body"], new["body"], set(item.get("ignore") or [])):
            found.append(f"body {old['body'][:120]!r} -> {new['body'][:120]!r}")
        if found:
            problems[name] = found
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["record", "compare"])
    parser.add_argument("--base", required=True, help="the running app, e.g. http://127.0.0.1:3000")
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="record: where to write the responses")
    parser.add_argument("--against", type=Path, help="compare: a recording made before the change")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        items = load_requests(args.requests)
        if args.mode == "record":
            if not args.out:
                parser.error("record needs --out")
            responses = record(args.base, items, args.timeout)
            args.out.write_text(json.dumps(responses, indent=2) + "\n", encoding="utf-8")
            print(f"recorded {len(responses)} responses to {args.out}")
            return 0
        if not args.against:
            parser.error("compare needs --against")
        problems = compare(args.base, items, json.loads(args.against.read_text(encoding="utf-8")), args.timeout)
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"contract: {error}", file=sys.stderr)
        return 2
    for name, found in problems.items():
        print(f"{name}: {'; '.join(found)}")
    print(f"{len(items) - len(problems)}/{len(items)} responses unchanged")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
