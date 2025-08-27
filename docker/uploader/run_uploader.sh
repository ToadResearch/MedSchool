#!/usr/bin/env bash
set -euo pipefail

FHIR_BASE_URL="${FHIR_BASE_URL:-http://hapi:8080/fhir}"
ZIP_URL="${SYNTHEA_ZIP_URL:?SYNTHEA_ZIP_URL not set}"
WORKDIR="/tmp/uploader"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "Waiting for HAPI at $FHIR_BASE_URL/metadata ..."
until curl -fsS "$FHIR_BASE_URL/metadata" >/dev/null 2>&1; do
  sleep 2
done
echo "HAPI is ready."

echo "Downloading Synthea zip..."
curl -fsSL "$ZIP_URL" -o synthea.zip

echo "Unzipping..."
unzip -q synthea.zip -d data

echo "Uploading bundles to $FHIR_BASE_URL ..."
python /app/upload_synthea.py \
  --base-url "$FHIR_BASE_URL" \
  --dir "$WORKDIR/data" \
  --retry 1 \
  # --workers 1 \


echo "Upload complete."
