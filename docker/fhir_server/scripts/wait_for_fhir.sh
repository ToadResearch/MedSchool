#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./wait_for_fhir.sh [BASE_FHIR_URL]
# We resolve the base URL with this precedence:
#   1) Command line argument BASE_FHIR_URL (highest precedence)
#   2) FHIR_BASE_URL
#   3) FHIR_PROXY_INTERNAL_BASE + "/fhir"
#   4) HAPI_INTERNAL_FHIR_BASE
#   5) HAPI_INTERNAL_BASE + "/fhir"
#   6) http://hapi:${HAPI_CONTAINER_PORT:-8080}/fhir
#
# Bonus: if FHIR_PROXY_INTERNAL_BASE isn't set but we have
# MIDDLEMAN_INTERNAL_BASE and FHIR_ROUTE, we synthesize it:
#   FHIR_PROXY_INTERNAL_BASE="${MIDDLEMAN_INTERNAL_BASE}/${FHIR_ROUTE}"

# ── Find repo root (git if available; else current dir) and load .env ──
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env"
  set +a
fi

# ── Dependencies ──
if ! command -v curl >/dev/null 2>&1; then
  echo "Missing dependency: curl" >&2
  exit 1
fi


BASE_ARG="${1:-}"

# If we can synthesize the internal proxy base, do it up front.
if [[ -z "${FHIR_PROXY_INTERNAL_BASE:-}" ]] \
   && [[ -n "${MIDDLEMAN_INTERNAL_BASE:-}" ]] \
   && [[ -n "${FHIR_ROUTE:-}" ]]; then
  export FHIR_PROXY_INTERNAL_BASE="${MIDDLEMAN_INTERNAL_BASE%/}/${FHIR_ROUTE}"
fi

# Resolve BASE from envs, with command line arg having highest precedence
if [[ -n "${BASE_ARG}" ]]; then
  BASE="${BASE_ARG%/}"
elif [[ -n "${FHIR_BASE_URL:-}" ]]; then
  BASE="${FHIR_BASE_URL%/}"
elif [[ -n "${FHIR_PROXY_INTERNAL_BASE:-}" ]]; then
  BASE="${FHIR_PROXY_INTERNAL_BASE%/}/fhir"
elif [[ -n "${HAPI_INTERNAL_FHIR_BASE:-}" ]]; then
  BASE="${HAPI_INTERNAL_FHIR_BASE%/}"
elif [[ -n "${HAPI_INTERNAL_BASE:-}" ]]; then
  BASE="${HAPI_INTERNAL_BASE%/}/fhir"
else
  HAPI_HOST="${HAPI_HOST:-hapi}"
  HAPI_PORT="${HAPI_CONTAINER_PORT:-8080}"
  BASE="http://${HAPI_HOST}:${HAPI_PORT}/fhir"
fi

URL="${BASE}/metadata"

echo -n "Waiting for HAPI FHIR server at ${URL}..."
until curl -fsS "${URL}" >/dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " ready."
