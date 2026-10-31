#!/usr/bin/env python3
"""Record the bytes used on every PVC. Read-only.

    python3 pvc_usage.py <out-dir>

Writes <out-dir>/pvc_usage.tsv with the columns
namespace, pvc, pod, node, used_bytes, capacity_bytes, source.

It reads each node's kubelet volume statistics (source=kubelet-stats). For a mounted PVC those
do not cover, it runs df inside a pod that mounts it (source=df). A PVC no running pod mounts
is listed with source=unmounted and no numbers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def kubectl(*args: str, timeout: int = 60) -> str:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True, timeout=timeout).stdout


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    pvcs = json.loads(kubectl("get", "pvc", "-A", "-o", "json"))["items"]
    pods = json.loads(kubectl("get", "pods", "-A", "-o", "json"))["items"]
    nodes = [n["metadata"]["name"] for n in json.loads(kubectl("get", "nodes", "-o", "json"))["items"]]

    # (namespace, pvc) -> (pod, node, container, mountPath) for a running pod that mounts it
    mounts: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for p in pods:
        if p["status"].get("phase") != "Running":
            continue
        ns = p["metadata"]["namespace"]
        vols = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in p["spec"].get("volumes", [])
                if "persistentVolumeClaim" in v}
        for c in p["spec"]["containers"]:
            for m in c.get("volumeMounts", []):
                if m["name"] in vols:
                    mounts.setdefault((ns, vols[m["name"]]), (p["metadata"]["name"], p["spec"].get("nodeName", ""), c["name"], m["mountPath"]))

    usage: dict[tuple[str, str], tuple[str, str, str]] = {}
    for node in nodes:
        try:
            summary = json.loads(kubectl("get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary", timeout=30))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(f"WARNING stats summary unavailable for {node}", file=sys.stderr)
            continue
        for p in summary.get("pods", []):
            for v in p.get("volume", []) or []:
                ref = v.get("pvcRef")
                if ref and "usedBytes" in v:
                    usage[(ref["namespace"], ref["name"])] = (str(v["usedBytes"]), str(v.get("capacityBytes", "")), "kubelet-stats")

    rows = []
    for pvc in pvcs:
        key = (pvc["metadata"]["namespace"], pvc["metadata"]["name"])
        pod, node, container, path = mounts.get(key, ("", "", "", ""))
        if key in usage:
            used, cap, src = usage[key]
        elif pod:
            try:
                df = kubectl("exec", "-n", key[0], pod, "-c", container, "--", "df", "-kP", path, timeout=30).splitlines()[-1].split()
                used, cap, src = str(int(df[2]) * 1024), str(int(df[1]) * 1024), "df"
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError, ValueError):
                used, cap, src = "", "", "unmeasured"
        else:
            used, cap, src = "", "", "unmounted"
        rows.append([key[0], key[1], pod, node, used, cap, src])

    with open(out / "pvc_usage.tsv", "w", encoding="utf-8") as f:
        f.write("namespace\tpvc\tpod\tnode\tused_bytes\tcapacity_bytes\tsource\n")
        for r in sorted(rows):
            f.write("\t".join(r) + "\n")
    measured = sum(bool(r[4]) for r in rows)
    print(f"{len(rows)} PVCs, {measured} measured, {len(rows) - measured} not measured -> {out / 'pvc_usage.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
