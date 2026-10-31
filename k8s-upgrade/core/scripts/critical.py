#!/usr/bin/env python3
"""List the workloads the cluster and its applications depend on. Read-only.

    python3 critical.py <out-dir> [critical-extra.txt]

Writes <out-dir>/critical.json: every Deployment, StatefulSet and DaemonSet in a critical
category (kube-system, cni, storage, database, operator, policy-security, ingress, gitops,
extra), with its desired and ready replicas at this moment. critical-extra.txt adds
`namespace/kind/name` lines the user marks critical (kind: deployment, statefulset or
daemonset). gate.py and compare.py read the file; the categories are in references/baseline.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Matched against "namespace/name image image ..." of each workload, in this order.
CATEGORIES = [
    ("cni", r"\b(calico|cilium|flannel|canal|weave|antrea|kube-router|kube-ovn|tigera)"),
    ("storage", r"(longhorn|rook|ceph|openebs|portworx|csi|nfs-subdir|local-path|topolvm|democratic)"),
    ("policy-security", r"(kyverno|gatekeeper|cert-manager|vault|external-secrets|falco|trivy|istio|linkerd|sealed-secrets|neuvector)"),
    ("database", r"(postgres|postgis|timescale|spilo|cloudnative-pg|mysql|mariadb|percona|mongo|redis|valkey|clickhouse|cassandra|scylla|elasticsearch|opensearch|/etcd|bitnami/etcd|kafka|redpanda|nats|minio|rustfs|rabbitmq|nebula|neo4j|zookeeper|qdrant|weaviate|milvus)"),
    ("ingress", r"(ingress-nginx|nginx-ingress|traefik|apisix|envoy-gateway|envoyproxy|haproxy|contour|kong|gateway)"),
    ("gitops", r"(argocd|argo-cd|argoproj|fluxcd|flux-system)"),
    ("operator", r"(operator|controller-manager)"),
]


def kubectl_json(*args: str) -> dict:
    out = subprocess.run(["kubectl", "get", *args, "-o", "json"], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def replicas(w: dict) -> tuple[int, int]:
    kind, st, spec = w["kind"], w.get("status", {}), w.get("spec", {})
    if kind == "DaemonSet":
        return st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)
    return spec.get("replicas", 1), st.get("readyReplicas", 0)


def classify(w: dict) -> tuple[str, str] | None:
    ns, name = w["metadata"]["namespace"], w["metadata"]["name"]
    pod = w["spec"]["template"]["spec"]
    images = " ".join(c.get("image", "") for c in pod.get("containers", []) + pod.get("initContainers", []))
    text = f"{ns}/{name} {images}".lower()
    for cat, pattern in CATEGORIES:
        m = re.search(pattern, text)
        if m:
            return cat, m.group(0)
    if ns == "kube-system":
        return "kube-system", "namespace"
    return None


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    extra = set()
    if len(sys.argv) == 3 and Path(sys.argv[2]).is_file():
        for line in Path(sys.argv[2]).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ns, kind, name = line.split("/")
                extra.add((ns, kind.lower(), name))

    items = kubectl_json("deployments,statefulsets,daemonsets", "-A")["items"]
    critical = []
    for w in items:
        ns, name, kind = w["metadata"]["namespace"], w["metadata"]["name"], w["kind"]
        hit = classify(w)
        if (ns, kind.lower(), name) in extra:
            hit = ("extra", "critical-extra.txt")
        if not hit:
            continue
        desired, ready = replicas(w)
        critical.append({"category": hit[0], "reason": hit[1], "namespace": ns, "kind": kind,
                         "name": name, "desired": desired, "ready": ready})
    found = {(c["namespace"], c["kind"].lower(), c["name"]) for c in critical}
    for ns, kind, name in sorted(extra - found):
        print(f"WARNING critical-extra.txt: {ns}/{kind}/{name} not found", file=sys.stderr)

    critical.sort(key=lambda c: (c["category"], c["namespace"], c["name"]))
    (out / "critical.json").write_text(json.dumps(critical, indent=1) + "\n")
    current = None
    for c in critical:
        if c["category"] != current:
            current = c["category"]
            print(f"[{current}]")
        flag = "" if c["ready"] >= c["desired"] else "  NOT READY"
        print(f"  {c['namespace']}/{c['kind'].lower()}/{c['name']} {c['ready']}/{c['desired']}{flag}")
    print(f"{len(critical)} critical workloads -> {out / 'critical.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
