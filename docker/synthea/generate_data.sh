#!/usr/bin/env bash
set -euo pipefail

PROP="/app/synthea.properties"
JAR="/app/synthea.jar"
OUT_DIR="/synthea"

mkdir -p "${OUT_DIR}"
chmod 0777 "${OUT_DIR}" || true

# Clean any leftover internal default output to avoid FileAlreadyExistsException noise
rm -rf /app/out || true

# Option A: also clean the mounted out dir (uncomment if you want a fresh /out every run)
rm -rf "${OUT_DIR:?}/"* || true

echo "Generating Synthea data (target OUT_DIR=${OUT_DIR}) ..."

# Keep population small while debugging; crank it up later.
java -jar "${JAR}" \
     -c "${PROP}" \
     -s 13541938151512 \
     -cs 3381 \
     -r 20250913 \
     -p 111

echo "Synthea finished. Verifying output under ${OUT_DIR} ..."
# Accept common Synthea outputs: .json, .json.gz, .ndjson
collect_matches() {
  local root="$1"
  find "$root" -type f \( -iname '*.json' -o -iname '*.json.gz' -o -iname '*.ndjson' \) -print0 2>/dev/null || true
}