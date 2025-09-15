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

echo "Looking for locally generated Synthea JSON under $LOCAL_SYNTHEA_ROOT ..."
if [[ ! -d "$LOCAL_SYNTHEA_ROOT" ]]; then
  echo "ERROR: Local Synthea directory $LOCAL_SYNTHEA_ROOT not found."
  echo "Make sure the 'synthea' service has run and the 'synthea_out' volume is mounted into this container at /synthea."
  exit 1
fi

# Collect all JSON files produced by Synthea (often under /out/fhir/)
mapfile -d '' FILES < <(find "$LOCAL_SYNTHEA_ROOT" -type f -name '*.json' -print0 2>/dev/null || true)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "ERROR: No JSON bundles found in $LOCAL_SYNTHEA_ROOT."
  echo "Did the 'synthea' job finish successfully and write bundles to the volume?"
  exit 1
fi

echo "Preparing flat workspace at $FLAT_DIR ..."
mkdir -p "$FLAT_DIR"
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

echo "Uploading bundles to $FHIR_BASE_URL from $DATA_DIR ..."
python /app/upload_synthea.py \
  --base-url "$FHIR_BASE_URL" \
  --dir "$DATA_DIR" \
  --retry 1 \
  --workers 6

echo "Upload complete."
