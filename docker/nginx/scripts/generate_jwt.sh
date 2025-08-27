#!/usr/bin/env bash
# Generate a time-limited HS256 JWT signed with $JWT_SHARED_SECRET.
# Also restarts the gateway to ensure it uses the latest secret.
#
# Dependencies: openssl, coreutils, docker.

set -euo pipefail

# --- Configuration & Argument Parsing ---
HOURS=24
INDEFINITE=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Generates a JWT for accessing the FHIR server.

Options:
  --expires-in <hours>  Set the token's lifespan in hours. Defaults to 24.
  --no-expiry           Create a token that does not expire. (For demos only)
  -h, --help            Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expires-in)
      if [[ -z "${2:-}" || ! "$2" =~ ^[0-9]+$ ]]; then
        echo "Error: --expires-in requires a positive integer." >&2; usage; exit 1
      fi
      HOURS="$2"
      shift # past argument
      ;;
    --no-expiry) INDEFINITE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift # past argument or value
done


# --- Locate project root and fhir_server directory ---
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ENV_FILE="$REPO_ROOT/.env"

# --- Load JWT_SHARED_SECRET from .env if not already set ---
if [[ -z "${JWT_SHARED_SECRET:-}" && -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

secret="${JWT_SHARED_SECRET:?Error: JWT_SHARED_SECRET is not set. Please define it in the .env file.}"

# 1) JWT header  ────────────────────────────────────────────
header='{"alg":"HS256","typ":"JWT"}'

# 2) JWT payload (conditionally includes 'exp') ────────────
now=$(date +%s)
# keep the JSON object **open** so we can safely append "exp"
base_payload=$(printf '{"sub":"medschool-cli","scope":"fhir/*.*","iat":%s' "$now")

if [[ $INDEFINITE -eq 1 ]]; then
  payload="${base_payload}}"
  echo "🔹 Generating a non-expiring JWT."
else
  exp=$((now + HOURS * 3600))
  payload="${base_payload},\"exp\":${exp}}"
  echo "🔹 Generating a JWT that will expire in $HOURS hour(s)."
fi


# 3) Helper: base64url-encode (RFC 7515 §2)
b64url() {
  openssl base64 -e -A | tr '+/' '-_' | tr -d '='
}

# 4) Encode header + payload
header_b64=$(printf '%s' "$header"   | b64url)
payload_b64=$(printf '%s' "$payload" | b64url)

# 5) Sign "<header>.<payload>" with HMAC-SHA256
sig=$(printf '%s.%s' "$header_b64" "$payload_b64" |
      openssl dgst -binary -sha256 -hmac "$secret" |
      b64url)

# 6) Final token
token="${header_b64}.${payload_b64}.${sig}"
echo "Generated JWT:"
echo "$token"
echo ""
echo "Important: Please copy the token above into the FHIR_BEARER_TOKEN variable in your .env file."
echo ""


# --- Recreate gateway to ensure it has the latest secret from .env ---
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"
GATEWAY_CONTAINER_NAME="medschool-gateway"
if command -v docker >/dev/null 2>&1 && [[ -f "$COMPOSE_FILE" ]]; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$GATEWAY_CONTAINER_NAME" 2>/dev/null || echo "false")" == "true" ]]; then
    echo "Recreating the Nginx gateway container to apply the latest JWT secret..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --force-recreate gateway | cat
    echo "Gateway recreated."
  else
    echo "Docker services are not running. Skipping gateway restart."
  fi
else
  echo "Warning: Could not automatically restart the gateway."
fi
