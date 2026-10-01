#!/usr/bin/env bash
# Regenerates chart/README.md with the one helm-docs invocation the library specifies.
# Usage: chart-docs.sh [chart-dir] [--check]    --check reports a stale README and changes nothing
set -euo pipefail

CHART_DIR="${1:-chart}"
MODE="${2:-}"
cd "${CHART_DIR}"
[ -f README.gotmpl ] || { echo "no README.gotmpl in $(pwd)" >&2; exit 1; }

ARGS=(--chart-search-root . --template-files README.gotmpl --sort-values-order file --document-dependency-values)

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
