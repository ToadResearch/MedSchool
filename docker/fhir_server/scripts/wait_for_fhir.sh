#!/usr/bin/env bash
set -euo pipefail

# Use MIDDLEMAN_PORT from environment/.env file, with a fallback to 3000
MIDDLEMAN_PORT="${MIDDLEMAN_PORT:-3000}"

BASE="${1:-http://localhost:${MIDDLEMAN_PORT}/fhir}"

echo -n "Waiting for HAPI FHIR server at $BASE/metadata..."
until curl -fsS "$BASE/metadata" >/dev/null 2>&1; do
  echo -n "."
  sleep 2
done
echo " ready."
