#!/usr/bin/env python3
"""Measure what telemetry costs a service: the same app and load with the SDK off, then on.

Usage:
  python3 overhead.py --cmd "<start command>" --url http://127.0.0.1:PORT --requests requests.json \
      [--duration 30] [--concurrency 8] [--warmup 5] [--rate RPS] [--budget budget.json] [--env KEY=VALUE ...] --out DIR

Starts the app twice from the same command: first with OTEL_SDK_DISABLED=true (the baseline), then
as-is with telemetry on (OTEL_SDK_DISABLED removed). Each run waits until the URL answers (any HTTP
status counts as ready; --ready-path picks the path), drives the fixed request mix in requests.json
(the format scripts/contract.py reads: method, path, optional headers and body) from a thread pool
for --warmup seconds (discarded) and then --duration seconds (as fast as the app answers, or paced
at --rate requests per second in total so both runs carry the same load), and records each request's latency
and errors (transport failures and 5xx). It samples the process tree's CPU time and RSS while the
load runs (/proc on Linux, `ps` elsewhere; children included), stops the app with SIGTERM to its
process group and records how long shutdown takes (SIGKILL after --kill-after seconds). Between the
runs it waits for the port to be free again.

Budget (on vs off), overridable with --budget, a JSON object with any of these keys:
  p50_pct 5, p50_ms 1      p50 may grow by 5 % or 1 ms, whichever is larger
  p99_pct 10, p99_ms 5     p99 may grow by 10 % or 5 ms, whichever is larger
  cpu_pct 10, cpu_points 2 CPU (% of one core) may grow by 10 % relative, or 2 points for a near-idle app;
                           when the runs' throughput differs by over 10 % (unpaced load against a slower
                           app), CPU time per request is compared instead, by the same 10 %
  cpu_ms_per_request 1     or telemetry may add 1 ms of CPU per request:
                           (cpu_seconds_on/requests_on - cpu_seconds_off/requests_off) x 1000. The CPU check
                           passes when either rule does; a fast app (stubs answering in microseconds) makes
                           a small absolute cost look large as % of a core
  rss_mib 50               peak RSS may grow by 50 MiB
  errors 0                 no added errors
  shutdown_s 15            telemetry-on shutdown within 15 s
Writes DIR/overhead.json and DIR/overhead.md. Header values and bodies are never printed or written:
requests are named by method and path only. Standard library only, Python 3.10+.
Exit codes: 0 within budget, 1 over budget, 2 bad input, the port already taken or the app not ready.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

DEFAULT_BUDGET = {"p50_pct": 5.0, "p50_ms": 1.0, "p99_pct": 10.0, "p99_ms": 5.0, "cpu_pct": 10.0, "cpu_points": 2.0,
                  "cpu_ms_per_request": 1.0, "rss_mib": 50.0, "errors": 0, "shutdown_s": 15.0}
SHELL_CHARS = re.compile(r"[|&;<>()$`*?~]|^\s*cd\s|\s(?:&&|\|\|)\s")
SAMPLE_EVERY = 0.25
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") and "SC_CLK_TCK" in os.sysconf_names else 100
LINUX = sys.platform.startswith("linux") and Path("/proc/self/stat").exists()


class InputError(Exception):
    """Bad arguments or files: exit 2."""


class NotReady(Exception):
    """The app did not come up, or its port was taken: exit 2."""


# Requests

def load_requests(path: Path) -> list[dict]:
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path}: {error}") from None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) and i.get("method") and i.get("path")
                                                           for i in items):
        raise InputError(f"{path}: expected a non-empty JSON list of objects with method and path")
    prepared = []
    for item in items:
        body = item.get("body")
        data = None if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
        headers = {str(k): str(v) for k, v in (item.get("headers") or {}).items()}
        if data is not None and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        path_only = str(item["path"]).split("?")[0]
        prepared.append({"name": f"{str(item['method']).upper()} {path_only}", "method": str(item["method"]).upper(),
                         "path": str(item["path"]), "headers": headers, "data": data})
    return prepared


def load_budget(path: Path | None) -> dict:
    budget = dict(DEFAULT_BUDGET)
    if path is None:
        return budget
    try:
        given = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"{path}: {error}") from None
    if not isinstance(given, dict):
        raise InputError(f"{path}: expected a JSON object")
    unknown = sorted(set(given) - set(DEFAULT_BUDGET))
    if unknown:
        raise InputError(f"{path}: unknown budget keys {', '.join(unknown)}")
    for key, value in given.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise InputError(f"{path}: {key} must be a non-negative number")
        budget[key] = float(value)
    return budget


def parse_env(pairs: list[str]) -> dict[str, str]:
    env = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise InputError(f"--env expects KEY=VALUE, got a value for {key or '?'!r}")
        env[key] = value
    return env


def parse_url(url: str) -> tuple[str, int, str]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InputError("--url must be an http(s) URL such as http://127.0.0.1:8080")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.hostname, port, parts.scheme


# Process tree sampling

def _ps_time(value: str) -> float:
    """`ps -o time=`: [[dd-]hh:]mm:ss[.cc] in seconds."""
    days, _, rest = value.rpartition("-")
    parts = [float(p) for p in rest.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds + (int(days) * 86400 if days else 0)


def process_table() -> dict[int, tuple[int, float, int]]:
    """pid -> (ppid, cpu seconds, rss KiB) for every process."""
    table: dict[int, tuple[int, float, int]] = {}
    if LINUX:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                stat = Path(f"/proc/{entry.name}/stat").read_text()
                fields = stat[stat.rindex(")") + 2:].split()
                rss_pages = int(fields[21])
                table[int(entry.name)] = (int(fields[1]), (int(fields[11]) + int(fields[12])) / CLK_TCK,
                                          rss_pages * (os.sysconf("SC_PAGE_SIZE") // 1024))
            except (OSError, ValueError, IndexError):
                continue
        return table
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,time="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return table
    for row in out.splitlines():
        parts = row.split()
        if len(parts) >= 4:
            try:
                table[int(parts[0])] = (int(parts[1]), _ps_time(parts[3]), int(parts[2]))
            except ValueError:
                continue
    return table


def tree_usage(root: int) -> tuple[float, int] | None:
    """CPU seconds and RSS KiB of the process and its descendants, or None once it has gone."""
    table = process_table()
    if root not in table:
        return None
    children: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    cpu, rss, stack, seen = 0.0, 0, [root], set()
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in table:
            continue
        seen.add(pid)
        cpu += table[pid][1]
        rss += table[pid][2]
        stack += children.get(pid, [])
    return cpu, rss


class Sampler(threading.Thread):
    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.pid, self.stop_event = pid, threading.Event()
        self.rss: list[int] = []
        self.first: tuple[float, float] | None = None   # (wall, cpu seconds)
        self.last: tuple[float, float] | None = None

    def run(self) -> None:
        while not self.stop_event.is_set():
            usage = tree_usage(self.pid)
            if usage is not None:
                now = time.monotonic()
                self.first = self.first or (now, usage[0])
                self.last = (now, usage[0])
                self.rss.append(usage[1])
            self.stop_event.wait(SAMPLE_EVERY)

    def result(self) -> dict:
        cpu = None
        if self.first and self.last and self.last[0] > self.first[0]:
            cpu = round(100 * (self.last[1] - self.first[1]) / (self.last[0] - self.first[0]), 2)
        return {"cpu_pct": cpu,
                "cpu_seconds": round(self.last[1] - self.first[1], 4) if self.first and self.last else None,
                "rss_peak_mib": round(max(self.rss) / 1024, 1) if self.rss else None,
                "rss_mean_mib": round(sum(self.rss) / len(self.rss) / 1024, 1) if self.rss else None,
                "samples": len(self.rss)}


# Load

def _connection(host: str, port: int, scheme: str, timeout: float) -> http.client.HTTPConnection:
    if scheme == "https":
        import ssl
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl._create_unverified_context())
    return http.client.HTTPConnection(host, port, timeout=timeout)


def drive(target: tuple[str, int, str], prefix: str, items: list[dict], seconds: float, concurrency: int,
          timeout: float, rate: float = 0) -> dict:
    """Each worker keeps one connection and walks the request mix from its own offset until the deadline,
    paced at rate / concurrency requests per second when a rate is given."""
    host, port, scheme = target
    deadline = time.monotonic() + seconds
    latencies: list[float] = []
    errors = {"transport": 0, "5xx": 0}
    statuses: dict[str, int] = {}
    lock = threading.Lock()

    def worker(offset: int) -> None:
        conn, index = None, offset
        interval = concurrency / rate if rate else 0
        due = time.monotonic() + interval * offset / concurrency
        mine: list[float] = []
        mine_errors = {"transport": 0, "5xx": 0}
        mine_status: dict[str, int] = {}
        while time.monotonic() < deadline:
            if interval:
                pause = due - time.monotonic()
                if pause > 0:
                    time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
                    if time.monotonic() >= deadline:
                        break
                due = max(due + interval, time.monotonic() - interval)   # never burst to catch up
            item = items[index % len(items)]
            index += 1
            started = time.perf_counter()
            try:
                conn = conn or _connection(host, port, scheme, timeout)
                conn.request(item["method"], prefix + item["path"], body=item["data"], headers=item["headers"])
                response = conn.getresponse()
                response.read()
                elapsed = time.perf_counter() - started
                if response.will_close:
                    conn.close()
                    conn = None
                mine.append(elapsed)
                code = str(response.status)
                mine_status[code] = mine_status.get(code, 0) + 1
                if response.status >= 500:
                    mine_errors["5xx"] += 1
            except (OSError, http.client.HTTPException):
                mine_errors["transport"] += 1
                if conn is not None:
                    conn.close()
                conn = None
                time.sleep(0.01)
        if conn is not None:
            conn.close()
        with lock:
            latencies.extend(mine)
            for key, value in mine_errors.items():
                errors[key] += value
            for key, value in mine_status.items():
                statuses[key] = statuses.get(key, 0) + value

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(concurrency)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(seconds + timeout + 5)
    wall = time.monotonic() - started
    latencies.sort()
    return {"requests": len(latencies), "throughput_rps": round(len(latencies) / wall, 1) if wall else None,
            "p50_ms": percentile(latencies, 50), "p90_ms": percentile(latencies, 90), "p99_ms": percentile(latencies, 99),
            "max_ms": round(latencies[-1] * 1000, 3) if latencies else None,
            "errors": errors["transport"] + errors["5xx"], "error_kinds": errors, "statuses": statuses}


def percentile(ordered: list[float], pct: float) -> float | None:
    if not ordered:
        return None
    rank = max(0, min(len(ordered) - 1, int(-(-pct * len(ordered) // 100)) - 1))   # nearest rank
    return round(ordered[rank] * 1000, 3)


# App lifecycle

def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_port_free(host: str, port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not port_open(host, port):
            return True
        time.sleep(0.1)
    return not port_open(host, port)


def start(cmd: str, env: dict[str, str]) -> subprocess.Popen:
    args = ["/bin/sh", "-c", cmd] if SHELL_CHARS.search(cmd) else shlex.split(cmd)
    if not args:
        raise InputError("--cmd is empty")
    try:
        return subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        raise InputError(f"could not start the app: {error.strerror or error}") from None


def wait_ready(proc: subprocess.Popen, target: tuple[str, int, str], path: str, seconds: float) -> float:
    host, port, scheme = target
    started = time.monotonic()
    deadline = started + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise NotReady(f"the app exited with code {proc.returncode} before it answered")
        try:
            conn = _connection(host, port, scheme, 2)
            conn.request("GET", path)
            conn.getresponse().read()
            conn.close()
            return round(time.monotonic() - started, 3)
        except (OSError, http.client.HTTPException):
            time.sleep(0.2)
    raise NotReady(f"the app did not answer on {host}:{port}{path} within {seconds:g}s")


def stop(proc: subprocess.Popen, kill_after: float) -> dict:
    """SIGTERM to the app's process group; the time until the main process exits."""
    started = time.monotonic()
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(kill_after)
        killed = False
    except subprocess.TimeoutExpired:
        killed = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
    elapsed = round(time.monotonic() - started, 3)
    try:   # anything left in the group (a shell's children) goes too
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return {"shutdown_s": elapsed, "killed": killed, "exit_code": proc.returncode}


def run_once(label: str, args, env: dict[str, str], items: list[dict], target: tuple[str, int, str], prefix: str) -> dict:
    host, port, _ = target
    if not wait_port_free(host, port, args.port_wait):
        raise NotReady(f"{host}:{port} is still taken before the {label} run; stop whatever listens there")
    proc = start(args.cmd, env)
    try:
        ready = wait_ready(proc, target, args.ready_path, args.ready_timeout)
        if args.warmup > 0:
            drive(target, prefix, items, args.warmup, args.concurrency, args.timeout, args.rate)
        sampler = Sampler(proc.pid)
        sampler.start()
        load = drive(target, prefix, items, args.duration, args.concurrency, args.timeout, args.rate)
        sampler.stop_event.set()
        sampler.join()
    except BaseException:
        stop(proc, args.kill_after)
        raise
    shutdown = stop(proc, args.kill_after)
    usage = sampler.result()
    if usage["cpu_seconds"] is not None and load["requests"]:
        usage["cpu_ms_per_request"] = round(usage["cpu_seconds"] * 1000 / load["requests"], 4)
    return {"label": label, "ready_s": ready, **load, **usage, **shutdown}


# Comparison

def compare(off: dict, on: dict, budget: dict) -> list[dict]:
    checks = []

    def check(name: str, before, after, allowed_after, unit: str, rule: str) -> None:
        ok = None if before is None or after is None else after <= allowed_after + 1e-9
        checks.append({"check": name, "off": before, "on": after,
                       "delta": None if ok is None else round(after - before, 3),
                       "limit": None if allowed_after is None else round(allowed_after, 3), "unit": unit, "rule": rule,
                       "ok": ok})

    for key, pct, floor in (("p50_ms", "p50_pct", "p50_ms"), ("p99_ms", "p99_pct", "p99_ms")):
        before = off.get(key)
        allowed = None if before is None else before + max(before * budget[pct] / 100, budget[floor])
        check(key.split("_")[0], before, on.get(key), allowed, "ms",
              f"+{budget[pct]:g}% or +{budget[floor]:g} ms, whichever is larger")
    cpu = off.get("cpu_pct")
    rps_off, rps_on = off.get("throughput_rps"), on.get("throughput_rps")
    if rps_off and rps_on and abs(rps_on - rps_off) > 0.1 * rps_off and off.get("cpu_ms_per_request") is not None:
        # Unpaced load: a slower app answers fewer requests and burns less CPU, so compare CPU per request.
        per = off["cpu_ms_per_request"]
        check("cpu", per, on.get("cpu_ms_per_request"), per * (1 + budget["cpu_pct"] / 100), "CPU ms per request",
              f"+{budget['cpu_pct']:g}% relative (throughput differs; pace with --rate to compare % of a core)")
    else:
        allowed = None if cpu is None else max(cpu * (1 + budget["cpu_pct"] / 100), cpu + budget["cpu_points"])
        check("cpu", cpu, on.get("cpu_pct"), allowed, "% of a core",
              f"+{budget['cpu_pct']:g}% relative (at least +{budget['cpu_points']:g} points)")
    # Or the absolute cost: CPU time telemetry adds to each request, which stays meaningful when the app does little.
    cpu_check = checks[-1]
    per_off, per_on = off.get("cpu_ms_per_request"), on.get("cpu_ms_per_request")
    added = None if per_off is None or per_on is None else round(per_on - per_off, 4)
    per_ok = None if added is None else added <= budget["cpu_ms_per_request"] + 1e-9
    relative_ok = cpu_check["ok"]
    cpu_check.update({"relative_ok": relative_ok, "cpu_ms_per_request_off": per_off, "cpu_ms_per_request_on": per_on,
                      "added_cpu_ms_per_request": added, "added_cpu_ms_per_request_limit": budget["cpu_ms_per_request"],
                      "per_request_ok": per_ok,
                      "ok": True if relative_ok or per_ok else None if relative_ok is None and per_ok is None else False})
    cpu_check["rule"] += f", or at most +{budget['cpu_ms_per_request']:g} ms of CPU per request"
    rss = off.get("rss_peak_mib")
    check("rss_peak", rss, on.get("rss_peak_mib"), None if rss is None else rss + budget["rss_mib"], "MiB",
          f"+{budget['rss_mib']:g} MiB")
    check("errors", off.get("errors"), on.get("errors"), (off.get("errors") or 0) + budget["errors"], "count",
          f"+{budget['errors']:g} added errors")
    check("shutdown", off.get("shutdown_s"), on.get("shutdown_s"), budget["shutdown_s"], "s",
          f"telemetry-on shutdown within {budget['shutdown_s']:g} s")
    if on.get("killed"):
        checks[-1]["ok"] = False
    return checks


def _state(ok) -> str:
    return "unmeasured" if ok is None else "ok" if ok else "over"


def markdown(report: dict) -> str:
    off, on = report["runs"]["off"], report["runs"]["on"]

    def fmt(value) -> str:
        return "n/a" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)

    lines = [f"# Telemetry overhead: {'within budget' if report['within_budget'] else 'OVER BUDGET'}", "",
             f"{report['settings']['duration_s']:g} s of load at concurrency {report['settings']['concurrency']} "
             f"after {report['settings']['warmup_s']:g} s warm-up"
             + (f", paced at {report['settings']['rate_rps']:g} rps" if report["settings"]["rate_rps"] else ", unpaced")
             + f", {len(report['requests'])} request(s) in the mix.", "",
             "| Check | Telemetry off | Telemetry on | Delta | Limit (on) | Budget | Result |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in report["checks"]:
        result = "unmeasured" if c["ok"] is None else "ok" if c["ok"] else "over"
        lines.append(f"| {c['check']} ({c['unit']}) | {fmt(c['off'])} | {fmt(c['on'])} | {fmt(c['delta'])} | "
                     f"{fmt(c['limit'])} | {c['rule']} | {result} |")
    cpu = next((c for c in report["checks"] if c["check"] == "cpu"), None)
    if cpu is not None:
        lines += ["", f"CPU per request: {fmt(cpu.get('cpu_ms_per_request_off'))} ms off, "
                      f"{fmt(cpu.get('cpu_ms_per_request_on'))} ms on, "
                      f"{fmt(cpu.get('added_cpu_ms_per_request'))} ms added "
                      f"(limit {fmt(cpu.get('added_cpu_ms_per_request_limit'))} ms: "
                      f"{_state(cpu.get('per_request_ok'))}); relative rule: {_state(cpu.get('relative_ok'))}."]
    lines += ["", "| Run | Requests | Throughput (rps) | p90 ms | Max ms | Errors | Ready s | Mean RSS MiB | CPU s | CPU ms per request |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, run in (("off", off), ("on", on)):
        lines.append(f"| {name} | {fmt(run['requests'])} | {fmt(run['throughput_rps'])} | {fmt(run['p90_ms'])} | "
                     f"{fmt(run['max_ms'])} | {fmt(run['errors'])} | {fmt(run['ready_s'])} | {fmt(run['rss_mean_mib'])} | "
                     f"{fmt(run.get('cpu_seconds'))} | {fmt(run.get('cpu_ms_per_request'))} |")
    lines += ["", "Requests: " + ", ".join(report["requests"]) + ".", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cmd", required=True, help="the app's start command (run with telemetry off, then on)")
    parser.add_argument("--url", required=True, help="the app's base URL, e.g. http://127.0.0.1:8080")
    parser.add_argument("--requests", type=Path, required=True, help="request mix, contract.py format")
    parser.add_argument("--out", type=Path, required=True, help="directory for overhead.json and overhead.md")
    parser.add_argument("--duration", type=float, default=30.0, help="measured load per run, seconds")
    parser.add_argument("--warmup", type=float, default=5.0, help="discarded load before measuring, seconds")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rate", type=float, default=0, help="total requests per second (default: as fast as the app answers)")
    parser.add_argument("--budget", type=Path, help="JSON object overriding budget keys")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="extra env for both runs")
    parser.add_argument("--ready-path", default="/", help="path polled until the app answers (any status)")
    parser.add_argument("--ready-timeout", type=float, default=60.0)
    parser.add_argument("--port-wait", type=float, default=30.0, help="seconds to wait for the port to be free")
    parser.add_argument("--kill-after", type=float, default=30.0, help="SIGKILL when SIGTERM takes longer")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout, seconds")
    args = parser.parse_args(argv)
    try:
        if args.duration <= 0 or args.warmup < 0 or args.concurrency < 1 or args.rate < 0:
            raise InputError("--duration must be > 0, --warmup and --rate >= 0, --concurrency >= 1")
        items = load_requests(args.requests)
        budget = load_budget(args.budget)
        extra = parse_env(args.env)
        host, port, scheme = parse_url(args.url)
        prefix = urllib.parse.urlsplit(args.url).path.rstrip("/")
        if not args.ready_path.startswith("/"):
            args.ready_path = "/" + args.ready_path
        args.ready_path = prefix + args.ready_path
        if port_open(host, port):
            raise NotReady(f"{host}:{port} already answers before the app starts; stop whatever listens there")
        base_env = {**os.environ, **extra}
        off_env = {**base_env, "OTEL_SDK_DISABLED": "true"}
        on_env = {k: v for k, v in base_env.items() if k != "OTEL_SDK_DISABLED"}
        target = (host, port, scheme)
        off = run_once("off", args, off_env, items, target, prefix)
        on = run_once("on", args, on_env, items, target, prefix)
    except InputError as error:
        print(f"overhead: {error}", file=sys.stderr)
        return 2
    except NotReady as error:
        print(f"overhead: {error}", file=sys.stderr)
        return 2
    checks = compare(off, on, budget)
    within = all(c["ok"] is not False for c in checks)
    report = {"within_budget": within, "budget": budget, "checks": checks, "runs": {"off": off, "on": on},
              "requests": sorted({i["name"] for i in items}),
              "settings": {"duration_s": args.duration, "warmup_s": args.warmup, "concurrency": args.concurrency,
                           "rate_rps": args.rate or None,
                           "extra_env_keys": sorted(extra)}}
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "overhead.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.out / "overhead.md").write_text(markdown(report), encoding="utf-8")
    except OSError as error:
        print(f"overhead: {error}", file=sys.stderr)
        return 2
    for c in checks:
        state = "unmeasured" if c["ok"] is None else "ok" if c["ok"] else "OVER"
        print(f"{c['check']}: off {c['off']} on {c['on']} {c['unit']} (limit {c['limit']}) {state}")
        if c["check"] == "cpu":
            print(f"cpu per request: added {c['added_cpu_ms_per_request']} ms (limit {c['added_cpu_ms_per_request_limit']} ms)")
    print(f"{'within budget' if within else 'over budget'}; wrote {args.out / 'overhead.json'} and overhead.md")
    return 0 if within else 1


if __name__ == "__main__":
    sys.exit(main())
