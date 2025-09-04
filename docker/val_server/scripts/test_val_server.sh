#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# test_val_server.sh
#
# Minimal smoke test for the Validator Wrapper.
# TODO: remove this and fix it to use generic endpoints with new middleman
# Uniform gateway surface: 
#   All validator endpoints are exposed as: http://<gateway>/validator/<endpoint>
#
# Defaults:
#   BASE URL: http://localhost:8080/validator
#   FHIR SV : 4.0.1 (R4)
#
# What this does:
#   - GET  /validator/version     → check wrapper version
#   - GET  /validator/versions    → list supported FHIR versions
#   - POST /validator/validate    → validate a good Patient (R4)
#   - POST /validator/validate    → validate a bad  Patient (R4)
#
# Auth:
#   No authentication required for validator endpoints.
#
# Requirements: bash, curl, jq (uuidgen optional; we fall back if missing)
#
# Usage:
#   ./docker/val_server/scripts/test_val_server.sh
#   ./docker/val_server/scripts/test_val_server.sh http://localhost:8080/validator
#
#
# Robustness note:
#   We *do not* use an AUTH_HEADER array under `set -u`. Instead, curl_auth()
#   appends Authorization only when the token exists—avoids unbound var errors.
# ------------------------------------------------------------------------------

set -euo pipefail

# Optional debug: VERBOSE=1 ./script.sh
if [[ "${VERBOSE:-}" == "1" ]]; then
  set -x
fi

# --- resolve script dir (for relative .env lookup) --------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# TODO: remove this and fix it to use generic endpoints with new middleman
# --- base URL (default: gateway path) ---------------------------------------------
# BASE_URL_DEFAULT="http://localhost:8080/validator"
# BASE_URL="${1:-$BASE_URL_DEFAULT}"
# BASE_URL="${BASE_URL%/}"  # trim trailing slash if present

# --- load .env (current dir and repo root candidates) -----------------------------
load_env_if_present() {
  local candidates=(
    ".env"
    "$SCRIPT_DIR/../../.env"
    "$SCRIPT_DIR/../../../.env"
  )
  local f
  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      # shellcheck disable=SC1090
      set -a
      . "$f"
      set +a
      break
    fi
  done
}
load_env_if_present

# --- helpers ----------------------------------------------------------------------
need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "❌ Missing required tool: $1" >&2
    exit 1
  }
}

uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  elif command -v shasum >/dev/null 2>&1; then
    date +%s%N | shasum | awk '{print substr($1,1,32)}'
  else
    date +%s%N
  fi
}

# curl wrapper (no auth needed)
curl_auth() {
  command curl "$@"
}

# Return HTTP code; never fail the script (avoids silent exits with `set -e`)
http_code() {
  local code
  code="$(curl_auth -s -o /dev/null -w "%{http_code}" "$1" || true)"
  if [[ -z "$code" ]]; then code="000"; fi
  echo "$code"
  return 0
}

json_get() {
  local url="$1"
  curl_auth -sS -f "$url"
}

json_post() {
  local url="$1"
  local body="$2"
  curl_auth -sS -f -H 'Content-Type: application/json' -d "$body" "$url"
}

section() {
  echo
  echo "==> $*"
}

# --- sanity checks ----------------------------------------------------------------
need curl
need jq

echo "🏁 Using ${BASE_URL}"

# Preflight: /validator/version
VERSION_URL="${BASE_URL}/version"
CODE="$(http_code "$VERSION_URL")"

echo "Preflight: GET $VERSION_URL → HTTP $CODE"

if [[ "$CODE" == "000" ]]; then
  cat >&2 <<EOF
❌ Could not reach $VERSION_URL (no response).
EOF
  exit 1
elif [[ "$CODE" == "401" ]]; then
  cat >&2 <<EOF
❌ 401 Unauthorized at: $VERSION_URL
EOF
  exit 1
elif [[ "$CODE" == "404" ]]; then
  cat >&2 <<EOF
❌ 404 Not Found at: $VERSION_URL

Check:
  • Is the validator container healthy? (docker compose ps)
EOF
  exit 1
elif [[ "$CODE" != "200" ]]; then
  echo "❌ Unexpected status $CODE from $VERSION_URL" >&2
  exit 1
fi

# --- Calls ------------------------------------------------------------------------

section "GET /validator/version"
json_get "${VERSION_URL}" | jq .

VERSIONS_URL="${BASE_URL}/versions"

section "GET /validator/versions"
json_get "${VERSIONS_URL}" | jq .

# R4 validation requests (sv=4.0.1)
SESSION_OK="$(uuid)"
SESSION_BAD="$(uuid)"
VALIDATE_URL="${BASE_URL}/validate"

section "POST /validator/validate (R4 Patient OK)"
REQ_OK="$(jq -cn --arg sid "$SESSION_OK" '
{
  cliContext: { sv: "4.0.1", locale: "en" },
  filesToValidate: [
    {
      fileName: "patient-ok.json",
      fileType: "json",
      fileContent: "{\"resourceType\":\"Patient\",\"name\":[{\"family\":\"Doe\",\"given\":[\"Jane\"]}],\"gender\":\"female\",\"birthDate\":\"1990-04-03\"}"
    }
  ],
  sessionId: $sid
}')"

RESP_OK="$(json_post "$VALIDATE_URL" "$REQ_OK")"
echo "$RESP_OK" | jq .
ISSUES_OK_COUNT="$(echo "$RESP_OK" | jq '[.outcomes[]?.issues[]?] | length // 0')"
echo "Issues: ${ISSUES_OK_COUNT}"
echo "By level/severity: $(echo "$RESP_OK" | jq '[.outcomes[]?.issues[]?] | group_by(.level // .severity // "UNKNOWN") | map({(.[0].level // .[0].severity // "UNKNOWN"): length}) | add // {}')"

section "POST /validator/validate (R4 Patient BAD: invalid birthDate)"
REQ_BAD="$(jq -cn --arg sid "$SESSION_BAD" '
{
  cliContext: { sv: "4.0.1", locale: "en" },
  filesToValidate: [
    {
      fileName: "patient-bad.json",
      fileType: "json",
      fileContent: "{\"resourceType\":\"Patient\",\"birthDate\":\"not-a-date\"}"
    }
  ],
  sessionId: $sid
}')"

RESP_BAD="$(json_post "$VALIDATE_URL" "$REQ_BAD")"
echo "$RESP_BAD" | jq .
ISSUES_BAD_COUNT="$(echo "$RESP_BAD" | jq '[.outcomes[]?.issues[]?] | length // 0')"
echo "Issues: ${ISSUES_BAD_COUNT}"
echo "By level/severity: $(echo "$RESP_BAD" | jq '[.outcomes[]?.issues[]?] | group_by(.level // .severity // "UNKNOWN") | map({(.[0].level // .[0].severity // "UNKNOWN"): length}) | add // {}')"

echo
echo "✅ Done."
