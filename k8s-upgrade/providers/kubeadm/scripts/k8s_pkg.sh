#!/usr/bin/env bash
# Manage the Kubernetes and CRI-O packages on a node. Run as root (remote.sh --sudo).
#
#   k8s_pkg.sh versions <MAJOR.MINOR> [kubeadm|cri-o]   list versions offered (changes nothing)
#   k8s_pkg.sh repo <MAJOR.MINOR>                       point the Kubernetes repository, and the
#                                                       CRI-O repository when the node has one, at that minor
#   k8s_pkg.sh install kubeadm <X.Y.Z>                  unhold, install exactly that kubeadm, hold
#   k8s_pkg.sh install kubelet <X.Y.Z>                  same for kubelet and kubectl, then restart the kubelet
#   k8s_pkg.sh install cri-o <MAJOR.MINOR|X.Y.Z>        same for cri-o (latest patch of a minor), then restart crio
#
# Versions are given without a leading v. Kubernetes has one repository per minor on
# pkgs.k8s.io (core:/stable:/vX.Y). CRI-O has one per minor too, on pkgs.k8s.io
# (addons:/cri-o:/stable:/vX.Y, up to 1.32) or the openSUSE build service
# (isv:/cri-o:/stable:/vX.Y, from 1.33), and a CRI-O minor matches the Kubernetes minor it
# supports, so `repo` moves both together. `repo` refuses a switch to a repository that has no
# packages for the minor (a host change needs a new repository line and signing key) and keeps
# each previous file as <file>.bak-<previous minor>. Downgrades are allowed, for recovery.
set -euo pipefail

die() { echo "k8s_pkg.sh: $*" >&2; exit 2; }
[[ $# -ge 2 ]] || die "usage: versions <MAJOR.MINOR> [kubeadm|cri-o] | repo <MAJOR.MINOR> | install kubeadm|kubelet|cri-o <version>"

if command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null; then PM=dnf
elif command -v yum >/dev/null; then PM=yum
else die "unsupported package manager"; fi

if [[ $PM == apt ]]; then REPO_DIRS=(/etc/apt/sources.list.d/ /etc/apt/sources.list); FORMAT=deb
else REPO_DIRS=(/etc/yum.repos.d/); FORMAT=rpm; fi

# The repository file whose active lines contain the pattern: core:/stable: or cri-o:/stable:.
repo_file() {
  local F
  for F in $(grep -rls "$1" "${REPO_DIRS[@]}" 2>/dev/null | grep -v '\.bak-'); do
    grep -v '^\s*#' "$F" | grep -q "$1" && { echo "$F"; return; }
  done
}

# Repository base URL for a minor, taken from the node's own file; empty when it has none.
node_repo_url() {
  local PATTERN=$1 MINOR=$2 F URL
  F=$(repo_file "$PATTERN")
  [[ -n $F ]] || return 0
  URL=$(grep -v '^\s*#' "$F" | grep -oE "https?://[^ ]*${PATTERN}/v[0-9]+\.[0-9]+/[^ ]*" | head -1)
  [[ -n $URL ]] && sed -E "s|${PATTERN}/v[0-9]+\.[0-9]+/|${PATTERN}/v${MINOR}/|" <<<"${URL%/}/"
  return 0
}

# Where a minor's packages may be published: the node's configured host first, then the known
# hosts. CRI-O moved from pkgs.k8s.io (up to 1.32) to the openSUSE build service (1.33 on).
candidate_urls() {
  local KIND=$1 MINOR=$2
  if [[ $KIND == cri-o ]]; then
    { node_repo_url "$CRIO" "$MINOR"
      echo "https://pkgs.k8s.io/addons:/cri-o:/stable:/v$MINOR/$FORMAT/"
      echo "https://download.opensuse.org/repositories/isv:/cri-o:/stable:/v$MINOR/$FORMAT/"; } | awk 'NF && !seen[$0]++'
  else
    { node_repo_url "$K8S" "$MINOR"
      echo "https://pkgs.k8s.io/core:/stable:/v$MINOR/$FORMAT/"; } | awk 'NF && !seen[$0]++'
  fi
}

# Prints the versions and sets FOUND_URL to the repository that had them.
FOUND_URL=""
find_versions() {
  local KIND=$1 MINOR=$2 PKG URL LIST
  PKG=$([[ $KIND == cri-o ]] && echo cri-o || echo kubeadm)
  for URL in $(candidate_urls "$KIND" "$MINOR"); do
    LIST=$(list_versions "$PKG" "$URL")
    if [[ -n $LIST ]]; then FOUND_URL=$URL; echo "$LIST"; return 0; fi
  done
  return 1
}

list_versions() {
  local PKG=$1 URL=$2
  if [[ $PM == apt ]]; then
    curl -fsSL "${URL}Packages" 2>/dev/null \
      | awk -v pkg="$PKG" '$0 == "Package: " pkg {p=1; next} /^Package:/ {p=0} p && /^Version:/ {print $2}' \
      | sort -uV || true
  else
    "$PM" -q --repofrompath="probe,$URL" --repo=probe --nogpgcheck list --showduplicates "$PKG" 2>/dev/null \
      | awk -v pkg="$PKG" 'index($1, pkg) == 1 {print $2}' | sort -uV || true
  fi
}

switch_repo() {
  local PATTERN=$1 MINOR=$2 KIND=$3 F CURRENT URL
  F=$(repo_file "$PATTERN")
  [[ -n $F ]] || return 1
  CURRENT=$(grep -v '^\s*#' "$F" | grep -oE "${PATTERN}/v[0-9]+\.[0-9]+" | head -1 | grep -oE 'v[0-9.]+$')
  if [[ $CURRENT == "v$MINOR" ]]; then
    echo "$F already on v$MINOR"
    return 0
  fi
  # Refuse a switch to a repository with no packages: the minor may live on another host,
  # which needs a new repository line and its own signing key, not just a new version.
  URL=$(node_repo_url "$PATTERN" "$MINOR")
  if [[ -z $(list_versions "$([[ $KIND == cri-o ]] && echo cri-o || echo kubeadm)" "$URL") ]]; then
    if find_versions "$KIND" "$MINOR" >/dev/null; then
      die "$F: $URL has no $KIND packages; v$MINOR is published at $FOUND_URL. Replace the repository line and its signing key (from that repository's Release.key) by hand, then run repo again."
    fi
    die "no $KIND packages for v$MINOR on any known repository"
  fi
  cp -a "$F" "$F.bak-$CURRENT"
  sed -i -E "s|${PATTERN}/v[0-9]+\.[0-9]+/|${PATTERN}/v${MINOR}/|g" "$F"
  echo "$F: $CURRENT -> v$MINOR (previous kept as $F.bak-$CURRENT)"
}

refresh() {
  if [[ $PM == apt ]]; then apt-get update -qq; else "$PM" -q makecache >/dev/null; fi
}

# Unhold, install the given package specs, hold again.
install_held() {
  local SPECS=("$@") NAMES=() S
  for S in "${SPECS[@]}"; do NAMES+=("${S%%=*}"); done
  if [[ $PM == apt ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-mark unhold "${NAMES[@]}" >/dev/null
    apt-get install -y --allow-downgrades --allow-change-held-packages -o Dpkg::Options::=--force-confold "${SPECS[@]}"
    apt-mark hold "${NAMES[@]}" >/dev/null
  else
    local RPM=()
    for S in "${SPECS[@]}"; do RPM+=("${S%%=*}-${S#*=}"); done
    RPM=("${RPM[@]/%-\*/}")
    "$PM" install -y --disableexcludes=all "${RPM[@]}" || "$PM" downgrade -y --disableexcludes=all "${RPM[@]}"
  fi
}

check_minor() { [[ $1 =~ ^[0-9]+\.[0-9]+$ ]] || die "expected MAJOR.MINOR, got $1"; }
check_version() { [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "expected X.Y.Z without v, got $1"; }

K8S=core:/stable:
CRIO=cri-o:/stable:

case $1 in
  versions)
    check_minor "$2"
    KIND=${3:-kubeadm}
    [[ $KIND == kubeadm || $KIND == cri-o ]] || die "versions takes kubeadm or cri-o"
    if ! find_versions "$KIND" "$2"; then
      echo "no $KIND packages for v$2 (the minor is not released yet, or the node cannot reach the repository)" >&2
      exit 1
    fi
    CONFIGURED=$(node_repo_url "$([[ $KIND == cri-o ]] && echo "$CRIO" || echo "$K8S")" "$2")
    echo "# from $FOUND_URL" >&2
    if [[ -n $CONFIGURED && $CONFIGURED != "$FOUND_URL" ]]; then
      echo "# NOTE: the node's repository points at $CONFIGURED; switching to v$2 needs a new repository line and signing key" >&2
    fi
    ;;

  repo)
    check_minor "$2"
    # CRI-O first: if its repository cannot move, nothing has changed yet.
    if [[ -n $(repo_file "$CRIO") ]]; then
      switch_repo "$CRIO" "$2" cri-o
    fi
    switch_repo "$K8S" "$2" kubeadm || die "no pkgs.k8s.io Kubernetes repository configured"
    refresh
    ;;

  install)
    [[ $# -eq 3 ]] || die "usage: install kubeadm|kubelet|cri-o <version>"
    case $2 in
      kubeadm)
        check_version "$3"
        install_held "kubeadm=$3-*"
        echo "kubeadm $(kubeadm version -o short)" ;;
      kubelet)
        check_version "$3"
        install_held "kubelet=$3-*" "kubectl=$3-*"
        systemctl daemon-reload
        systemctl restart kubelet
        sleep 5
        echo "kubelet $(kubelet --version | awk '{print $2}') $(systemctl is-active kubelet)" ;;
      cri-o)
        if [[ $3 =~ ^[0-9]+\.[0-9]+$ ]]; then
          URL=$(node_repo_url "$CRIO" "$3")
          [[ -n $URL ]] || die "no CRI-O repository configured on this node"
          VER=$(list_versions cri-o "$URL" | grep -E "^$3\.[0-9]+" | tail -1)
          [[ -n $VER ]] || die "no cri-o packages for v$3 at $URL (run repo $3 first)"
          VER=${VER%%-*}
        else
          check_version "$3"
          VER=$3
        fi
        install_held "cri-o=$VER*"
        systemctl daemon-reload
        systemctl restart crio
        for _ in $(seq 1 12); do
          crictl --runtime-endpoint unix:///var/run/crio/crio.sock info >/dev/null 2>&1 && break
          sleep 5
        done
        echo "cri-o $(crio --version | awk '/^Version:/ {print $2}') $(systemctl is-active crio)" ;;
      *) die "install takes kubeadm, kubelet or cri-o" ;;
    esac
    ;;

  *) die "unknown command $1" ;;
esac
