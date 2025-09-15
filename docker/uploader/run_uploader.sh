#!/usr/bin/env bash
set -euo pipefail

FHIR_BASE_URL="${FHIR_BASE_URL:?FHIR_BASE_URL not set}"
WORKDIR="/tmp/uploader"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "Waiting for HAPI at $FHIR_BASE_URL/metadata ..."
until curl -fsS "$FHIR_BASE_URL/metadata" >/dev/null 2>&1; do
  sleep 2
done
echo "HAPI is ready."

LOCAL_SYNTHEA_ROOT="/synthea"
FLAT_DIR="$WORKDIR/data_local"
DATA_DIR="$FLAT_DIR"

echo "=== Diagnostics: mounted volumes & free space ==="
df -h || true
echo "================================================="

echo "Checking that local Synthea volume is mounted at: $LOCAL_SYNTHEA_ROOT"
if [[ ! -d "$LOCAL_SYNTHEA_ROOT" ]]; then
  echo "ERROR: Directory $LOCAL_SYNTHEA_ROOT does not exist."
  echo "Make sure 'synthea_out' is mounted into the uploader as '/synthea' (compose does this) and that the 'synthea' job ran."
  exit 1
fi

echo
echo "=== Top-level listing of $LOCAL_SYNTHEA_ROOT ==="
ls -la "$LOCAL_SYNTHEA_ROOT" || true
echo "==============================================="

# Helper: recursively list a few levels to see structure (without huge spam)
echo
echo "=== Directory structure (2 levels) under $LOCAL_SYNTHEA_ROOT ==="
# Busybox 'find' is available in the base image; limit depth for readability if possible
# Fallback to a manual approach if -maxdepth is not supported (it is in GNU findutils, also in Debian base)
find "$LOCAL_SYNTHEA_ROOT" -maxdepth 2 -type d -print 2>/dev/null || true
echo "================================================================"

echo
echo "Searching for JSON bundles produced by Synthea (recursive) ..."
# Collect candidate JSONs from common Synthea locations (the search is already recursive)
# This will catch: /synthea/fhir/*.json, /synthea/out/fhir/*.json, or any nested *.json
mapfile -d '' FILES < <(find "$LOCAL_SYNTHEA_ROOT" -type f \( -iname '*.json' \) -print0 2>/dev/null || true)

TOTAL_JSON="${#FILES[@]}"

if [[ "$TOTAL_JSON" -eq 0 ]]; then
  echo "ERROR: No '*.json' files were found anywhere under $LOCAL_SYNTHEA_ROOT."
  echo "Likely causes:"
  echo "  • The 'synthea' job did not complete successfully."
  echo "  • Synthea wrote to a different directory than expected (check exporter.baseDirectory in synthea.properties)."
  echo "  • The shared volume 'synthea_out' is empty or not the same volume bound to the synthea container."
  echo
  echo "Quick checks you can run:"
  echo "  docker compose logs synthea --no-color | tail -n 200"
  echo "  docker compose exec synthea sh -lc 'ls -la /out; find /out -maxdepth 2 -type f -name \"*.json\" | head -n 20'"
  exit 1
fi

echo "Found $TOTAL_JSON JSON files. Showing a few sample paths:"
for i in $(seq 0 $((TOTAL_JSON-1))); do
  [[ $i -ge 10 ]] && break
  echo "  - ${FILES[$i]}"
done

echo
echo "Preparing flat workspace at $FLAT_DIR ..."
mkdir -p "$FLAT_DIR"

# Copy into a flat directory, deduplicating name collisions
COPIED=0
for f in "${FILES[@]}"; do
  base="$(basename "$f")"
  if [[ -e "$FLAT_DIR/$base" ]]; then
    i=1
    while [[ -e "$FLAT_DIR/${i}_$base" ]]; do i=$((i+1)); done
    cp "$f" "$FLAT_DIR/${i}_$base"
  else
    cp "$f" "$FLAT_DIR/$base"
  fi
  COPIED=$((COPIED + 1))
done
echo "Prepared $COPIED JSON bundle files from local generation."

echo
echo "=== Flat dir quick count & sample ==="
ls -la "$FLAT_DIR" | head -n 50 || true
echo "====================================="

echo
echo "Uploading bundles to $FHIR_BASE_URL from $DATA_DIR ..."
python /app/upload_synthea.py \
  --base-url "$FHIR_BASE_URL" \
  --dir "$DATA_DIR" \
  --retry 1 \
  --workers 6

echo "Upload complete."
