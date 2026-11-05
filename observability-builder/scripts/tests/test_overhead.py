import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overhead  # noqa: E402

# A tiny app: telemetry "on" (OTEL_SDK_DISABLED unset) adds APP_SLOW_MS of latency per request.
APP = r'''
import os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLOW = 0 if os.environ.get("OTEL_SDK_DISABLED") == "true" else float(os.environ.get("APP_SLOW_MS", "0")) / 1000

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if SLOW:
            time.sleep(SLOW)
        status = 500 if self.path.startswith("/boom") else 200
        raw = b'{"ok": true}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    do_GET = do_POST = _reply

class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

Server(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class OverheadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.app = self.dir / "app.py"
        self.app.write_text(APP)
        self.requests = self.dir / "requests.json"
        self.requests.write_text(json.dumps([
            {"method": "GET", "path": "/orders/1?token=query-secret", "headers": {"Authorization": "Bearer header-secret"}},
            {"method": "POST", "path": "/orders", "body": {"card": "body-secret"}},
        ]))
        self.port = free_port()
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *extra, slow_ms=0, budget=None):
        args = ["--cmd", f"{sys.executable} {self.app} {self.port}", "--url", f"http://127.0.0.1:{self.port}",
                "--requests", str(self.requests), "--duration", "1.5", "--warmup", "0.3", "--concurrency", "4",
                "--out", str(self.out), "--env", f"APP_SLOW_MS={slow_ms}", "--ready-timeout", "20", *extra]
        if budget is not None:
            path = self.dir / "budget.json"
            path.write_text(json.dumps(budget))
            args += ["--budget", str(path)]
        return overhead.main(args)

    def written(self):
        text = (self.out / "overhead.json").read_text() + (self.out / "overhead.md").read_text()
        for secret in ("header-secret", "body-secret", "query-secret"):
            self.assertNotIn(secret, text)
        return json.loads((self.out / "overhead.json").read_text())

    def test_within_budget(self):
        # Loose CPU/RSS limits: both runs are the same app, so only noise differs.
        code = self.run_main(budget={"cpu_pct": 200, "cpu_points": 50, "rss_mib": 200, "p50_ms": 20, "p99_ms": 50})
        report = self.written()
        self.assertEqual(code, 0, report["checks"])
        self.assertTrue(report["within_budget"])
        self.assertGreater(report["runs"]["off"]["requests"], 0)
        self.assertEqual(report["runs"]["on"]["errors"], 0)
        self.assertLess(report["runs"]["on"]["shutdown_s"], 15)
        self.assertIsNotNone(report["runs"]["on"]["rss_peak_mib"])
        self.assertIn("within budget", (self.out / "overhead.md").read_text())

    def test_over_budget_when_telemetry_adds_latency(self):
        code = self.run_main(slow_ms=30, budget={"cpu_pct": 200, "cpu_points": 50, "rss_mib": 200})
        report = self.written()
        self.assertEqual(code, 1)
        failed = {c["check"] for c in report["checks"] if c["ok"] is False}
        self.assertIn("p50", failed)
        self.assertGreater(report["runs"]["on"]["p50_ms"], report["runs"]["off"]["p50_ms"] + 20)

    def test_added_errors_are_over_budget(self):
        self.requests.write_text(json.dumps([{"method": "GET", "path": "/boom"}]))
        checks = overhead.compare({"errors": 0, "p50_ms": 1, "p99_ms": 2, "shutdown_s": 0.1},
                                  {"errors": 3, "p50_ms": 1, "p99_ms": 2, "shutdown_s": 0.1}, dict(overhead.DEFAULT_BUDGET))
        self.assertFalse(next(c for c in checks if c["check"] == "errors")["ok"])
        checks = overhead.compare({"shutdown_s": 0.1}, {"shutdown_s": 16}, dict(overhead.DEFAULT_BUDGET))
        self.assertFalse(next(c for c in checks if c["check"] == "shutdown")["ok"])

    def test_latency_floor_applies_when_percent_is_smaller(self):
        checks = overhead.compare({"p50_ms": 2.0, "p99_ms": 4.0}, {"p50_ms": 2.9, "p99_ms": 8.9}, dict(overhead.DEFAULT_BUDGET))
        self.assertTrue(next(c for c in checks if c["check"] == "p50")["ok"])
        self.assertTrue(next(c for c in checks if c["check"] == "p99")["ok"])
        checks = overhead.compare({"p50_ms": 2.0}, {"p50_ms": 3.1}, dict(overhead.DEFAULT_BUDGET))
        self.assertFalse(next(c for c in checks if c["check"] == "p50")["ok"])

    def test_cpu_passes_on_either_rule(self):
        budget = dict(overhead.DEFAULT_BUDGET)
        # A fast app: +0.6 ms CPU per request doubles % of a core, but stays under the 1 ms per-request budget.
        off = {"cpu_pct": 6.0, "throughput_rps": 100.0, "cpu_ms_per_request": 0.6}
        on = {"cpu_pct": 12.0, "throughput_rps": 100.0, "cpu_ms_per_request": 1.2}
        cpu = next(c for c in overhead.compare(off, on, budget) if c["check"] == "cpu")
        self.assertFalse(cpu["relative_ok"])
        self.assertTrue(cpu["per_request_ok"])
        self.assertTrue(cpu["ok"])
        self.assertAlmostEqual(cpu["added_cpu_ms_per_request"], 0.6)
        # Over both rules.
        on = {"cpu_pct": 30.0, "throughput_rps": 100.0, "cpu_ms_per_request": 3.0}
        cpu = next(c for c in overhead.compare(off, on, budget) if c["check"] == "cpu")
        self.assertFalse(cpu["per_request_ok"])
        self.assertFalse(cpu["ok"])
        # A slow app: +3 ms per request is within 10 % of its CPU, so the relative rule passes alone.
        off = {"cpu_pct": 50.0, "throughput_rps": 10.0, "cpu_ms_per_request": 50.0}
        on = {"cpu_pct": 53.0, "throughput_rps": 10.0, "cpu_ms_per_request": 53.0}
        cpu = next(c for c in overhead.compare(off, on, budget) if c["check"] == "cpu")
        self.assertTrue(cpu["relative_ok"])
        self.assertFalse(cpu["per_request_ok"])
        self.assertTrue(cpu["ok"])
        # A stricter per-request budget from --budget.
        budget["cpu_ms_per_request"] = 0.5
        off = {"cpu_pct": 6.0, "throughput_rps": 100.0, "cpu_ms_per_request": 0.6}
        on = {"cpu_pct": 12.0, "throughput_rps": 100.0, "cpu_ms_per_request": 1.2}
        self.assertFalse(next(c for c in overhead.compare(off, on, budget) if c["check"] == "cpu")["ok"])
        # Nothing measured stays unmeasured.
        self.assertIsNone(next(c for c in overhead.compare({}, {}, budget) if c["check"] == "cpu")["ok"])

    def test_report_shows_both_cpu_numbers(self):
        self.run_main(budget={"cpu_pct": 200, "cpu_points": 50, "rss_mib": 200, "p50_ms": 20, "p99_ms": 50})
        report = self.written()
        cpu = next(c for c in report["checks"] if c["check"] == "cpu")
        for key in ("relative_ok", "per_request_ok", "added_cpu_ms_per_request", "cpu_ms_per_request_off",
                    "cpu_ms_per_request_on"):
            self.assertIn(key, cpu)
        self.assertIsNotNone(report["runs"]["on"]["cpu_seconds"])
        self.assertEqual(report["budget"]["cpu_ms_per_request"], 1.0)
        markdown = (self.out / "overhead.md").read_text()
        self.assertIn("CPU per request:", markdown)
        self.assertIn("ms of CPU per request", markdown)

    def test_app_not_ready_and_bad_input_exit_2(self):
        args = ["--cmd", f"{sys.executable} -c pass", "--url", f"http://127.0.0.1:{self.port}",
                "--requests", str(self.requests), "--duration", "1", "--warmup", "0", "--out", str(self.out)]
        self.assertEqual(overhead.main(args), 2)
        bad = self.dir / "bad.json"
        bad.write_text('{"method": "GET"}')
        self.assertEqual(overhead.main(args[:4] + ["--requests", str(bad)] + args[6:]), 2)
        budget = self.dir / "budget.json"
        budget.write_text('{"p50": 1}')
        self.assertEqual(overhead.main(args + ["--budget", str(budget)]), 2)
        self.assertEqual(overhead.main(args + ["--env", "NOEQUALS"]), 2)

    def test_port_already_taken_exits_2(self):
        holder = subprocess.Popen([sys.executable, str(self.app), str(self.port)])
        try:
            deadline = time.monotonic() + 10
            while not overhead.port_open("127.0.0.1", self.port) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(self.run_main(), 2)
        finally:
            holder.terminate()
            holder.wait()

    def test_ps_time_parsing(self):
        self.assertAlmostEqual(overhead._ps_time("0:01.50"), 1.5)
        self.assertAlmostEqual(overhead._ps_time("01:02:03"), 3723)
        self.assertAlmostEqual(overhead._ps_time("1-00:00:01"), 86401)


if __name__ == "__main__":
    unittest.main()
