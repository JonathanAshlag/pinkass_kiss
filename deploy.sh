#!/usr/bin/env bash
# Deploys dashboards/pinkas-ops.json to a Kibana instance via PUT (upsert).
#
# Required env vars:
#   KIBANA_URL            e.g. http://localhost:5601
# Auth (one of):
#   KIBANA_API_KEY                          (preferred)
#   KIBANA_USERNAME + KIBANA_PASSWORD       (basic auth, e.g. local dev)
# Optional:
#   INDEX_PREFIX          defaults to "pinkas-events" -> queries "${INDEX_PREFIX}-*"
#   KIBANA_SPACE_ID        defaults to "default"
#
# Usage:
#   KIBANA_URL=https://kibana.example.com KIBANA_API_KEY=... ./deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_FILE="${SCRIPT_DIR}/dashboards/pinkas-ops.json"
DASHBOARD_ID="pinkas-ops"
INDEX_PREFIX="${INDEX_PREFIX:-pinkas-events}"
INDEX_PATTERN="${INDEX_PREFIX}-*"
API_VERSION="2023-10-31"

if [[ -z "${KIBANA_URL:-}" ]]; then
  echo "Error: KIBANA_URL is not set." >&2
  exit 1
fi

if [[ -z "${KIBANA_API_KEY:-}" && ( -z "${KIBANA_USERNAME:-}" || -z "${KIBANA_PASSWORD:-}" ) ]]; then
  echo "Error: set KIBANA_API_KEY, or both KIBANA_USERNAME and KIBANA_PASSWORD." >&2
  exit 1
fi

if [[ ! -f "$DASHBOARD_FILE" ]]; then
  echo "Error: dashboard file not found at $DASHBOARD_FILE" >&2
  exit 1
fi

AUTH_HEADER=()
if [[ -n "${KIBANA_API_KEY:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: ApiKey ${KIBANA_API_KEY}")
else
  AUTH_HEADER=(-u "${KIBANA_USERNAME}:${KIBANA_PASSWORD}")
fi

BASE_URL="${KIBANA_URL%/}"
if [[ -n "${KIBANA_SPACE_ID:-}" && "${KIBANA_SPACE_ID}" != "default" ]]; then
  BASE_URL="${BASE_URL}/s/${KIBANA_SPACE_ID}"
fi

TMP_PAYLOAD="$(mktemp)"
trap 'rm -f "$TMP_PAYLOAD"' EXIT

sed "s/__INDEX_PATTERN__/${INDEX_PATTERN}/g" "$DASHBOARD_FILE" > "$TMP_PAYLOAD"

echo "Deploying dashboard '${DASHBOARD_ID}' to ${BASE_URL} (index pattern: ${INDEX_PATTERN})..."

HTTP_CODE=$(curl -s -o /tmp/pinkas-ops-deploy-response.json -w "%{http_code}" \
  -X PUT "${BASE_URL}/api/dashboards/${DASHBOARD_ID}" \
  -H "Content-Type: application/json" \
  -H "kbn-xsrf: true" \
  -H "Elastic-Api-Version: ${API_VERSION}" \
  "${AUTH_HEADER[@]}" \
  --data-binary @"$TMP_PAYLOAD")

if [[ "$HTTP_CODE" != "200" && "$HTTP_CODE" != "201" ]]; then
  echo "Error: deploy failed with HTTP ${HTTP_CODE}" >&2
  cat /tmp/pinkas-ops-deploy-response.json >&2
  exit 1
fi

echo "Deployed. Verifying round-trip and portability..."

GET_HTTP_CODE=$(curl -s -o /tmp/pinkas-ops-getback-response.json -w "%{http_code}" \
  -X GET "${BASE_URL}/api/dashboards/${DASHBOARD_ID}" \
  -H "Elastic-Api-Version: ${API_VERSION}" \
  "${AUTH_HEADER[@]}")

if [[ "$GET_HTTP_CODE" != "200" ]]; then
  echo "Error: round-trip GET failed with HTTP ${GET_HTTP_CODE}" >&2
  cat /tmp/pinkas-ops-getback-response.json >&2
  exit 1
fi

if grep -q '"ref_id"\|"references"\|data_view_reference\|data_view_spec' /tmp/pinkas-ops-getback-response.json; then
  echo "Error: deployed dashboard contains external saved-object references. Portability check failed." >&2
  exit 1
fi

echo "OK: dashboard '${DASHBOARD_ID}' deployed and verified portable (no ref_id/references/data views)."
