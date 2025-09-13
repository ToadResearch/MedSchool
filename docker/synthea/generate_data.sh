#!/usr/bin/env bash
set -euo pipefail

# Ensure exporter targets R4 bundles (transaction bundles recommended for HAPI)
# You can also mount a custom file and skip this one.
PROP="/app/synthea.properties"

echo "Generating Synthea data..."

# Run Synthea
java -jar /app/synthea.jar \
  --exporter.properties "$PROP" \
  -s 13541938151512 \
  -p 1000 \

echo "Synthea generation complete."