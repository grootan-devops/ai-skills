#!/usr/bin/env python3
"""Decide whether the cluster is healthy enough to touch the next node. Read-only.

    python3 gate.py [--node NAME] [--expect-version vX.Y.Z] [--pre DIR] [--wait SECONDS] [--out FILE]

Prints PASS, FAIL or SKIP per check and exits 1 if any check failed. The checks and what
passes are in references/gates.md: api-readyz, kubernetes-endpoints, webhooks, node-ready,
node-runtime, node-cni, storage, critical, nodes-cordoned. --wait repeats every 30 seconds until all pass
or the time runs out.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CNI = re.compile(r"(calico|cilium|flannel|canal|weave|antrea|kube-router|kube-ovn)", re.I)


def kubectl(*args: str, timeout: int = 30) -> str:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True, timeout=timeout).stdout


def items(*args: str) -> list[dict]:
    return json.loads(kubectl("get", *args, "-o", "json"))["items"]


def is_ready(obj: dict) -> bool:
    return any(c["type"] == "Ready" and c["status"] == "True" for c in obj.get("status", {}).get("conditions", []))


def check_api() -> tuple[bool, str]:
    out = kubectl("get", "--raw", "/readyz").strip()
    if out == "ok":
        return True, "readyz ok"
    verbose = kubectl("get", "--raw", "/readyz?verbose").strip()
    if verbose:
        return "readyz check passed" in verbose, verbose.splitlines()[-1]
    # Some authenticating proxies in front of the API return an empty body for /readyz.
    live = kubectl("get", "--raw", "/livez").strip()
    return live == "ok", f"readyz body empty (proxy?), livez {live or 'empty'}"


def check_endpoints() -> tuple[bool, str]:
    cps = [n for n in items("nodes", "-l", "node-role.kubernetes.io/control-plane")]
    want = {a["address"] for n in cps for a in n["status"]["addresses"] if a["type"] == "InternalIP"}
    have = set()
    for s in items("endpointslices", "-n", "default", "-l", "kubernetes.io/service-name=kubernetes"):
        for ep in s.get("endpoints") or []:
            have.update(ep.get("addresses", []))
    missing = want - have
    return not missing and bool(have), f"have {sorted(have)}" + (f", missing {sorted(missing)}" if missing else "")


def check_webhooks() -> tuple[bool, str]:
    configs = kubectl("get", "validatingwebhookconfigurations,mutatingwebhookconfigurations", "-o", "json")
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("webhook_scan.py"))],
                       input=configs, capture_output=True, text=True, timeout=300)
    down = [l for l in r.stdout.splitlines() if l.startswith("DOWN")]
    return not down, "; ".join(down) or "every Fail webhook has a ready backend"


def check_node(node: str, version: str | None) -> tuple[bool, str]:
    n = json.loads(kubectl("get", "node", node, "-o", "json"))
    got = n["status"]["nodeInfo"]["kubeletVersion"]
    ok = is_ready(n) and (version is None or got == version)
    return ok, f"Ready={is_ready(n)} kubelet={got}" + (f" expected={version}" if version else "")


def check_runtime(node: str) -> tuple[bool, str]:
    info = json.loads(kubectl("get", "node", node, "-o", "json"))["status"]["nodeInfo"]
    runtime, kubelet = info.get("containerRuntimeVersion", ""), info["kubeletVersion"]
    name, _, version = runtime.partition("://")
    if name == "containerd":
        return True, runtime
    if name == "cri-o":
        minor = lambda v: ".".join(v.lstrip("v").split(".")[:2])
        ok = minor(version) == minor(kubelet)
        return ok, f"{runtime}, kubelet {kubelet}" + ("" if ok else ": CRI-O minor must match the kubelet minor")
    return False, f"{runtime or 'unknown'}: only containerd and CRI-O are supported"


def check_cni(node: str) -> tuple[bool, str]:
    pods = [p for p in items("pods", "-A", "--field-selector", f"spec.nodeName={node}")
            if any(o.get("kind") == "DaemonSet" for o in p["metadata"].get("ownerReferences", []))
            and CNI.search(p["metadata"]["name"])]
    if not pods:
        return False, "no CNI DaemonSet pod found on the node"
    bad = [p["metadata"]["name"] for p in pods if not is_ready(p)]
    return not bad, "not ready: " + ", ".join(bad) if bad else f"{len(pods)} CNI pod(s) ready"


def check_storage() -> tuple[bool, str]:
    problems = []
    for va in items("volumeattachments"):
        st = va.get("status", {})
        for key in ("attachError", "detachError"):
            if st.get(key):
                problems.append(f"{va['spec']['source'].get('persistentVolumeName')} {key}: {st[key].get('message', '')[:120]}")
    by_pv: dict[str, set] = {}
    for va in items("volumeattachments"):
        by_pv.setdefault(va["spec"]["source"].get("persistentVolumeName"), set()).add(va["spec"]["nodeName"])
    problems += [f"{pv} has attachments on {sorted(n)}" for pv, n in by_pv.items() if pv and len(n) > 1]
    detail = []
    try:
        kubectl("get", "crd", "volumes.longhorn.io")
        longhorn = True
    except subprocess.CalledProcessError:
        longhorn = False
    if longhorn:
        vols = items("volumes.longhorn.io", "-A")
        bad = [f"{v['metadata']['name']}={v['status'].get('state')}/{v['status'].get('robustness')}"
               for v in vols if v["status"].get("state") != "detached" and v["status"].get("robustness") != "healthy"]
        ims = [f"{i['spec'].get('nodeID')}={i['status'].get('currentState')}"
               for i in items("instancemanagers.longhorn.io", "-A") if i["status"].get("currentState") != "running"]
        problems += [f"longhorn volume {b}" for b in bad] + [f"longhorn instance-manager {b}" for b in ims]
        detail.append(f"longhorn {len(vols)} volumes")
    return not problems, "; ".join(problems[:15]) + (f" (+{len(problems) - 15} more)" if len(problems) > 15 else "") \
        if problems else ", ".join(detail + ["no VolumeAttachment problems"])


def check_critical(pre: Path) -> tuple[bool, str]:
    before = json.loads((pre / "critical.json").read_text())
    current = {(w["metadata"]["namespace"], w["kind"], w["metadata"]["name"]): w
               for w in items("deployments,statefulsets,daemonsets", "-A")}
    bad = []
    for c in before:
        if c["ready"] < c["desired"]:
            continue  # already failing in the baseline: compare.py reports it
        w = current.get((c["namespace"], c["kind"], c["name"]))
        if w is None:
            bad.append(f"{c['namespace']}/{c['name']} missing")
            continue
        st, spec = w.get("status", {}), w.get("spec", {})
        desired, ready = ((st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)) if w["kind"] == "DaemonSet"
                          else (spec.get("replicas", 1), st.get("readyReplicas", 0)))
        if ready < desired:
            bad.append(f"{c['category']} {c['namespace']}/{c['name']} {ready}/{desired}")
    return not bad, "; ".join(bad) or f"{len(before)} critical workloads as ready as before"


def check_cordoned(node: str | None) -> tuple[bool, str]:
    cordoned = [n["metadata"]["name"] for n in items("nodes") if n["spec"].get("unschedulable") and n["metadata"]["name"] != node]
    return not cordoned, "cordoned: " + ", ".join(cordoned) if cordoned else "no other node cordoned"


def run(args) -> tuple[bool, list[str]]:
    checks = [("api-readyz", check_api), ("kubernetes-endpoints", check_endpoints), ("webhooks", check_webhooks)]
    if args.node:
        checks += [("node-ready", lambda: check_node(args.node, args.expect_version)),
                   ("node-runtime", lambda: check_runtime(args.node)),
                   ("node-cni", lambda: check_cni(args.node))]
    checks.append(("storage", check_storage))
    checks.append(("critical", (lambda: check_critical(args.pre)) if args.pre else None))
    checks.append(("nodes-cordoned", lambda: check_cordoned(args.node)))
    lines, ok_all = [], True
    for name, fn in checks:
        if fn is None:
            lines.append(f"SKIP {name}: no --pre baseline")
            continue
        try:
            ok, detail = fn()
        except Exception as e:  # a check that cannot run is a failed check
            ok, detail = False, f"{type(e).__name__}: {str(e).strip()[:200]}"
        ok_all &= ok
        lines.append(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return ok_all, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node")
    ap.add_argument("--expect-version")
    ap.add_argument("--pre", type=Path)
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args()

    deadline = time.time() + args.wait
    while True:
        ok, lines = run(args)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ok or time.time() >= deadline:
            break
        print(f"{stamp} not yet: " + " | ".join(l for l in lines if l.startswith("FAIL")), file=sys.stderr)
        time.sleep(30)
    report = [f"gate {stamp} node={args.node or '-'}"] + lines + [f"RESULT {'PASS' if ok else 'FAIL'}"]
    print("\n".join(report))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(report) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
