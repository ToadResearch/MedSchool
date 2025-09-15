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



# 1) Look in the intended /out
# mapfile -d '' FILES < <(collect_matches "${OUT_DIR}")
# COUNT="${#FILES[@]}"

# if [[ "${COUNT}" -eq 0 ]]; then
#   echo "No files found in ${OUT_DIR}. Harvesting from likely defaults…"

#   # Known Synthea defaults/fallbacks (jar or wrapper may write here)
#   CANDIDATES=(
#     "/app/output"
#     "/app/output/fhir"
#     "/output"
#     "/output/fhir"
#   )

#   for cand in "${CANDIDATES[@]}"; do
#     if [[ -d "$cand" ]]; then
#       echo "  Checking candidate: $cand"
#       mapfile -d '' FALLBACK < <(collect_matches "$cand")
#       if [[ "${#FALLBACK[@]}" -gt 0 ]]; then
#         echo "  Found ${#FALLBACK[@]} files in $cand; copying to ${OUT_DIR} (flattening)…"
#         mkdir -p "${OUT_DIR}"
#         for f in "${FALLBACK[@]}"; do
#           base="$(basename "$f")"
#           if [[ -e "${OUT_DIR}/${base}" ]]; then
#             i=1
#             while [[ -e "${OUT_DIR}/${i}_${base}" ]]; do i=$((i+1)); done
#             cp "$f" "${OUT_DIR}/${i}_${base}"
#           else
#             cp "$f" "${OUT_DIR}/${base}"
#           fi
#         done
#         break
#       fi
#     fi
#   done

#   # Re-scan /out after harvesting
#   mapfile -d '' FILES < <(collect_matches "${OUT_DIR}")
#   COUNT="${#FILES[@]}"
# fi

# if [[ "${COUNT}" -eq 0 ]]; then
#   echo "ERROR: Still no export files under ${OUT_DIR}."
#   echo "Diagnostics:"
#   echo "  - exporter.baseDirectory (properties): $(grep -nE '^exporter\.baseDirectory\s*=' "${PROP}" || echo 'not set')"
#   echo "  - Contents of /app/output (if exists):"
#   ls -la /app/output 2>/dev/null || true
#   echo "  - Contents of ${OUT_DIR}:"
#   ls -la "${OUT_DIR}" 2>/dev/null || true
#   exit 1
# fi

# echo "Success: found ${COUNT} exported files under ${OUT_DIR}."
# echo "Sample files:"
# for i in $(seq 0 $((COUNT-1))); do
#   [[ $i -ge 10 ]] && break
#   echo "  - ${FILES[$i]}"
# done

# echo "Synthea generation complete."
