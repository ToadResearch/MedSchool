#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-help}"; shift || true
base="${TARGET_FHIR_BASE:-http://hapi:8080/fhir}"

case "$cmd" in
  loinc)
    # Usage: loinc /data/LOINC_..._MULTI-AXIAL_HIERARCHY.zip /data/LOINC_..._Text.zip
    # Pass one or more -d files (order doesn't matter)
    if [ "$#" -lt 1 ]; then
      echo "Usage: loinc <zip...>"; exit 2
    fi
    exec hapi-fhir-cli upload-terminology -v r4 -t "$base" -u http://loinc.org $(printf ' -d %q' "$@")
    ;;

  snomed)
    # Usage: snomed /data/SnomedCT_InternationalRF2_PRODUCTION_YYYYMMDDT*.zip
    if [ "$#" -lt 1 ]; then
      echo "Usage: snomed <zip...>"; exit 2
    fi
    exec hapi-fhir-cli upload-terminology -v r4 -t "$base" -u http://snomed.info/sct $(printf ' -d %q' "$@")
    ;;

  icd10)
    # Optional: WHO ICD-10 (ClaML/XML)
    if [ "$#" -lt 1 ]; then
      echo "Usage: icd10 <zip-or-xml...>"; exit 2
    fi
    exec hapi-fhir-cli upload-terminology -v r4 -t "$base" -u http://hl7.org/fhir/sid/icd-10 $(printf ' -d %q' "$@")
    ;;

  reindex)
    exec hapi-fhir-cli reindex-terminology -v r4 -t "$base"
    ;;

  *)
    cat <<USAGE
Usage:
  loader loinc  </data/LOINC_*.zip ...>
  loader snomed </data/SnomedCT_InternationalRF2_*.zip ...>
  loader icd10  </data/icd*.zip|.xml ...>
  loader reindex
Env:
  TARGET_FHIR_BASE (default: http://hapi:8080/fhir)
USAGE
    ;;
esac
