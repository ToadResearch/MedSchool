#!/usr/bin/env bash
# MedSchool/shutdown.sh
# ----------------------------------------------------------------------
# Usage Modes:
#
#   Default (Stop):
#       ./shutdown.sh
#       → Stops the Docker containers.
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

# Figure out compose project & network name.
# NOTE: Middleman creates extra containers (mm-alpine-<uuid>) that attach to the
# compose network. We must remove those before 'docker compose down' deletes the
# network or we’ll hit "network ... has active endpoints".
PROJECT_DIR_NAME="$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]')"
PROJECT_FROM_ENV=""
if [[ -f "$REPO_ROOT/.env" ]]; then
  PROJECT_FROM_ENV=$(grep -E '^[[:space:]]*COMPOSE_PROJECT_NAME=' "$REPO_ROOT/.env" \
    | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]') || true
fi
PROJECT_NAME="${PROJECT_FROM_ENV:-$PROJECT_DIR_NAME}"
NETWORK_NAME="${PROJECT_NAME}_medschool-net"

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

# --- Targeted cleanups (safe with set -u, no mapfile) ---

# Remove Middleman session containers and stale sandbox container(s) only.
cleanup_sessions_only() {
  echo "Cleaning Middleman session containers…"

  # Middleman session containers (labeled by Middleman).
  SESSION_IDS="$(docker ps -aq --filter "label=mm.group=alpine-sessions" || true)"
  if [[ -n "$SESSION_IDS" ]]; then
    COUNT="$(echo "$SESSION_IDS" | wc -w | tr -d ' ')"
    echo "  Removing ${COUNT} middleman session container(s)…"
    echo "$SESSION_IDS" | xargs docker rm -f >/dev/null 2>&1 || true
  fi

  # Stale sandbox container(s) from older runs (even if service is commented out).
  docker rm -f medschool-sandbox >/dev/null 2>&1 || true
}

# Remove containers attached to the project network that are *not*
# managed by this compose project (i.e., no com.docker.compose.project=$PROJECT_NAME label).
# This prevents "active endpoints" without killing compose-managed services.
cleanup_network_orphans() {
  # Skip if network doesn't exist (e.g., after a full down)
  if ! docker network inspect "${NETWORK_NAME:-}" >/dev/null 2>&1; then
    return 0
  fi

  echo "Cleaning non-compose containers still attached to ${NETWORK_NAME:-}…"

  CIDS_ON_NET="$(docker ps -aq --filter "network=${NETWORK_NAME:-}" || true)"
  REMOVED=0

  if [[ -n "$CIDS_ON_NET" ]]; then
    for cid in $CIDS_ON_NET; do
      # Is this container owned by THIS compose project?
      PROJ_LABEL="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$cid" 2>/dev/null || true)"
      if [[ "${PROJ_LABEL:-}" != "$PROJECT_NAME" ]]; then
        # Not ours → safe to remove here.
        docker rm -f "$cid" >/dev/null 2>&1 || true
        REMOVED=$((REMOVED+1))
      fi
    done
  fi

  if [[ $REMOVED -gt 0 ]]; then
    echo "  Removed ${REMOVED} non-compose container(s) from ${NETWORK_NAME:-}."
  fi
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
  echo "    - All containers (hapi, postgres, uploader, mcp, etc)"
  echo "    - The database volume 'pgdata' (ALL SYNTHETIC PATIENT DATA WILL BE LOST)"
  echo "    - Docker images used by the compose file"
  echo ""
  read -p "Are you absolutely sure you want to proceed? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi

  VOLUME_NAME="${PROJECT_NAME}_pgdata"
  if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    USE_FLAG_V=1
  else
    USE_FLAG_V=0
  fi

  # Important: clear Middleman sessions first, then any non-compose orphans on the network.
  cleanup_sessions_only
  cleanup_network_orphans

  # Run compose down, including -v only when the volume actually exists.
  if [[ "$USE_FLAG_V" -eq 1 ]]; then
    echo "Purging stack (docker compose down -v --rmi all)..."
    docker compose -f "$COMPOSE_FILE" --env-file .env down -v --rmi all --remove-orphans
  else
    echo "Purging stack (docker compose down --rmi all)..."
    docker compose -f "$COMPOSE_FILE" --env-file .env down --rmi all --remove-orphans
  fi

  # Try to remove the network explicitly (harmless if already gone).
  docker network rm "${NETWORK_NAME:-}" >/dev/null 2>&1 || true

  # Fallback: explicitly remove sandbox image(s) if they exist and weren't matched by compose.
  docker rmi -f medschool-python-sandbox 2>/dev/null || true
  # Optionally also prune these if you no longer want them around:
  # docker rmi -f medschool-sandbox medschool-alpine-sandbox 2>/dev/null || true

  echo "Purge complete."

elif [[ $DOWN -eq 1 ]]; then
  # Important: clear Middleman sessions first so the network delete won't fail.
  cleanup_sessions_only
  # Also remove any non-compose containers left on the project network (but keep compose services).
  cleanup_network_orphans

  echo " gracefully stopping and removing containers (including sandbox)…"
  docker compose -f "$COMPOSE_FILE" --env-file .env down --remove-orphans
  # Try to remove the network explicitly (ignore if compose already did).
  docker network rm "${NETWORK_NAME:-}" >/dev/null 2>&1 || true

  echo "Containers removed. Your data volume ('pgdata') is preserved."
  echo "   Run './startup.sh' to start again."
  echo "   To delete all data, run this script again with the '--purge' flag."

else
  # Default mode: stop containers only.
  # We still clear Middleman sessions so the network is clean, but we DO NOT
  # delete compose-managed containers here (preserves your non-destructive stop).
  cleanup_sessions_only

  echo "Stopping MedSchool services..."
  docker compose -f "$COMPOSE_FILE" --env-file .env stop

  echo "Services stopped. Containers and data are preserved."
  echo "   Run 'docker compose start' or './startup.sh' to resume."
  echo "   To stop and remove containers instead, run './shutdown.sh --down'."
  echo "   To delete everything (containers, data, images), run './shutdown.sh --purge'."
fi