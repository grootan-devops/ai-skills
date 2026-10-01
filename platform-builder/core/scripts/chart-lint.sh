#!/usr/bin/env bash
# Lints and renders the chart the way CI does: values.yaml on its own, then with each release overlay.
# Usage: chart-lint.sh [chart-dir]
set -euo pipefail

CHART_DIR="${1:-chart}"

if ! compgen -G "${CHART_DIR}/charts/*.tgz" >/dev/null; then
  helm dependency build "${CHART_DIR}"
fi

echo ">> values.yaml"
helm lint --strict "${CHART_DIR}" -f "${CHART_DIR}/values.yaml"
helm template release "${CHART_DIR}" -f "${CHART_DIR}/values.yaml" >/dev/null

for overlay in "${CHART_DIR}"/values.*.yaml "${CHART_DIR}"/values-*.yaml; do
  [ -e "${overlay}" ] || continue
  echo ">> $(basename "${overlay}")"
  helm lint --strict "${CHART_DIR}" -f "${CHART_DIR}/values.yaml" -f "${overlay}"
  helm template release "${CHART_DIR}" -f "${CHART_DIR}/values.yaml" -f "${overlay}" >/dev/null
done

echo ">> chart lint and render passed"
