#!/usr/bin/env bash
# Run a command or a local script on a cluster node over SSH, optionally as root.
#
#   remote.sh <cluster-dir> <node> [--sudo] -- <command...>
#   remote.sh <cluster-dir> <node> [--sudo] --script <local-script> [args...]
#
# <cluster-dir> holds control-plane.md and workers.md (references/workspace.md). The SSH
# password goes to sshpass through SSHPASS; the sudo password goes to `sudo -S` on the first
# line of standard input, ahead of the script. Neither appears on a command line. A node with
# passwordless sudo gets no password line. With --sudo, KUBECONFIG is set to
# /etc/kubernetes/admin.conf when that file exists, so kubectl works on control planes.
set -euo pipefail

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
[[ $# -ge 3 ]] || usage

CLUSTER_DIR=$1
NODE=$2
shift 2
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
NODES_PY="$SCRIPT_DIR/nodes.py"
CP_FILE="$CLUSTER_DIR/control-plane.md"
WORKER_FILE="$CLUSTER_DIR/workers.md"

USE_SUDO=0
MODE=""
SCRIPT_FILE=""
ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --sudo) USE_SUDO=1; shift ;;
    --script) MODE=script; SCRIPT_FILE=$2; shift 2; ARGS=("$@"); break ;;
    --) shift; MODE=cmd; ARGS=("$@"); break ;;
    *) usage ;;
  esac
done
[[ -n $MODE ]] || usage
if [[ $MODE == script && ! -r $SCRIPT_FILE ]]; then
  echo "remote.sh: cannot read $SCRIPT_FILE" >&2; exit 2
fi

field() { python3 "$NODES_PY" --get "$NODE" "$1" "$CP_FILE" "$WORKER_FILE"; }

HOST=$(field host)
PORT=$(field port)
SSH_USER=$(field user)
AUTH=$(field auth)

SSH_OPTS=(-p "$PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15
          -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o LogLevel=ERROR)
if [[ $AUTH == key ]]; then
  SSH_CMD=(ssh -i "$(field ssh_key_path)" -o BatchMode=yes "${SSH_OPTS[@]}")
else
  command -v sshpass >/dev/null || { echo "remote.sh: sshpass is required for password auth" >&2; exit 2; }
  SSHPASS=$(field password)
  export SSHPASS
  SSH_CMD=(sshpass -e ssh -o PubkeyAuthentication=no "${SSH_OPTS[@]}")
fi
TARGET="$SSH_USER@$HOST"

# Arguments for the remote bash, quoted for the remote shell.
REMOTE_ARGS=""
if [[ $MODE == script && ${#ARGS[@]} -gt 0 ]]; then
  REMOTE_ARGS=$(printf '%q ' "${ARGS[@]}")
fi

payload() {
  if [[ $USE_SUDO == 1 ]]; then
    printf '%s\n' '[ -r /etc/kubernetes/admin.conf ] && export KUBECONFIG=/etc/kubernetes/admin.conf'
  fi
  if [[ $MODE == script ]]; then
    cat "$SCRIPT_FILE"
  else
    printf '%s\n' "${ARGS[*]}"
  fi
}

if [[ $USE_SUDO == 0 ]]; then
  payload | "${SSH_CMD[@]}" "$TARGET" "bash -s -- $REMOTE_ARGS"
  exit $?
fi

if "${SSH_CMD[@]}" "$TARGET" 'sudo -n true' </dev/null >/dev/null 2>&1; then
  payload | "${SSH_CMD[@]}" "$TARGET" "sudo -n bash -s -- $REMOTE_ARGS"
  exit $?
fi

SUDO_PASSWORD=$(field sudo_password)
if [[ -z $SUDO_PASSWORD ]]; then
  echo "remote.sh: $NODE needs a sudo password and none is set" >&2; exit 2
fi
{ printf '%s\n' "$SUDO_PASSWORD"; payload; } | "${SSH_CMD[@]}" "$TARGET" "sudo -S -p '' bash -s -- $REMOTE_ARGS"
