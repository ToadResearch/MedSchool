#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# query_hapi.sh
#
# Prints a table of resource counts from a HAPI FHIR server.
#
# ---------------------------------------------------------------------------
set -euo pipefail

# ───── Locate repo root ──────────────────────────────────────────────
# Find the repository root by looking for pyproject.toml or .git
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# ───── Load environment (so $FHIR_BASE_URL is available) ─────────────
# Load .env if present; make variables exported while sourcing.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env"
  set +a
fi

# ─── Configuration & Dependencies ─────────────────────────────────────────
# Arg 1 overrides .env/env var. If unset, try $FHIR_BASE_URL; if still empty, fail clearly.
BASE_URL="${1:-${FHIR_BASE_URL:-}}"
: "${BASE_URL:?Provide BASE_URL as arg or set FHIR_BASE_URL in .env/env}"

ACCEPT="application/fhir+json"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need curl
need jq

# ───── Fetch and validate public /metadata endpoint ─────────────────────
# This endpoint is public, so no auth is needed, but it's a good healthcheck.
if ! curl -sS -m 30 -H "Accept: $ACCEPT" "$BASE_URL/metadata" | jq -e -r '.resourceType=="CapabilityStatement"' >/dev/null; then
    echo "Error: Could not fetch a valid CapabilityStatement from $BASE_URL/metadata" >&2
    echo "Is the server running and accessible?" >&2
    exit 1
fi

echo "Using FHIR base: $BASE_URL" >&2

# ───── Fetch resource types & counts ───────────────────────────────────────
types=$(curl -sS -m 30 -H "Accept: $ACCEPT" "$BASE_URL/metadata" | jq -r '.rest[]?.resource[]?.type' | sort -u)
if [[ -z "$types" ]]; then
  echo "No resource types found in CapabilityStatement." >&2
  exit 1
fi

printf "%-30s %12s\n" "ResourceType" "Count"
printf "%-30s %12s\n" "------------" "-----"

for t in $types; do
  resource_url="$BASE_URL/$t?_summary=count&_total=accurate"

  # Make the request and capture the response body and HTTP code
  response=$(curl -sS -m30 \
    -H "Accept: $ACCEPT" \
    -w "\n%{http_code}" \
    "$resource_url")

  http_code="${response##*$'\n'}"
  body="${response%$'\n'"$http_code"}"

  if [[ "$http_code" == "200" ]]; then
    # Success: parse the total from the JSON body
    total=$(echo "$body" | jq -r '.total // 0')
    printf "%-30s %12d\n" "$t" "$total"
  else
    # Other error: Report it and continue to the next resource.
    printf "%-30s %12s\n" "$t" "Error ($http_code)"
  fi
done