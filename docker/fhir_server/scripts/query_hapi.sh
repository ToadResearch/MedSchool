#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# query_hapi.sh
#
# Prints a table of resource counts from a HAPI FHIR server.
#
# • Uses $FHIR_BEARER_TOKEN for JWT auth.
# • If that variable isn’t already exported, the script will source the
#   project-root .env file so you don’t have to `source .env` manually.
# • Exits with a clear error if the token is missing or invalid (401).
# ---------------------------------------------------------------------------
set -euo pipefail

# ───── Locate repo root & .env ──────────────────────────────────────────────
# Find the repository root by looking for pyproject.toml or .git
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ENV_FILE="$REPO_ROOT/.env"

# If token missing, try loading .env to pick up FHIR_BEARER_TOKEN
if [[ -z "${FHIR_BEARER_TOKEN:-}" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

# ─── Check for Token ────────────────────────────────────────────────────────
# Exit early if the token is not set, as all queries will fail.
if [[ -z "${FHIR_BEARER_TOKEN:-}" ]]; then
  echo "Error: FHIR_BEARER_TOKEN is not set in the environment or .env file." >&2
  echo "Please run './docker/nginx/scripts/generate_jwt.sh' and update your .env file." >&2
  exit 1
fi

# ─── Configuration & Dependencies ─────────────────────────────────────────
BASE_URL="${1:-http://localhost:8080/fhir}"
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

  # Make the authenticated request and capture the response body and HTTP code
  response=$(curl -sS -m30 \
    -H "Accept: $ACCEPT" \
    -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
    -w "\n%{http_code}" \
    "$resource_url")

  http_code="${response##*$'\n'}"
  body="${response%$'\n'"$http_code"}"

  if [[ "$http_code" == "200" ]]; then
    # Success: parse the total from the JSON body
    total=$(echo "$body" | jq -r '.total // 0')
    printf "%-30s %12d\n" "$t" "$total"
  elif [[ "$http_code" == "401" ]]; then
    # Fatal Auth Error: The token is bad. Exit with a helpful message.
    echo "------------------------------------------------------------------" >&2
    echo "Error: Received HTTP 401 Unauthorized for resource type '$t'." >&2
    echo "Your FHIR_BEARER_TOKEN is likely invalid or has expired." >&2
    echo "" >&2
    echo "SOLUTION: Run './docker/nginx/scripts/generate_jwt.sh' and" >&2
    echo "          copy the new token into your .env file." >&2
    echo "------------------------------------------------------------------" >&2
    exit 1
  else
    # Other error: Report it and continue to the next resource.
    printf "%-30s %12s\n" "$t" "Error ($http_code)"
  fi
done