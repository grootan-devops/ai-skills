"""db_stats.py: which containers are databases, and how their clients' output becomes rows."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db_stats  # noqa: E402


def pod(*containers, ready=None):
    ready = {c[0] for c in containers} if ready is None else ready
    return {"spec": {"containers": [{"name": n, "image": i} for n, i in containers]},
            "status": {"containerStatuses": [{"name": n, "ready": n in ready} for n, _ in containers]}}


def exec_result(stdout="", stderr="", rc=0):
    return patch.object(db_stats.subprocess, "run", return_value=subprocess.CompletedProcess([], rc, stdout, stderr))


class DetectTests(unittest.TestCase):
    def test_database_containers_are_found_and_sidecars_skipped(self):
        p = pod(("postgres", "docker.io/library/postgres:16.4"),
                ("metrics", "quay.io/prometheuscommunity/postgres-exporter:v0.15.0"),
                ("cache", "valkey/valkey:8.0"))
        self.assertEqual(db_stats.detect(p), [("postgres", "postgres"), ("cache", "redis")])

    def test_containers_that_are_not_ready_are_not_measured(self):
        p = pod(("mysql", "mysql:8.4"), ready=set())
        self.assertEqual(db_stats.detect(p), [])

    def test_the_image_tag_never_marks_a_container_as_a_sidecar(self):
        p = pod(("clickhouse", "altinity/clickhouse-server:24.3.5.47.altinitystable"))
        self.assertEqual(db_stats.detect(p), [("clickhouse", "clickhouse")])
        self.assertFalse(db_stats.skipped({"name": "db", "image": "postgres@sha256:" + "a" * 64}))

    def test_percona_server_for_mongodb_is_measured_as_mongodb(self):
        p = pod(("mongod", "percona/percona-server-mongodb:7.0.12-7"))
        self.assertEqual(db_stats.detect(p), [("mongod", "mongo")])
        p = pod(("mysql", "percona/percona-server:8.0.36"))
        self.assertEqual(db_stats.detect(p), [("mysql", "mysql")])


class MeasureTests(unittest.TestCase):
    def test_client_output_becomes_one_row_per_database(self):
        with exec_result("app\t104857600\npostgres\t7500000.0\nnoise line\n"):
            rows = db_stats.measure("data", "pg-0", "postgres", "postgres")
        self.assertEqual(rows, [["data", "pg-0", "postgres", "postgres", "app", "104857600", "bytes", "ok"],
                                ["data", "pg-0", "postgres", "postgres", "postgres", "7500000", "bytes", "ok"]])

    def test_redis_counts_keys_and_an_empty_keyspace_is_zero(self):
        with exec_result("db0\t42\n"):
            self.assertEqual(db_stats.measure("c", "r-0", "redis", "redis")[0][4:], ["db0", "42", "keys", "ok"])
        with exec_result(""):
            self.assertEqual(db_stats.measure("c", "r-0", "redis", "redis")[0][4:], ["(empty)", "0", "keys", "ok"])

    def test_unmeasurable_pods_get_one_row_with_the_reason(self):
        cases = [(("NO_CLIENT\n", "", 0), "no-client"),
                 (("NO_CREDENTIALS\n", "", 0), "no-credentials"),
                 (("", "psql: FATAL: password authentication failed for user", 2), "auth-failed"),
                 (("", "connection refused", 1), "error")]
        for (out, err, rc), status in cases:
            with exec_result(out, err, rc):
                self.assertEqual(db_stats.measure("d", "p", "c", "postgres"), [["d", "p", "c", "postgres", "-", "", "", status]])

    def test_a_client_that_hangs_is_an_error(self):
        with patch.object(db_stats.subprocess, "run", side_effect=subprocess.TimeoutExpired("kubectl", 90)):
            self.assertEqual(db_stats.measure("d", "p", "c", "mongo")[0][-1], "error")

    def test_credentials_stay_inside_the_pod(self):
        with exec_result("app\t1\n") as run:
            db_stats.measure("d", "p", "c", "mysql")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:8], ["kubectl", "exec", "-n", "d", "p", "-c", "c", "--"])
        self.assertIn("$MYSQL_PWD", cmd[-1])
        self.assertFalse(any("PASSWORD=" in a for a in cmd[:-1]))


if __name__ == "__main__":
    unittest.main()
