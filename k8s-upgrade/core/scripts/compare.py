#!/usr/bin/env python3
"""Compare the baseline taken before an upgrade with the one taken after it.

    python3 compare.py <pre-dir> <post-dir> [--target vX.Y.Z] [--out FILE]

Each folder holds the output of snapshot.sh, critical.py, db_stats.py and pvc_usage.py. The
comparison is in references/verify.md: nodes, critical workloads, workload pods (matched by
workload, because pod names change), PVCs and PVs, databases and PVC usage. Prints a Markdown
report with REGRESSION and WARNING lines; exit code 1 when there is a regression.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BAD_STATES = re.compile(r"^(Pending|ContainerCreating|PodInitializing|Init:.*|ImagePullBackOff|ErrImagePull|"
                        r"Evicted|Unknown|Error|CreateContainerConfigError|CreateContainerError|OOMKilled|"
                        r"ContainerStatusUnknown|NodeLost|Terminating)$")
SHRINK = 0.10
PVC_MIN_DROP = 100 * 1024 * 1024


def load(d: Path, name: str) -> list:
    p = d / name
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return data["items"] if isinstance(data, dict) else data


def tsv(d: Path, name: str) -> list[dict]:
    p = d / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def pod_state(p: dict) -> str:
    if p["metadata"].get("deletionTimestamp"):
        return "Terminating"
    st = p.get("status", {})
    if st.get("reason"):
        return st["reason"]
    for cs in st.get("initContainerStatuses", []):
        w = cs.get("state", {}).get("waiting")
        if w:
            return f"Init:{w.get('reason', 'Waiting')}"
    for cs in st.get("containerStatuses", []):
        w = cs.get("state", {}).get("waiting")
        if w:
            return w.get("reason", "Waiting")
        t = cs.get("state", {}).get("terminated")
        if t and st.get("phase") == "Running":
            return t.get("reason", "Error")
    return st.get("phase", "Unknown")


def pod_ready(p: dict) -> bool:
    return any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))


def workload(p: dict) -> str | None:
    """namespace/Kind/name of the pod's workload; None for Job pods, which are skipped."""
    ns, labels = p["metadata"]["namespace"], p["metadata"].get("labels", {})
    for o in p["metadata"].get("ownerReferences", []):
        kind, name = o["kind"], o["name"]
        if kind == "Job":
            return None
        if kind == "ReplicaSet" and "pod-template-hash" in labels:
            kind, name = "Deployment", name[: -(len(labels["pod-template-hash"]) + 1)]
        if kind == "Node":  # static pod: kube-apiserver-<node> and the like
            return f"{ns}/StaticPod/{p['metadata']['name']}"
        return f"{ns}/{kind}/{name}"
    return f"{ns}/Pod/{p['metadata']['name']}"


def summarize(pods: list) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"ready": 0, "states": Counter()})
    for p in pods:
        if p.get("status", {}).get("phase") == "Succeeded":
            continue
        w = workload(p)
        if w is None:
            continue
        out[w]["ready"] += pod_ready(p)
        out[w]["states"][pod_state(p)] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pre", type=Path)
    ap.add_argument("post", type=Path)
    ap.add_argument("--target")
    ap.add_argument("--out")
    args = ap.parse_args()

    reg: list[str] = []
    warn: list[str] = []
    info: list[str] = []

    # Nodes
    pre_nodes = {n["metadata"]["name"]: n for n in load(args.pre, "nodes.json")}
    post_nodes = {n["metadata"]["name"]: n for n in load(args.post, "nodes.json")}
    for name in pre_nodes:
        n = post_nodes.get(name)
        if n is None:
            reg.append(f"node {name} missing")
            continue
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"].get("conditions", []))
        ver = n["status"]["nodeInfo"]["kubeletVersion"]
        if not ready:
            reg.append(f"node {name} not Ready")
        if n["spec"].get("unschedulable"):
            reg.append(f"node {name} still cordoned")
        if args.target and ver != args.target:
            reg.append(f"node {name} kubelet {ver}, expected {args.target}")
        info.append(f"node {name}: {pre_nodes[name]['status']['nodeInfo']['kubeletVersion']} -> {ver}")

    # Critical workloads
    post_crit = {(c["namespace"], c["kind"], c["name"]): c for c in load(args.post, "critical.json")}
    for c in load(args.pre, "critical.json"):
        key = (c["namespace"], c["kind"], c["name"])
        now = post_crit.get(key)
        label = f"critical {c['category']} {c['namespace']}/{c['kind']}/{c['name']}"
        was_ready = c["ready"] >= c["desired"]
        if now is None:
            reg.append(f"{label} missing")
        elif now["ready"] < now["desired"]:
            (reg if was_ready else warn).append(
                f"{label} {now['ready']}/{now['desired']} ready" + ("" if was_ready else f" (pre-existing: {c['ready']}/{c['desired']} before)"))

    # Workload pods
    pre_w, post_w = summarize(load(args.pre, "pods.json")), summarize(load(args.post, "pods.json"))
    for w, before in pre_w.items():
        after = post_w.get(w)
        if after is None:
            if "/Pod/" in w and before["ready"] == 0:
                continue  # a bare pod that was not running before and is gone now
            reg.append(f"{w} has no pods now (had {before['ready']} ready)")
            continue
        if after["ready"] < before["ready"]:
            reg.append(f"{w} ready pods {before['ready']} -> {after['ready']} ({dict(after['states'])})")
        for state, count in after["states"].items():
            if BAD_STATES.match(state) and state not in before["states"]:
                reg.append(f"{w} {count} pod(s) {state} (none before)")
            elif state == "CrashLoopBackOff":
                if "CrashLoopBackOff" in before["states"]:
                    warn.append(f"{w} CrashLoopBackOff (pre-existing)")
                else:
                    reg.append(f"{w} CrashLoopBackOff (new)")
    for w, after in post_w.items():
        if w not in pre_w:
            bad = {s: c for s, c in after["states"].items() if BAD_STATES.match(s) or s == "CrashLoopBackOff"}
            if bad:
                warn.append(f"{w} is new since the baseline and has {bad}")

    # PVCs and PVs
    post_pvc = {(p["metadata"]["namespace"], p["metadata"]["name"]): p for p in load(args.post, "pvcs.json")}
    for p in load(args.pre, "pvcs.json"):
        key = (p["metadata"]["namespace"], p["metadata"]["name"])
        now = post_pvc.get(key)
        if now is None:
            reg.append(f"pvc {key[0]}/{key[1]} missing")
        elif p["status"].get("phase") == "Bound" and now["status"].get("phase") != "Bound":
            reg.append(f"pvc {key[0]}/{key[1]} {now['status'].get('phase')}")
    pv_pre, pv_post = len(load(args.pre, "pvs.json")), len(load(args.post, "pvs.json"))
    if pv_post < pv_pre:
        reg.append(f"PVs {pv_pre} -> {pv_post}")
    info.append(f"pvcs {len(load(args.pre, 'pvcs.json'))} -> {len(post_pvc)}, pvs {pv_pre} -> {pv_post}")

    # Databases
    def db_key(r):  # pods of a StatefulSet keep their names; others are matched by workload prefix
        return (r["namespace"], re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$", "", r["pod"]), r["engine"], r["database"])
    post_db = {db_key(r): r for r in tsv(args.post, "db_stats.tsv")}
    post_db_pods = {(r["namespace"], re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$", "", r["pod"]), r["engine"]): r
                    for r in tsv(args.post, "db_stats.tsv")}
    for r in tsv(args.pre, "db_stats.tsv"):
        if r["status"] != "ok":
            continue
        k = db_key(r)
        now = post_db.get(k)
        label = f"db {r['namespace']}/{r['pod']} {r['engine']} {r['database']}"
        if now is None:
            pod_row = post_db_pods.get(k[:3])
            if pod_row is not None and pod_row["status"] != "ok":
                reg.append(f"{label}: not measurable now ({pod_row['status']})")
            else:
                reg.append(f"{label}: missing")
            continue
        before, after = int(r["size"] or 0), int(now["size"] or 0)
        if before and after < before * (1 - SHRINK):
            warn.append(f"{label}: {before} -> {after} {r['unit']} ({(after - before) * 100 // before}%)")
    info.append(f"databases measured {sum(r['status'] == 'ok' for r in tsv(args.pre, 'db_stats.tsv'))} -> "
                f"{sum(r['status'] == 'ok' for r in tsv(args.post, 'db_stats.tsv'))}")

    # PVC usage
    post_use = {(r["namespace"], r["pvc"]): r for r in tsv(args.post, "pvc_usage.tsv")}
    for r in tsv(args.pre, "pvc_usage.tsv"):
        if not r["used_bytes"]:
            continue
        now = post_use.get((r["namespace"], r["pvc"]))
        label = f"pvc usage {r['namespace']}/{r['pvc']}"
        if now is None or not now["used_bytes"]:
            warn.append(f"{label}: not measured now ({now['source'] if now else 'missing'})")
            continue
        before, after = int(r["used_bytes"]), int(now["used_bytes"])
        if after < before * (1 - SHRINK) and before - after > PVC_MIN_DROP:
            warn.append(f"{label}: {before} -> {after} bytes")

    lines = [f"# Upgrade comparison: {args.pre} -> {args.post}", ""]
    lines += ["## Regressions", ""] + ([f"- REGRESSION {r}" for r in reg] or ["- none"]) + [""]
    lines += ["## Warnings", ""] + ([f"- WARNING {w}" for w in warn] or ["- none"]) + [""]
    lines += ["## Summary", ""] + [f"- {i}" for i in info] + [""]
    lines.append(f"RESULT {'FAIL' if reg else 'PASS'} ({len(reg)} regressions, {len(warn)} warnings)")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 1 if reg else 0


if __name__ == "__main__":
    sys.exit(main())
