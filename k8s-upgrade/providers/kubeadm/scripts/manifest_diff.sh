#!/usr/bin/env bash
# Compare a control plane's static pod manifests and kubelet configuration with the backup
# taken before its upgrade. Run as root on the control plane (remote.sh --sudo).
#
#   manifest_diff.sh <backup-dir>
#
# Prints the full diffs, then every changed line that is not an image: line. Exit code 2 when
# that list is not empty: something other than an image tag changed and needs a review.
set -uo pipefail

BACKUP=${1:?usage: manifest_diff.sh <backup-dir>}
BACKUP=${BACKUP%/}
[[ -d $BACKUP/manifests ]] || { echo "$BACKUP/manifests not found" >&2; exit 1; }

MANIFESTS=$(diff -ru "$BACKUP/manifests/" /etc/kubernetes/manifests/)
KUBELET=$(diff -u "$BACKUP/config.yaml" /var/lib/kubelet/config.yaml)
FLAGS=$(diff -u "$BACKUP/kubeadm-flags.env" /var/lib/kubelet/kubeadm-flags.env)

echo "========== CONTROL PLANE MANIFESTS =========="
echo "${MANIFESTS:-no changes}"
echo "========== KUBELET CONFIGURATION =========="
echo "${KUBELET:-no changes}"
echo "========== KUBEADM FLAGS =========="
echo "${FLAGS:-no changes}"

OTHER=$(printf '%s\n%s\n%s\n' "$MANIFESTS" "$KUBELET" "$FLAGS" \
  | grep -E '^[-+]' | grep -Ev '^(\+\+\+|---) ' | grep -Ev '^[-+][[:space:]]*image:[[:space:]]' || true)

echo "========== CHANGES OTHER THAN image: =========="
if [[ -z $OTHER ]]; then
  echo "none"
  exit 0
fi
echo "$OTHER"
echo "========== REMOVED =========="
grep '^-' <<<"$OTHER" || echo "none"
exit 2
