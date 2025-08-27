#!/usr/bin/env bash
# MedSchool/startup.sh
# ------------------------------------------------------------
# Usage Modes:
#
#   Default:
#       ./startup.sh
#       → Starts Postgres + HAPI FHIR server and waits for /fhir/metadata.
#
#   With Synthea data:
#       ./startup.sh --synthea
#       → Starts the server and runs the uploader job to download
#         and load Synthea sample data into the server.
#
#   Clean slate:
#       ./startup.sh --reset
#       → Tears down all containers and removes volumes before starting.
#         Recommended if you encounter errors like 'port is already allocated'.
# ------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
# REPO_ROOT is now the current directory where startup.sh is executed.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"

WITH_SYNTHEA=0
REBUILD=0
RESET=0

# --- Services (edit here) ---
# Base services that are always started with `up -d`.
BASE_SERVICES=(gateway db hapi validator mcp sandbox)

# All services to show in the service summary (can include one-shots like `uploader`).
ALL_SERVICES=("${BASE_SERVICES[@]}" uploader)

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --synthea   Also run the one-shot uploader to download and load Synthea data.
  --rebuild        Rebuild the uploader image before running it (implies --synthea).
  --reset          Tear down the stack completely (removes DB data) before starting.
  -h, --help       Show this help.

Examples:
  $(basename "$0")                        # Start Postgres + HAPI + Gateway
  $(basename "$0") --synthea         # Start services then load Synthea data
  $(basename "$0") --reset --synthea # Recommended for a clean start with data
EOF
}

# ---------- Parse args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --synthea) WITH_SYNTHEA=1 ;;
    --rebuild) REBUILD=1 ;;
    --reset) RESET=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ $REBUILD -eq 1 ]]; then
  WITH_SYNTHEA=1
fi

# Ensure .env is present in the root directory
if [[ ! -f "$REPO_ROOT/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
fi

# Source the .env file to load environment variables
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env"
  set +a
fi

# Use the docker-compose.yaml directly from the root
if [[ $RESET -eq 1 ]]; then
  echo "--reset flag detected. Tearing down the full stack first..."
  # The '-v' flag removes the named volumes, clearing the database.
  docker compose -f "$COMPOSE_FILE" --env-file .env down -v || true
fi

# echo "Pulling required Docker images..."
# docker compose -f "$COMPOSE_FILE" --env-file .env pull

# # Build sandbox so its image tag exists locally (avoids registry pull errors)
# echo "Building local image (sandbox)..."
# docker compose -f "$COMPOSE_FILE" --env-file .env build --pull sandbox


echo "Building all local images (pulling base layers as needed)…"
docker compose -f "$COMPOSE_FILE" --env-file .env build --pull

# Start base services from the centralized list
echo "Starting services (${BASE_SERVICES[*]})..."
docker compose -f "$COMPOSE_FILE" --env-file .env up -d "${BASE_SERVICES[@]}"


echo "Kicking off validator pre-warm (runs once in background)..."
# one-shot job; talks to the validator container directly on 3500 inside the compose network
docker compose -f "$COMPOSE_FILE" --env-file .env up -d validator-prewarm || true

# Use HAPI_PORT from environment/.env file, with a fallback to 8080
HAPI_PORT="${HAPI_PORT:-8080}"

print_service_info() {
  echo ""
  echo "Service info summary:"
  for svc in "${ALL_SERVICES[@]}"; do
    cid=$(docker compose -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)
    if [[ -n "$cid" ]]; then
      echo ""
      echo "Service: $svc"
      docker stats --no-stream --format '  Usage → Mem: {{.MemUsage}} | CPU: {{.CPUPerc}}' "$cid"
      echo -n "  Ports: "
      docker port "$cid" | sed 's/^/ /' || echo "Not published"
    fi
  done
  echo ""
}

print_service_info

if [[ $WITH_SYNTHEA -eq 1 ]]; then
  echo "Running uploader to load Synthea data..."
  # The 'up' command will start the service if it's not running
  docker compose -f "$COMPOSE_FILE" --env-file .env up ${REBUILD:+--build} uploader
else
  echo "Skipping Synthea data load. Use the --synthea flag to load data."
fi

echo "Counting resources (requires a valid token in .env)..."
"$REPO_ROOT/docker/fhir_server/scripts/wait_for_fhir.sh" # TODO: the query_hapi.sh fails on --synthea flag if this isn't run first... figure out why
"$REPO_ROOT/docker/fhir_server/scripts/query_hapi.sh" "http://localhost:${HAPI_PORT}/fhir" || true

echo -e "\nDone."
if [[ $WITH_SYNTHEA -eq 0 ]]; then
  echo "   To load Synthea data later, run: ./startup.sh --synthea"
fi
