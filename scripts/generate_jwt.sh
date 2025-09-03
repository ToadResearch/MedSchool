#!/usr/bin/env bash
# MedSchool/scripts/generate_jwt.sh
# ----------------------------------------------------------------------
# Generate a JWT token for authenticating with the FHIR server.
#
# Usage:
#   ./scripts/generate_jwt.sh [--expires-in HOURS] [--no-expiry]
#
# Options:
#   --expires-in HOURS  Set token expiration time in hours (default: 24)
#   --no-expiry         Create a token that never expires (for demos)
#   -h, --help          Show this help message
#
# The script reads JWT_SHARED_SECRET from the .env file in the project root.
# ----------------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DEFAULT_EXPIRY_HOURS=24
EXPIRY_HOURS=""
NO_EXPIRY=false

# --- Argument Parsing ---
usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Generate a JWT token for FHIR server authentication.

Options:
  --expires-in HOURS  Token expires in HOURS hours (default: 24)
  --no-expiry         Create a token that never expires
  -h, --help          Show this help

Examples:
  $(basename "$0")                    # 24-hour token
  $(basename "$0") --expires-in 168   # 1-week token
  $(basename "$0") --no-expiry        # No expiration
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expires-in)
      EXPIRY_HOURS="$2"
      shift 2
      ;;
    --no-expiry)
      NO_EXPIRY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

# --- Load Environment ---
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env file not found at $ENV_FILE" >&2
  echo "Please copy .env.example to .env and configure it." >&2
  exit 1
fi

# Source the .env file
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${JWT_SHARED_SECRET:-}" ]]; then
  echo "Error: JWT_SHARED_SECRET not set in .env file" >&2
  exit 1
fi

# --- Generate JWT ---
if [[ "$NO_EXPIRY" == true ]]; then
  # No expiration token
  python3 -c "
import sys
try:
    import jwt
except ImportError:
    print('Error: PyJWT library not installed. Install with: pip install PyJWT', file=sys.stderr)
    sys.exit(1)

import datetime

secret = '$JWT_SHARED_SECRET'
payload = {
    'iss': 'medschool-jwt-generator',
    'sub': 'medschool-user',
    'iat': int(datetime.datetime.utcnow().timestamp())
}
token = jwt.encode(payload, secret, algorithm='HS256')
print(token)
"
else
  # Token with expiration
  EXPIRY_HOURS="${EXPIRY_HOURS:-$DEFAULT_EXPIRY_HOURS}"
  python3 -c "
import sys
try:
    import jwt
except ImportError:
    print('Error: PyJWT library not installed. Install with: pip install PyJWT', file=sys.stderr)
    sys.exit(1)

import datetime

secret = '$JWT_SHARED_SECRET'
hours = int('$EXPIRY_HOURS')
exp_time = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)

payload = {
    'iss': 'medschool-jwt-generator',
    'sub': 'medschool-user',
    'iat': int(datetime.datetime.utcnow().timestamp()),
    'exp': int(exp_time.timestamp())
}
token = jwt.encode(payload, secret, algorithm='HS256')
print(token)
"
fi
