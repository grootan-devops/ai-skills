#!/usr/bin/env bash
# Regenerates chart/README.md with the helm-docs flags the chart's own tpl-library release
# specifies in its chart contract (read from the vendored dependency package).
# Usage: chart-docs.sh [chart-dir] [--check]    --check reports a stale README and changes nothing
set -euo pipefail

CHART_DIR="${1:-chart}"
MODE="${2:-}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

# A command substitution, so a release without a contract stops the script under set -e.
FLAGS="$(python3 "${SCRIPTS}/chart_contract.py" "${CHART_DIR}" helm-docs-args)"
ARGS=(--chart-search-root .)
while IFS= read -r ARG; do
  [ -n "${ARG}" ] && ARGS+=("${ARG}")
done <<< "${FLAGS}"

cd "${CHART_DIR}"
[ -f README.gotmpl ] || { echo "no README.gotmpl in $(pwd)" >&2; exit 1; }

if [ "${MODE}" = "--check" ]; then
  tmp="$(mktemp)"
  trap 'rm -f "${tmp}"' EXIT
  helm-docs "${ARGS[@]}" --dry-run > "${tmp}"
  if python3 -c 'import sys; a, b = (open(p).read().strip() for p in sys.argv[1:3]); sys.exit(0 if a == b else 1)' "${tmp}" README.md; then
    echo "README.md is current"
  else
    echo "README.md is stale: run $0 ${CHART_DIR}" >&2
    exit 1
  fi
else
  helm-docs "${ARGS[@]}"
fi
