#!/usr/bin/env python3
"""Record the databases and their sizes in every running database pod. Read-only.

    python3 db_stats.py <out-dir> [--namespace NS]

Writes <out-dir>/db_stats.tsv with the columns
namespace, pod, container, engine, database, size, unit, status.

Engines: PostgreSQL, MySQL/MariaDB, MongoDB, ClickHouse (size in bytes) and Redis/Valkey
(size in keys). The engine's own client runs inside the database container, and the query
reads its credentials from that container's environment (POSTGRES_PASSWORD,
MARIADB_ROOT_PASSWORD, MONGO_INITDB_ROOT_PASSWORD, CLICKHOUSE_PASSWORD, REDIS_PASSWORD and
the like), which Kubernetes fills from the database's Secrets. The variables expand inside the
pod: no credential is read out of the cluster or printed. A pod that cannot be measured gets
one row with status no-client, auth-failed, no-credentials or error; references/baseline.md
says how to measure it by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# engine: (image pattern, shell script run in the container; prints "database<TAB>size")
ENGINES = {
    "postgres": (r"(postgres|postgis|timescale|spilo|cloudnative-pg/postgresql|percona-postgresql)", r"""
U="${POSTGRES_USER:-${PGUSER:-postgres}}"
export PGPASSWORD="${POSTGRES_PASSWORD:-${PGPASSWORD:-}}"
Q="select datname, pg_database_size(datname) from pg_database where datallowconn and not datistemplate"
command -v psql >/dev/null || { echo NO_CLIENT; exit 0; }
psql -U "$U" -d postgres -AtF "$(printf '\t')" -c "$Q" 2>/dev/null \
  || psql -h 127.0.0.1 -U "$U" -d postgres -AtF "$(printf '\t')" -c "$Q"
"""),
    "mysql": (r"(mysql|mariadb|percona-server(?!-mongodb)|percona-xtradb)", r"""
C=$(command -v mariadb || command -v mysql) || { echo NO_CLIENT; exit 0; }
export MYSQL_PWD="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-${MARIADB_PASSWORD:-${MYSQL_PASSWORD:-}}}}"
U=root
[ -z "${MARIADB_ROOT_PASSWORD:-}${MYSQL_ROOT_PASSWORD:-}" ] && [ -n "${MARIADB_USER:-${MYSQL_USER:-}}" ] && U="${MARIADB_USER:-$MYSQL_USER}"
[ -z "$MYSQL_PWD" ] && { echo NO_CREDENTIALS; exit 0; }
"$C" -u "$U" -N -B -e "select s.schema_name, coalesce(sum(t.data_length + t.index_length), 0) from information_schema.schemata s left join information_schema.tables t on t.table_schema = s.schema_name group by s.schema_name"
"""),
    "mongo": (r"(mongo|percona-server-mongodb)", r"""
C=$(command -v mongosh || command -v mongo) || { echo NO_CLIENT; exit 0; }
U="${MONGO_INITDB_ROOT_USERNAME:-${MONGODB_ROOT_USER:-root}}"
P="${MONGO_INITDB_ROOT_PASSWORD:-${MONGODB_ROOT_PASSWORD:-}}"
E='db.adminCommand({listDatabases: 1}).databases.forEach(function (d) { print(d.name + "\t" + d.sizeOnDisk) })'
if [ -n "$P" ]; then
  "$C" --quiet -u "$U" -p "$P" --authenticationDatabase admin --eval "$E"
else
  "$C" --quiet --eval "$E"
fi
"""),
    "clickhouse": (r"clickhouse-server|clickhouse/clickhouse|/clickhouse:", r"""
command -v clickhouse-client >/dev/null || { echo NO_CLIENT; exit 0; }
clickhouse-client --user "${CLICKHOUSE_USER:-default}" --password "${CLICKHOUSE_PASSWORD:-}" -q "SELECT d.name, sum(p.bytes_on_disk) FROM system.databases AS d LEFT JOIN (SELECT database, bytes_on_disk FROM system.parts WHERE active) AS p ON p.database = d.name GROUP BY d.name FORMAT TSV"
"""),
    "redis": (r"(redis|valkey)", r"""
C=$(command -v redis-cli || command -v valkey-cli) || { echo NO_CLIENT; exit 0; }
export REDISCLI_AUTH="${REDIS_PASSWORD:-${VALKEY_PASSWORD:-}}"
"$C" info keyspace | tr -d '\r' | awk -F'[:=,]' '/^db[0-9]+:/ {print $1 "\t" $3}'
"""),
}
UNITS = {"redis": "keys"}
# Sidecars and tools that run a database image or name but hold no data. Matched against the
# words of the container name and the image repository (never the tag: "altinitystable"
# would match "init").
SKIP = re.compile(r"^(exporter|operator|sentinel|pgbouncer|proxy|backup|metrics|init|bootstrap|keeper|"
                  r"express|admin|pgadmin|phpmyadmin|commander|agent|ui|insight|redisinsight)$", re.I)


def skipped(container: dict) -> bool:
    repo = container["image"].split("@")[0].rsplit(":", 1)[0] if ":" in container["image"].split("/")[-1] \
        else container["image"].split("@")[0]
    words = re.split(r"[-_./]", f"{container['name']}-{repo.split('/')[-1]}")
    return any(SKIP.match(w) for w in words)
AUTH_ERR = re.compile(r"(authentication failed|access denied|auth|wrongpass|noauth|password)", re.I)


def detect(pod: dict) -> list[tuple[str, str]]:
    ready = {c["name"] for c in pod["status"].get("containerStatuses", []) if c.get("ready")}
    found = []
    for c in pod["spec"]["containers"]:
        if c["name"] not in ready or skipped(c):
            continue
        for engine, (pattern, _) in ENGINES.items():
            if re.search(pattern, c["image"], re.I):
                found.append((c["name"], engine))
                break
    return found


def measure(ns: str, pod: str, container: str, engine: str) -> list[list[str]]:
    script = ENGINES[engine][1]
    base = [ns, pod, container, engine]
    try:
        r = subprocess.run(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "sh", "-c", script],
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return [base + ["-", "", "", "error"]]
    out = r.stdout.strip()
    if "NO_CLIENT" in out or "executable file not found" in r.stderr:
        return [base + ["-", "", "", "no-client"]]
    if "NO_CREDENTIALS" in out:
        return [base + ["-", "", "", "no-credentials"]]
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and re.fullmatch(r"\d+(\.\d+)?", parts[1].strip()):
            rows.append(base + [parts[0].strip(), str(int(float(parts[1]))), UNITS.get(engine, "bytes"), "ok"])
    if rows:
        return rows
    if r.returncode == 0 and engine == "redis":
        return [base + ["(empty)", "0", "keys", "ok"]]
    status = "auth-failed" if AUTH_ERR.search(r.stderr + out) else "error"
    return [base + ["-", "", "", status]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--namespace")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    scope = ["-n", args.namespace] if args.namespace else ["-A"]
    pods = json.loads(subprocess.run(["kubectl", "get", "pods", *scope, "-o", "json"],
                                     check=True, capture_output=True, text=True).stdout)["items"]
    rows = []
    for p in pods:
        if p["status"].get("phase") != "Running":
            continue
        for container, engine in detect(p):
            got = measure(p["metadata"]["namespace"], p["metadata"]["name"], container, engine)
            rows.extend(got)
            ok = sum(r[-1] == "ok" for r in got)
            print(f"{p['metadata']['namespace']}/{p['metadata']['name']} {engine}: "
                  f"{ok} database(s)" if ok else f"{p['metadata']['namespace']}/{p['metadata']['name']} {engine}: {got[0][-1]}")

    header = ["namespace", "pod", "container", "engine", "database", "size", "unit", "status"]
    with open(args.out_dir / "db_stats.tsv", "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    failed = sum(r[-1] != "ok" for r in rows)
    print(f"{len(rows) - failed} database rows measured, {failed} pod(s) not measured -> {args.out_dir / 'db_stats.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
