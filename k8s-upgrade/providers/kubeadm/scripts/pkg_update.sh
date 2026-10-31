#!/usr/bin/env bash
# Update operating-system packages on a node without touching Kubernetes.
# Run as root on the node (remote.sh --sudo).
#
#   pkg_update.sh --check              list what would be upgraded
#   pkg_update.sh --apply              upgrade, keeping Kubernetes and the container runtime back
#   pkg_update.sh --apply --runtime    also upgrade containerd, runc and crun (inside a drain window)
#
# Kubernetes packages (kubeadm, kubelet, kubectl, kubernetes-cni, cri-tools) and cri-o are never
# upgraded here; k8s_pkg.sh owns them, because a CRI-O minor follows the Kubernetes minor. On
# apt, packages held before the run stay held after.
# Exit code 3 from --check means a runtime package has an update.
set -euo pipefail

K8S_PKGS=(kubeadm kubelet kubectl kubernetes-cni cri-tools cri-o)
RUNTIME_PKGS=(containerd containerd.io runc crun)

ACTION=""
RUNTIME=0
for ARG in "$@"; do
  case $ARG in
    --check) ACTION=check ;;
    --apply) ACTION=apply ;;
    --runtime) RUNTIME=1 ;;
    *) echo "unknown option $ARG" >&2; exit 2 ;;
  esac
done
[[ -n $ACTION ]] || { echo "usage: pkg_update.sh --check | --apply [--runtime]" >&2; exit 2; }

KEEP=("${K8S_PKGS[@]}")
[[ $RUNTIME == 1 ]] || KEEP+=("${RUNTIME_PKGS[@]}")
RUNTIME_RE="^($(IFS='|'; echo "${RUNTIME_PKGS[*]//./\\.}"))$"

if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  UPGRADABLE=$(apt list --upgradable 2>/dev/null | awk -F/ 'NR>1 {print $1}')
  if [[ $ACTION == check ]]; then
    apt list --upgradable 2>/dev/null | tail -n +2
    if grep -Eq "$RUNTIME_RE" <<<"$UPGRADABLE"; then echo "RUNTIME_UPDATE=yes"; exit 3; fi
    echo "RUNTIME_UPDATE=no"; exit 0
  fi
  INSTALLED_KEEP=()
  for P in "${KEEP[@]}"; do
    dpkg -s "$P" >/dev/null 2>&1 && INSTALLED_KEEP+=("$P")
  done
  HELD_BEFORE=$(apt-mark showhold)
  ADDED_HOLDS=()
  for P in "${INSTALLED_KEEP[@]}"; do
    grep -qx "$P" <<<"$HELD_BEFORE" || ADDED_HOLDS+=("$P")
  done
  [[ ${#ADDED_HOLDS[@]} -gt 0 ]] && apt-mark hold "${ADDED_HOLDS[@]}" >/dev/null
  # Kubernetes packages stay held for good; only runtime holds added here are released.
  release() {
    for P in "${ADDED_HOLDS[@]}"; do
      [[ " ${K8S_PKGS[*]} " == *" $P "* ]] || apt-mark unhold "$P" >/dev/null
    done
  }
  trap release EXIT
  apt-get -y -o Dpkg::Options::=--force-confold upgrade
elif command -v dnf >/dev/null || command -v yum >/dev/null; then
  PM=$(command -v dnf || command -v yum)
  EXCLUDES=()
  for P in "${KEEP[@]}"; do EXCLUDES+=("--exclude=$P*"); done
  if [[ $ACTION == check ]]; then
    UPDATES=$("$PM" -q check-update 2>/dev/null || true)
    echo "$UPDATES"
    if awk '{print $1}' <<<"$UPDATES" | sed 's/\.[^.]*$//' | grep -Eq "$RUNTIME_RE"; then echo "RUNTIME_UPDATE=yes"; exit 3; fi
    echo "RUNTIME_UPDATE=no"; exit 0
  fi
  "$PM" -y upgrade "${EXCLUDES[@]}"
else
  echo "unsupported package manager" >&2; exit 2
fi

if [[ -f /var/run/reboot-required || -f /run/reboot-required ]]; then
  echo "REBOOT_REQUIRED=yes"
elif command -v needs-restarting >/dev/null && ! needs-restarting -r >/dev/null 2>&1; then
  echo "REBOOT_REQUIRED=yes"
else
  echo "REBOOT_REQUIRED=no"
fi
