#!/usr/bin/env bash
# Read-only node checks before an upgrade. Run as root on the node (remote.sh --sudo).
# Prints key=value lines, then the certificate table on control planes.
# references/preflight.md says which values stop the run.
set -uo pipefail

kv() { printf '%s=%s\n' "$1" "$2"; }

. /etc/os-release 2>/dev/null || true
kv hostname "$(hostname)"
kv os "${PRETTY_NAME:-unknown}"
kv kernel "$(uname -r)"

if command -v apt-get >/dev/null; then PKG=apt
elif command -v dnf >/dev/null; then PKG=dnf
elif command -v yum >/dev/null; then PKG=yum
else PKG=unsupported; fi
kv pkg_manager "$PKG"

if [[ -z $(swapon --show --noheadings 2>/dev/null) ]]; then kv swap off; else kv swap "on: $(swapon --show --noheadings | awk '{print $1}' | paste -sd, -)"; fi
kv swap_in_fstab "$(grep -Eq '^[^#].*\sswap\s' /etc/fstab && echo yes || echo no)"
kv cgroup "$(stat -fc %T /sys/fs/cgroup/ 2>/dev/null)"

if [[ -e /etc/kubernetes/manifests/kube-apiserver.yaml ]]; then ROLE=control-plane; else ROLE=worker; fi
kv role "$ROLE"

ver() { command -v "$1" >/dev/null && "$@" 2>/dev/null | head -1 || echo missing; }
kv kubeadm "$(ver kubeadm version -o short)"
kv kubelet "$(ver kubelet --version | awk '{print $2}')"
kv kubectl "$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])' 2>/dev/null || echo missing)"
kv kubelet_active "$(systemctl is-active kubelet 2>/dev/null)"

# Container runtime: the endpoint the kubelet uses decides which one is in use.
ENDPOINT=$(grep -oE 'container-runtime-endpoint=[^ "]+' /var/lib/kubelet/kubeadm-flags.env 2>/dev/null | head -1 | cut -d= -f2)
[[ -n $ENDPOINT ]] || ENDPOINT=$(awk '/^containerRuntimeEndpoint:/ {print $2}' /var/lib/kubelet/config.yaml 2>/dev/null)
if [[ -z $ENDPOINT ]]; then
  for S in /run/containerd/containerd.sock /var/run/crio/crio.sock /run/crio/crio.sock; do
    [[ -S $S ]] && { ENDPOINT=unix://$S; break; }
  done
fi
case $ENDPOINT in
  *containerd*) RUNTIME=containerd; SERVICE=containerd ;;
  *crio*) RUNTIME=cri-o; SERVICE=crio ;;
  "") RUNTIME=unknown; SERVICE="" ;;
  *) RUNTIME="unsupported (${ENDPOINT##*/})"; SERVICE="" ;;
esac
kv runtime "$RUNTIME"
kv runtime_endpoint "${ENDPOINT:-none}"
case $RUNTIME in
  containerd) kv runtime_version "$(ver containerd --version | awk '{print $3}')" ;;
  cri-o) kv runtime_version "$(ver crio --version | awk '/^Version:/ {print $2}')" ;;
  *) kv runtime_version unknown ;;
esac
kv runtime_active "$( [[ -n $SERVICE ]] && systemctl is-active "$SERVICE" 2>/dev/null || echo unknown)"
kv runc "$(ver runc --version | awk '/runc version/ {print $3}')"
kv crun "$(ver crun --version | awk '/crun version/ {print $3}')"
kv crictl_ok "$(CONTAINER_RUNTIME_ENDPOINT=$ENDPOINT crictl version >/dev/null 2>&1 && echo yes || echo no)"

# The kubelet and the runtime must use the same cgroup driver.
kv kubelet_cgroup_driver "$(awk '/^cgroupDriver:/ {print $2}' /var/lib/kubelet/config.yaml 2>/dev/null)"
case $RUNTIME in
  containerd)
    if containerd config dump 2>/dev/null | grep -Eq '^\s*SystemdCgroup\s*=\s*true'; then
      kv runtime_cgroup_driver systemd
    else
      kv runtime_cgroup_driver cgroupfs
    fi ;;
  cri-o)
    CM=$(crio config 2>/dev/null | awk -F'"' '/^\s*cgroup_manager\s*=/ {print $2}' | tail -1)
    kv runtime_cgroup_driver "${CM:-systemd}" ;;
  *) kv runtime_cgroup_driver unknown ;;
esac

case $PKG in
  apt)
    LISTS=(/etc/apt/sources.list /etc/apt/sources.list.d/)
    kv holds "$(apt-mark showhold | paste -sd, -)" ;;
  dnf|yum)
    LISTS=(/etc/yum.repos.d/)
    kv holds "excludes: $(grep -rhs '^exclude' /etc/yum.repos.d/ /etc/dnf/dnf.conf /etc/yum.conf | paste -sd, -)" ;;
  *) LISTS=() ;;
esac
REPO=""
CRIO_REPO=""
if [[ ${#LISTS[@]} -gt 0 ]]; then
  REPO=$(grep -rhs 'pkgs.k8s.io/core:/stable:' "${LISTS[@]}" | grep -v '^\s*#' | head -1)
  CRIO_REPO=$(grep -rhs 'cri-o:/stable:' "${LISTS[@]}" | grep -v '^\s*#' | head -1)
fi
if [[ -n $REPO ]]; then
  kv k8s_repo "$(grep -oE 'stable:/v[0-9]+\.[0-9]+' <<<"$REPO" | head -1 | cut -d/ -f2)"
else
  kv k8s_repo missing
fi
if [[ $RUNTIME == cri-o ]]; then
  if [[ -n $CRIO_REPO ]]; then
    kv crio_repo "$(grep -oE 'stable:/v[0-9]+\.[0-9]+' <<<"$CRIO_REPO" | head -1 | cut -d/ -f2)"
  else
    kv crio_repo missing
  fi
fi

kv disk_free_root_mb "$(df -Pm / | awk 'NR==2 {print $4}')"
kv disk_free_var_mb "$(df -Pm /var | awk 'NR==2 {print $4}')"
if [[ -f /var/run/reboot-required || -f /run/reboot-required ]]; then
  kv reboot_required yes
elif command -v needs-restarting >/dev/null && ! needs-restarting -r >/dev/null 2>&1; then
  kv reboot_required yes
else
  kv reboot_required no
fi

if [[ $ROLE == control-plane ]] && command -v kubeadm >/dev/null; then
  CERTS=$(kubeadm certs check-expiration 2>/dev/null)
  MIN_DAYS=$(awk '/^[a-z].*[0-9]+d/ {for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+d$/) {sub("d","",$i); print $i}}' <<<"$CERTS" | sort -n | head -1)
  kv cert_min_days "${MIN_DAYS:-unknown}"
  ETCD=$(grep -h 'image:' /etc/kubernetes/manifests/etcd.yaml 2>/dev/null | awk '{print $2}')
  kv etcd "${ETCD:-external}"
  echo "--- kubeadm certs check-expiration"
  echo "$CERTS"
fi
