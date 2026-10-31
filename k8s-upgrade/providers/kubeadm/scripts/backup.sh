#!/usr/bin/env bash
# Back up a node before its upgrade. Run as root on the node (remote.sh --sudo).
#
#   backup.sh --role control-plane|worker --version <current-version>
#
# Writes /etc/kubernetes-backup-<version>-<YYYYMMDD>/ (or ...-<HHMM> when that exists) and
# prints the folder on the last line. Control planes: /etc/kubernetes in the same layout,
# kubelet files, the repository files, the container runtime configuration (containerd or
# CRI-O), the kubeadm-config ConfigMap and a verified etcd snapshot (stacked etcd). Workers:
# kubelet files, the repository files and the runtime configuration.
set -euo pipefail

ROLE=""
VERSION=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --role) ROLE=$2; shift 2 ;;
    --version) VERSION=${2#v}; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[[ $ROLE == control-plane || $ROLE == worker ]] || { echo "--role control-plane|worker" >&2; exit 2; }
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "--version X.Y.Z" >&2; exit 2; }

BACKUP="/etc/kubernetes-backup-$VERSION-$(date +%Y%m%d)"
[[ -e $BACKUP ]] && BACKUP="$BACKUP-$(date +%H%M)"
[[ -e $BACKUP ]] && { echo "$BACKUP exists" >&2; exit 1; }
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

copy_if() { [[ -e $1 ]] && cp -a "$1" "$2"; return 0; }

if [[ $ROLE == control-plane ]]; then
  # tmp/ holds kubeadm's own earlier backups, including whole etcd data copies.
  tar -C /etc/kubernetes --exclude=./tmp -cf - . | tar -C "$BACKUP" -xpf -
else
  copy_if /etc/kubernetes/kubelet.conf "$BACKUP/"
fi
copy_if /var/lib/kubelet/config.yaml "$BACKUP/"
copy_if /var/lib/kubelet/kubeadm-flags.env "$BACKUP/"
copy_if /var/lib/kubelet/pki "$BACKUP/kubelet-pki"
for D in /etc/systemd/system/kubelet.service.d /usr/lib/systemd/system/kubelet.service.d; do
  if [[ -d $D ]]; then copy_if "$D" "$BACKUP/kubelet.service.d"; break; fi
done
mkdir -p "$BACKUP/repo"
grep -rlsE 'pkgs.k8s.io|cri-o:/stable:' /etc/apt/sources.list.d/ /etc/yum.repos.d/ 2>/dev/null | grep -v '\.bak-' \
  | while read -r F; do cp -a "$F" "$BACKUP/repo/"; done

# Container runtime configuration, and the endpoint crictl uses below.
mkdir -p "$BACKUP/runtime"
copy_if /etc/containerd "$BACKUP/runtime/containerd"
copy_if /etc/crio "$BACKUP/runtime/crio"
copy_if /etc/crictl.yaml "$BACKUP/runtime/"
ENDPOINT=$(grep -oE 'container-runtime-endpoint=[^ "]+' /var/lib/kubelet/kubeadm-flags.env 2>/dev/null | head -1 | cut -d= -f2)
[[ -n $ENDPOINT ]] || ENDPOINT=$(awk '/^containerRuntimeEndpoint:/ {print $2}' /var/lib/kubelet/config.yaml 2>/dev/null)
[[ -n $ENDPOINT ]] && export CONTAINER_RUNTIME_ENDPOINT=$ENDPOINT

if [[ $ROLE == control-plane ]]; then
  export KUBECONFIG=/etc/kubernetes/admin.conf
  kubectl -n kube-system get configmap kubeadm-config -o yaml > "$BACKUP/kubeadm-config.yaml"

  if [[ -f /etc/kubernetes/manifests/etcd.yaml ]]; then
    ETCD_ID=$(crictl ps --name '^etcd$' -q | head -1)
    [[ -n $ETCD_ID ]] || { echo "etcd container not running" >&2; exit 1; }
    SNAP=/var/lib/etcd/upgrade-backup-snapshot.db
    crictl exec "$ETCD_ID" etcdctl --endpoints=https://127.0.0.1:2379 \
      --cacert=/etc/kubernetes/pki/etcd/ca.crt \
      --cert=/etc/kubernetes/pki/etcd/server.crt \
      --key=/etc/kubernetes/pki/etcd/server.key \
      snapshot save "$SNAP" >/dev/null
    mv /var/lib/etcd/upgrade-backup-snapshot.db "$BACKUP/etcd-snapshot.db"
    [[ -s $BACKUP/etcd-snapshot.db ]] || { echo "etcd snapshot is empty" >&2; exit 1; }
    # Verify inside the etcd container, which ships the matching tools.
    cp "$BACKUP/etcd-snapshot.db" /var/lib/etcd/verify-snapshot.db
    STATUS=$(crictl exec "$ETCD_ID" etcdutl snapshot status /var/lib/etcd/verify-snapshot.db -w table 2>/dev/null \
      || crictl exec "$ETCD_ID" etcdctl snapshot status /var/lib/etcd/verify-snapshot.db -w table) \
      || { rm -f /var/lib/etcd/verify-snapshot.db; echo "etcd snapshot does not verify" >&2; exit 1; }
    rm -f /var/lib/etcd/verify-snapshot.db
    echo "$STATUS" | tee "$BACKUP/etcd-snapshot-status.txt"
  else
    echo "external etcd: no snapshot taken here; take one from the etcd cluster" | tee "$BACKUP/etcd-snapshot-status.txt"
  fi
fi

(cd "$BACKUP" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
du -sh "$BACKUP" | awk '{print "size " $1}'
echo "$BACKUP"
