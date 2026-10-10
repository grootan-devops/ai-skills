import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contract  # noqa: E402
import hold_port  # noqa: E402


class App(BaseHTTPRequestHandler):
    changed = False

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/orders/missing"):
            body, status = {"error": "not found"}, 404
        else:
            body, status = {"id": "o-1", "total": 25 if not App.changed else 26, "at": self.path}, 200
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), App)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.requests = Path(self.tmp.name) / "requests.json"
        self.requests.write_text(json.dumps([
            {"method": "GET", "path": "/orders/o-1", "headers": {"Authorization": "Bearer secret-value"}},
            {"method": "GET", "path": "/orders/missing"},
        ]))
        App.changed = False

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def run_main(self, *args):
        return contract.main([args[0], "--base", self.base, "--requests", str(self.requests), *args[1:]])

    def test_record_then_compare_unchanged_and_changed(self):
        before = Path(self.tmp.name) / "before.json"
        self.assertEqual(self.run_main("record", "--out", str(before)), 0)
        recorded = json.loads(before.read_text())
        self.assertEqual(recorded["#2 GET /orders/missing"]["status"], 404)
        self.assertEqual(recorded["#1 GET /orders/o-1"]["content_type"], "application/json")
        self.assertEqual(self.run_main("compare", "--against", str(before)), 0)
        App.changed = True
        problems = contract.compare(self.base, contract.load_requests(self.requests), recorded, 5)
        self.assertEqual(list(problems), ["#1 GET /orders/o-1"])
        self.assertNotIn("secret-value", " ".join(problems["#1 GET /orders/o-1"]))

    def test_ignored_keys_and_unreachable_app(self):
        self.assertTrue(contract.same_body('{"a": 1, "at": "x"}', '{"at": "y", "a": 1}', {"at"}))
        self.assertFalse(contract.same_body("plain", "other", set()))
        code = contract.main(["record", "--base", "http://127.0.0.1:9", "--requests", str(self.requests),
                              "--out", str(Path(self.tmp.name) / "x.json"), "--timeout", "1"])
        self.assertEqual(code, 2)


class HoldPortTests(unittest.TestCase):
    def test_port_is_taken_on_ipv4_and_ipv6(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        sockets, held = hold_port.hold(port)
        try:
            self.assertIn(f"0.0.0.0:{port}", held)
            for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1")):
                with socket.socket(family, socket.SOCK_STREAM) as other:
                    with self.assertRaises(OSError):
                        other.bind((host, port))
            if f"[::]:{port}" in held:
                with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as other:
                    with self.assertRaises(OSError):
                        other.bind(("::", port))
        finally:
            for sock in sockets:
                sock.close()


if __name__ == "__main__":
    unittest.main()
