#!/usr/bin/env python3
"""Find admission webhooks that can lock the API server out of its own Service. Read-only.

    kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json \\
      | python3 webhook_scan.py [--out FILE]

When the API server restarts it rewrites the default/kubernetes Endpoints and EndpointSlice
(and its lease in kube-system). A webhook with failurePolicy Fail that intercepts that write
while its own backend is down leaves the Service empty, and nothing in the cluster can reach
the API to recover. Each webhook is reported as:

  RISK  failurePolicy Fail, can intercept endpoints, endpointslices or services in default,
        or leases in kube-system, and no matchCondition exempts system:apiserver
  DOWN  failurePolicy Fail and its Service has no ready endpoint now
  OK    neither

Exit code 1 when anything is RISK or DOWN. --out also writes the result as JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

TARGETS = [  # (api group, resource, namespace)
    ("", "endpoints", "default"),
    ("discovery.k8s.io", "endpointslices", "default"),
    ("", "services", "default"),
    ("coordination.k8s.io", "leases", "kube-system"),
]


def selector_matches(sel: dict | None, labels: dict) -> bool:
    if not sel:
        return True
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in sel.get("matchExpressions") or []:
        key, op, vals = e["key"], e["operator"], e.get("values") or []
        if op == "In" and labels.get(key) not in vals:
            return False
        if op == "NotIn" and labels.get(key) in vals:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def rule_covers(rule: dict, group: str, resource: str) -> bool:
    groups, resources, ops = rule.get("apiGroups", []), rule.get("resources", []), rule.get("operations", [])
    if not ("*" in groups or group in groups):
        return False
    if not ("*" in resources or "*/*" in resources or resource in resources):
        return False
    return "*" in ops or "CREATE" in ops or "UPDATE" in ops


def exempts_apiserver(wh: dict) -> bool:
    for mc in wh.get("matchConditions") or []:
        expr = mc.get("expression", "")
        if "system:apiserver" in expr or "system:masters" in expr:
            return True
    return False


def service_ready(svc: dict) -> bool | None:
    try:
        out = subprocess.run(["kubectl", "get", "endpointslices", "-n", svc["namespace"], "-l",
                              f"kubernetes.io/service-name={svc['name']}", "-o", "json"],
                             check=True, capture_output=True, text=True, timeout=20).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for s in json.loads(out)["items"]:
        for ep in s.get("endpoints") or []:
            if ep.get("conditions", {}).get("ready", True):
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    args = ap.parse_args()
    configs = json.load(sys.stdin)["items"]

    results = []
    for cfg in configs:
        for wh in cfg.get("webhooks") or []:
            fail = wh.get("failurePolicy", "Fail") == "Fail"
            hits = []
            if fail and not exempts_apiserver(wh):
                for group, resource, ns in TARGETS:
                    ns_labels = {"kubernetes.io/metadata.name": ns}
                    if not selector_matches(wh.get("namespaceSelector"), ns_labels):
                        continue
                    if any(rule_covers(r, group, resource) for r in wh.get("rules") or []):
                        hits.append(f"{resource} in {ns}")
            svc = (wh.get("clientConfig") or {}).get("service")
            ready = service_ready(svc) if fail and svc else None
            status = "RISK" if hits else "DOWN" if ready is False else "OK"
            results.append({"status": status, "kind": cfg["kind"], "configuration": cfg["metadata"]["name"],
                            "webhook": wh["name"], "failurePolicy": wh.get("failurePolicy", "Fail"),
                            "intercepts": hits, "service_ready": ready,
                            "matchConditions": [m.get("name") for m in wh.get("matchConditions") or []]})

    for r in sorted(results, key=lambda r: (r["status"] == "OK", r["configuration"])):
        detail = ", ".join(r["intercepts"]) if r["intercepts"] else ""
        if r["service_ready"] is False:
            detail = (detail + "; " if detail else "") + "service has no ready endpoint"
        print(f"{r['status']:5} {r['kind']}/{r['configuration']} {r['webhook']} {r['failurePolicy']} {detail}".rstrip())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
    bad = [r for r in results if r["status"] != "OK"]
    print(f"{len(results)} webhooks, {len(bad)} RISK or DOWN")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
