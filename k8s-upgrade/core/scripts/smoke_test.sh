#!/usr/bin/env bash
# End-to-end check that new workloads still work after an upgrade. Creates a throwaway
# namespace and deletes it at the end (unless --keep).
#
#   smoke_test.sh [--host HOSTNAME] [--ingress-class CLASS | --gateway NAMESPACE/NAME]
#                 [--storage-class NAME] [--image IMAGE] [--client-image IMAGE] [--keep]
#
# Checks: the PVC binds and the volume is writable (init container), the pod becomes ready,
# another pod reaches the Service by DNS name and gets the token, and the Ingress or
# HTTPRoute is accepted. With --host and an address on the route, it also requests the route
# from this machine. Without --ingress-class or --gateway it uses the default IngressClass,
# else the only IngressClass, else the only Gateway, else skips the route.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASSETS="$SCRIPT_DIR/../assets/smoke"
NS="k8s-upgrade-smoke-$(date +%Y%m%d%H%M%S)"
TOKEN="smoke-$RANDOM$RANDOM"
HOST=""
INGRESS_CLASS=""
GATEWAY=""
STORAGE_CLASS=""
IMAGE="nginxinc/nginx-unprivileged:stable-alpine"
CLIENT_IMAGE="busybox:stable"
KEEP=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --host) HOST=$2; shift 2 ;;
    --ingress-class) INGRESS_CLASS=$2; shift 2 ;;
    --gateway) GATEWAY=$2; shift 2 ;;
    --storage-class) STORAGE_CLASS=$2; shift 2 ;;
    --image) IMAGE=$2; shift 2 ;;
    --client-image) CLIENT_IMAGE=$2; shift 2 ;;
    --keep) KEEP=1; shift ;;
    *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
  esac
done
HOST=${HOST:-$NS.smoke.invalid}

RESULTS=()
FAILED=0
pass() { RESULTS+=("PASS $1"); echo "PASS $1"; }
fail() { RESULTS+=("FAIL $1"); echo "FAIL $1"; FAILED=1; }
skip() { RESULTS+=("SKIP $1"); echo "SKIP $1"; }

render() {
  sed -e "s|__NS__|$NS|g" -e "s|__IMAGE__|$IMAGE|g" -e "s|__CLIENT_IMAGE__|$CLIENT_IMAGE|g" \
      -e "s|__TOKEN__|$TOKEN|g" -e "s|__HOST__|$HOST|g" -e "s|__INGRESS_CLASS__|$INGRESS_CLASS|g" \
      -e "s|__GATEWAY_NS__|${GATEWAY%%/*}|g" -e "s|__GATEWAY_NAME__|${GATEWAY##*/}|g" \
      -e "s|__STORAGE_CLASS_LINE__|$SC_LINE|g" "$1"
}

cleanup() {
  if [[ $KEEP == 1 ]]; then
    echo "kept namespace $NS; delete it with: kubectl delete namespace $NS"
    return
  fi
  kubectl delete namespace "$NS" --wait=true --timeout=5m >/dev/null 2>&1 \
    && echo "deleted namespace $NS" || echo "WARNING namespace $NS was not deleted; delete it by hand"
}

# Storage class
if [[ -n $STORAGE_CLASS ]]; then
  SC_LINE="storageClassName: $STORAGE_CLASS"
elif kubectl get storageclass -o jsonpath='{range .items[*]}{.metadata.annotations.storageclass\.kubernetes\.io/is-default-class}{"\n"}{end}' | grep -qx true; then
  SC_LINE=""
else
  echo "no default StorageClass; pass --storage-class" >&2; exit 2
fi

# Route kind
ROUTE=none
if [[ -n $GATEWAY ]]; then
  ROUTE=httproute
elif [[ -n $INGRESS_CLASS ]]; then
  ROUTE=ingress
else
  DEFAULT_IC=$(kubectl get ingressclass -o jsonpath='{range .items[?(@.metadata.annotations.ingressclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)
  ALL_IC=$(kubectl get ingressclass -o name 2>/dev/null | sed 's|.*/||')
  GWS=$(kubectl get gateways.gateway.networking.k8s.io -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
  if [[ -n $DEFAULT_IC ]]; then INGRESS_CLASS=$DEFAULT_IC; ROUTE=ingress
  elif [[ $(grep -c . <<<"$ALL_IC") == 1 ]]; then INGRESS_CLASS=$ALL_IC; ROUTE=ingress
  elif [[ $(grep -c . <<<"$GWS") == 1 ]]; then GATEWAY=$GWS; ROUTE=httproute
  fi
fi

trap cleanup EXIT
echo "namespace $NS, route $ROUTE${INGRESS_CLASS:+ class $INGRESS_CLASS}${GATEWAY:+ gateway $GATEWAY}"

if render "$ASSETS/app.yaml" | kubectl apply -f - >/dev/null; then pass "apply workload"; else fail "apply workload"; exit 1; fi

if kubectl -n "$NS" rollout status deploy/smoke --timeout=5m >/dev/null 2>&1; then
  pass "pod ready with PVC $(kubectl -n "$NS" get pvc smoke -o jsonpath='{.status.phase} {.spec.volumeName}') written by init container"
else
  fail "pod not ready: $(kubectl -n "$NS" get pods -o wide --no-headers 2>&1 | head -3)"
  kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -10
  exit 1
fi

render "$ASSETS/client.yaml" | kubectl apply -f - >/dev/null
if kubectl -n "$NS" wait job/smoke-client --for=condition=Complete --timeout=3m >/dev/null 2>&1 \
   && [[ $(kubectl -n "$NS" logs job/smoke-client 2>/dev/null | tr -d '\r\n') == "$TOKEN" ]]; then
  pass "service reached by DNS from another pod"
else
  fail "service not reachable from another pod: $(kubectl -n "$NS" logs job/smoke-client 2>&1 | tail -2)"
fi

case $ROUTE in
  ingress)
    render "$ASSETS/ingress.yaml" | kubectl apply -f - >/dev/null
    ADDR=""
    for _ in $(seq 1 24); do
      ADDR=$(kubectl -n "$NS" get ingress smoke -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
      [[ -n $ADDR ]] && break
      sleep 5
    done
    if [[ -n $ADDR ]]; then pass "ingress admitted, address $ADDR"; else skip "ingress has no address in status (controller may not publish one)"; fi ;;
  httproute)
    render "$ASSETS/httproute.yaml" | kubectl apply -f - >/dev/null
    ACCEPTED=""
    for _ in $(seq 1 24); do
      ACCEPTED=$(kubectl -n "$NS" get httproute smoke -o jsonpath='{.status.parents[*].conditions[?(@.type=="Accepted")].status}' 2>/dev/null)
      [[ $ACCEPTED == *True* ]] && break
      sleep 5
    done
    if [[ $ACCEPTED == *True* ]]; then pass "httproute accepted by $GATEWAY"; else fail "httproute not accepted by $GATEWAY"; fi
    ADDR=$(kubectl -n "${GATEWAY%%/*}" get gateways.gateway.networking.k8s.io "${GATEWAY##*/}" -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || true) ;;
  none)
    skip "route: no IngressClass or single Gateway found; pass --ingress-class or --gateway"
    ADDR="" ;;
esac

if [[ $ROUTE != none && -n $ADDR && $HOST != *.smoke.invalid ]]; then
  if [[ $ADDR =~ ^[0-9.]+$ ]]; then RESOLVE=(--resolve "$HOST:80:$ADDR"); else RESOLVE=(--connect-to "$HOST:80:$ADDR:80"); fi
  if [[ $(curl -s --max-time 15 "${RESOLVE[@]}" "http://$HOST/" | tr -d '\r\n') == "$TOKEN" ]]; then
    pass "route answered from this machine"
  else
    fail "route did not answer from this machine (http://$HOST/ via $ADDR)"
  fi
else
  skip "request through the route from this machine (needs --host and an address)"
fi

echo "RESULT $([[ $FAILED == 0 ]] && echo PASS || echo FAIL)"
exit $FAILED
