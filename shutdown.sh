#!/usr/bin/env bash
# MedSchool/shutdown.sh
# ----------------------------------------------------------------------
# Usage Modes:
#
#   Default (Stop):
#       ./shutdown.sh
#       → Stops the Docker containers (gateway, db, hapi).
#       → Preserves container state and all data volumes.
#
#   Down (Stop and Remove Containers):
#       ./shutdown.sh --down
#       → Stops and removes the Docker containers.
#       → Keeps the database volume ('pgdata') intact for the next run.
#
#   Purge (Nuke Everything):
#       ./shutdown.sh --purge
#       → Stops and removes containers.
#       → Deletes the Postgres data volume (ALL DATA WILL BE LOST).
#       → Removes the Docker images used by the services.
# ----------------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
# REPO_ROOT is now the current directory where shutdown.sh is executed.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"
PURGE=0
DOWN=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Manages the shutdown of the MedSchool Docker services.

Options:
  --down           Stops and removes the containers. Preserves data volumes.
  --purge          Destructive! Removes all associated containers,
                   volumes (deleting all data), and images.
  -h, --help       Show this help message.

Default action (no flags) is to simply stop the running containers.
EOF
}

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1 ;;
    --down) DOWN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

# --- Ensure docker-compose file exists ---
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Missing Docker Compose file at: $COMPOSE_FILE"
  echo "   Cannot proceed without the docker-compose.yaml file."
  exit 1
fi

# --- Use the docker-compose.yaml directly from the root ---
if [[ $PURGE -eq 1 ]]; then
  echo "--purge flag detected. This will stop the services and permanently delete:"
  echo "    - All containers (gateway, hapi, postgres, uploader, mcp)"
  echo "    - The database volume 'pgdata' (ALL SYNTHETIC PATIENT DATA WILL BE LOST)"
  echo "    - Docker images used by the compose file"
  echo ""
  read -p "Are you absolutely sure you want to proceed? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi

  # Determine project/volume name up front
  PROJECT_DIR_NAME="$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]')"
  PROJECT_FROM_ENV=""
  if [[ -f "$REPO_ROOT/.env" ]]; then
    PROJECT_FROM_ENV=$(grep -E '^[[:space:]]*COMPOSE_PROJECT_NAME=' "$REPO_ROOT/.env" | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]') || true
  fi
  PROJECT_NAME="${PROJECT_FROM_ENV:-$PROJECT_DIR_NAME}"
  VOLUME_NAME="${PROJECT_NAME}_pgdata"
  if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    USE_FLAG_V=1
  else
    USE_FLAG_V=0
  fi

  # Run compose down, including -v only when the volume actually exists
  if [[ "$USE_FLAG_V" -eq 1 ]]; then
    echo "Purging stack (docker compose --profile sandbox down -v --rmi all)..."
    docker compose -f "$COMPOSE_FILE" --env-file .env --profile sandbox down -v --rmi all
  else
    echo "Purging stack (docker compose --profile sandbox down --rmi all)..."
    docker compose -f "$COMPOSE_FILE" --env-file .env --profile sandbox down --rmi all
  fi

  # Fallback: explicitly remove the sandbox image if it exists and wasn't matched by compose
  docker rmi -f medschool-python-sandbox 2>/dev/null || true

  echo "Purge complete."

elif [[ $DOWN -eq 1 ]]; then
  echo " gracefully stopping and removing containers (including sandbox)..."
  docker compose -f "$COMPOSE_FILE" --env-file .env --profile sandbox down
  echo "Containers removed. Your data volume ('pgdata') is preserved."
  echo "   Run './startup.sh' to start again."
  echo "   To delete all data, run this script again with the '--purge' flag."

else
  echo "Stopping MedSchool services..."
  docker compose -f "$COMPOSE_FILE" --env-file .env stop
  echo "Services stopped. Containers and data are preserved."
  echo "   Run 'docker compose start' or './startup.sh' to resume."
  echo "   To stop and remove containers instead, run './shutdown.sh --down'."
  echo "   To delete everything (containers, data, images), run './shutdown.sh --purge'."
fi