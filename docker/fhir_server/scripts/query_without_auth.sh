#!/usr/bin/env bash
set -euo pipefail

# ─── Find & source .env anywhere above ────────────────────────────
CUR="$PWD"
ENV_FILE=""
while [[ "$CUR" != "/" ]]; do
  if [[ -f "$CUR/.env" ]]; then
    ENV_FILE="$CUR/.env"
    break
  fi
  CUR=$(dirname "$CUR")
done
if [[ -n "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
else
  echo "Warning: .env not found—relying on existing env vars" >&2
fi

# ─── Configure base URL ───────────────────────────────────────────
if [[ -n "${FHIR_BASE_URL:-}" ]]; then
  BASE_URL="$FHIR_BASE_URL"
else
  HOST="localhost"
  PORT="${HAPI_PORT:-8080}"
  BASE_URL="http://$HOST:$PORT/fhir"
fi

# ─── Dependencies ─────────────────────────────────────────────────
command -v curl >/dev/null 2>&1 || { echo "curl required" >&2; exit 1; }
command -v jq   >/dev/null 2>&1 || { echo "jq required"   >&2; exit 1; }

# ─── Fetch metadata (which should be unauthenticated) ───────────
meta_url="$BASE_URL/metadata"
echo "Fetching public metadata from $meta_url" >&2
meta_response=$(curl -sS -m30 \
    -H "Accept: application/fhir+json" \
    -w "\n%{http_code}" \
    "$meta_url")

meta_http_code="${meta_response##*$'\n'}"
meta_body="${meta_response%$'\n'"$meta_http_code"}"

if [[ "$meta_http_code" != "200" ]]; then
  echo "Error: Failed to fetch public metadata (HTTP $meta_http_code)." >&2
  echo "$meta_body" >&2
  exit 1
fi

echo "✅ Public metadata accessible. Verifying other endpoints are protected..."
echo ""

# ─── Extract types & print counts ─────────────────────────────────
types="$(jq -r '.rest[].resource[].type' <<<"$meta_body" | sort -u)"
printf "%-30s %10s\n" "ResourceType" "Status"
printf "%-30s %10s\n" "------------" "------"

for t in $types; do
  resource_url="$BASE_URL/$t?_summary=count&_total=accurate"

  # Make the unauthenticated request
  response=$(curl -sS -m30 \
    -H "Accept: application/fhir+json" \
    -w "\n%{http_code}" \
    "$resource_url")

  http_code="${response##*$'\n'}"

  if [[ "$http_code" == "200" ]]; then
    printf "%-30s %20s\n" "$t" "UNPROTECTED (200)"
  else
    printf "%-30s %20s\n" "$t" "Access Denied ($http_code)"
  fi
done