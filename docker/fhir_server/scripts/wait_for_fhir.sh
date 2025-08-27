#!/usr/bin/env bash
set -euo pipefail

# Use HAPI_PORT from environment/.env file, with a fallback to 8080
HAPI_PORT="${HAPI_PORT:-8080}"

BASE="${1:-http://localhost:${HAPI_PORT}/fhir}"

echo -n "Waiting for HAPI FHIR server at $BASE/metadata..."
until curl -fsS "$BASE/metadata" >/dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " ready."
