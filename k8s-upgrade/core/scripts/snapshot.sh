#!/usr/bin/env bash
# Save the cluster state as JSON for a later comparison. Read-only; uses the current
# kubectl context.
#
#   snapshot.sh <out-dir>
#
# Writes nodes, pods, pvcs, pvs, volumeattachments, workloads, webhooks, storageclasses,
# version and, when Longhorn is installed, longhorn-volumes as .json files, plus summary.txt.
set -euo pipefail

OUT=${1:?usage: snapshot.sh <out-dir>}
mkdir -p "$OUT"

get() { kubectl get "$@" -o json; }

kubectl version -o json > "$OUT/version.json"
get nodes > "$OUT/nodes.json"
get pods -A > "$OUT/pods.json"
get pvc -A > "$OUT/pvcs.json"
get pv > "$OUT/pvs.json"
get volumeattachments > "$OUT/volumeattachments.json"
get deployments,statefulsets,daemonsets -A > "$OUT/workloads.json"
get validatingwebhookconfigurations,mutatingwebhookconfigurations > "$OUT/webhooks.json"
get storageclasses > "$OUT/storageclasses.json"
if kubectl get crd volumes.longhorn.io >/dev/null 2>&1; then
  get volumes.longhorn.io -A > "$OUT/longhorn-volumes.json"
fi
date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/taken-at.txt"

python3 - "$OUT" <<'PY' | tee "$OUT/summary.txt"
import collections, json, sys
from pathlib import Path

out = Path(sys.argv[1])
load = lambda n: json.loads((out / n).read_text())["items"] if (out / n).exists() else []

nodes = load("nodes.json")
print(f"nodes: {len(nodes)}")
for n in nodes:
    ready = next((c["status"] for c in n["status"].get("conditions", []) if c["type"] == "Ready"), "?")
    cordon = " cordoned" if n["spec"].get("unschedulable") else ""
    print(f"  {n['metadata']['name']}: Ready={ready} kubelet={n['status']['nodeInfo']['kubeletVersion']}{cordon}")

def pod_state(p):
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
    return st.get("phase", "Unknown")

pods = load("pods.json")
states = collections.Counter(pod_state(p) for p in pods)
print(f"pods: {len(pods)}")
for s, c in sorted(states.items()):
    print(f"  {s}: {c}")

pvcs = load("pvcs.json")
print(f"pvcs: {len(pvcs)}")
for s, c in sorted(collections.Counter(p["status"].get("phase", "?") for p in pvcs).items()):
    print(f"  {s}: {c}")
print(f"pvs: {len(load('pvs.json'))}")
vas = load("volumeattachments.json")
print(f"volumeattachments: {len(vas)} (attached {sum(bool(v.get('status', {}).get('attached')) for v in vas)})")
lh = load("longhorn-volumes.json")
if lh:
    c = collections.Counter(f"{v['status'].get('state')}/{v['status'].get('robustness')}" for v in lh)
    print(f"longhorn volumes: {len(lh)}")
    for s, n in sorted(c.items()):
        print(f"  {s}: {n}")
PY
