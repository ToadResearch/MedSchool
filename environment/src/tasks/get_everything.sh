#!/usr/bin/env bash
set -euo pipefail

# Usage: ./get_everything.sh <ResourceType> <LogicalId> [--debug] [--tmpdir PATH]
# Example: ./get_everything.sh Patient 1206 --debug --tmpdir ./tmp_run
# Requires: curl, jq, perl
#
# ENV:
#   FHIR_BASE_URL  (required; loaded from .env if present)
#   DOTENV_PATH    (optional; explicit path to .env)

DEBUG=0
TMPDIR_OVERRIDE=""
# Parse trailing flags
for arg in "${@:3}"; do
  case "$arg" in
    --debug) DEBUG=1 ;;
    --tmpdir) echo "ERROR: --tmpdir requires a path argument" >&2; exit 1 ;;
  esac
done
# Support: --tmpdir PATH (order-insensitive, after the two positional args)
i=3
while [[ $i -le $# ]]; do
  a="${!i}"
  if [[ "$a" == "--tmpdir" ]]; then
    j=$((i+1))
    [[ $j -le $# ]] || { echo "ERROR: --tmpdir requires a path" >&2; exit 1; }
    TMPDIR_OVERRIDE="${!j}"; shift 2 || true
    continue
  fi
  i=$((i+1))
done

########################################
# Find and load .env (searches upward)
########################################
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_dotenv_up() {
  local dir="$1"
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/.env" ]]; then
      echo "$dir/.env"; return 0
    fi
    dir="$(dirname "$dir")"
  done
  return 1
}

if [[ -n "${DOTENV_PATH:-}" ]]; then
  if [[ -f "$DOTENV_PATH" ]]; then set -a; source "$DOTENV_PATH"; set +a
  else echo "WARN: DOTENV_PATH set but file not found: $DOTENV_PATH" >&2; fi
else
  if DOTENV_AUTO="$(find_dotenv_up "$SCRIPT_DIR")"; then
    echo "Loading env from: $DOTENV_AUTO" >&2
    set -a; source "$DOTENV_AUTO"; set +a
  else
    echo "INFO: No .env found in ancestor directories of $SCRIPT_DIR" >&2
  fi
fi

########################################
# Args & prereqs
########################################
if [[ -z "${FHIR_BASE_URL:-}" ]]; then
  echo "ERROR: FHIR_BASE_URL env var is not set." >&2; exit 1
fi
echo "FHIR_BASE_URL: $FHIR_BASE_URL"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <ResourceType> <LogicalId> [--debug] [--tmpdir PATH]" >&2; exit 1
fi

RESOURCE_TYPE="$1"
LOGICAL_ID="$2"
BASE_URL="${FHIR_BASE_URL%/}"
URL="${BASE_URL}/${RESOURCE_TYPE}/${LOGICAL_ID}/\$everything"
ORIGIN="$(printf '%s\n' "$BASE_URL" | sed -E 's#^(https?://[^/]+).*#\1#')"

OUTDIR="search_results"
OUTFILE="${OUTDIR}/${RESOURCE_TYPE}${LOGICAL_ID}.json"
mkdir -p "$OUTDIR"

########################################
# Local temp workspace (no system /var/folders)
########################################
# Default to ./tmp (or user-provided --tmpdir)
TMP_ROOT="${TMPDIR_OVERRIDE:-$(pwd)/tmp}"
mkdir -p "$TMP_ROOT"

mktemp_here() {
  # Portable mktemp that writes into TMP_ROOT
  local template="$TMP_ROOT/${1:-tmp}.XXXXXXXX"
  mktemp "$template"
}

TMP_FIRST="$(mktemp_here first)"
TMP_PAGE="$(mktemp_here page)"
RESULT="$(mktemp_here result)"
HDRS="$(mktemp_here hdrs)"

echo "TMP_FIRST=$TMP_FIRST"
echo "TMP_PAGE=$TMP_PAGE"
echo "RESULT=$RESULT"
echo "HDRS=$HDRS"

cleanup() {
  if [[ $DEBUG -eq 0 ]]; then
    rm -f "$TMP_FIRST" "$TMP_PAGE" "$RESULT" "$HDRS" "${RESULT}.new" 2>/dev/null || true
    # Keep the tmp directory itself; it’s inside the repo for visibility.
  else
    echo "DEBUG: keeping temp files in $TMP_ROOT" >&2
  fi
}
trap cleanup EXIT

########################################
# Helpers
########################################
sanitize_json_inplace() {
  local f="$1"
  # Replace bare NaN/Infinity tokens (not quoted) with null.
  perl -0777 -pe 's/(?<!")\b(?:NaN|Infinity|-Infinity)\b(?!")/null/g' -i "$f"
}

ensure_bundle_or_die() {
  local f="$1"
  if jq -e '.resourceType == "Bundle"' > /dev/null 2>&1 < "$f"; then return 0; fi
  sanitize_json_inplace "$f"
  if jq -e '.resourceType == "Bundle"' > /dev/null 2>&1 < "$f"; then return 0; fi
  echo "ERROR: Not a valid FHIR Bundle even after sanitize." >&2
  echo "---- Response headers ----" >&2; sed -n '1,60p' "$HDRS" >&2 || true
  echo "---- Body snippet (first 2000 bytes) ----" >&2; head -c 2000 "$f" >&2 || true
  if [[ $DEBUG -eq 1 ]]; then
    echo "---- grep NaN/Infinity ----" >&2
    grep -nE '\b(NaN|Infinity|-Infinity)\b' "$f" | head >&2 || true
  fi
  exit 1
}

next_from_bundle() {
  jq -r '.link[]? | select(.relation=="next") | .url' | head -n1
}

normalize_url() {
  local raw="$1"
  if [[ -z "$raw" ]]; then echo ""
  elif [[ "$raw" =~ ^https?:// ]]; then printf '%s\n' "$raw" | sed -E "s#^https?://[^/]+#${ORIGIN}#"
  elif [[ "$raw" =~ ^/ ]]; then printf '%s%s\n' "$ORIGIN" "$raw"
  else printf '%s/%s\n' "$ORIGIN" "$raw"
  fi
}

########################################
# Fetch first page
########################################
curl -sS -D "$HDRS" -H "Accept: application/fhir+json, application/json" "$URL" > "$TMP_FIRST"
sanitize_json_inplace "$TMP_FIRST"
ensure_bundle_or_die "$TMP_FIRST"
cp "$TMP_FIRST" "$RESULT"

########################################
# Paginate & merge
########################################
NEXT_URL_RAW="$(next_from_bundle < "$TMP_FIRST" || true)"
NEXT_URL="$(normalize_url "$NEXT_URL_RAW")"

while [[ -n "${NEXT_URL:-}" ]]; do
  curl -sS -D "$HDRS" -H "Accept: application/fhir+json, application/json" "$NEXT_URL" > "$TMP_PAGE"
  sanitize_json_inplace "$TMP_PAGE"

  jq -s '
    (.[0] // {}) as $acc
    | (.[1] // {}) as $page
    | $acc
    | .entry = ((.entry // []) + ($page.entry // []))
    | .link = ([$acc.link[]? | select(.relation=="self")])
  ' "$RESULT" "$TMP_PAGE" > "${RESULT}.new"
  mv "${RESULT}.new" "$RESULT"

  NEXT_URL_RAW="$(next_from_bundle < "$TMP_PAGE" || true)"
  NEXT_URL="$(normalize_url "$NEXT_URL_RAW")"
done

mv "$RESULT" "$OUTFILE"
echo "Saved: $OUTFILE"
