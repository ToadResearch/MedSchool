#!/usr/bin/env bash
# test_val_server.sh
# ---------------------------------------------------------------------------
# Minimal, standalone tests for the HL7 validator-wrapper container (no HAPI).
#
# HOW TO START THE SERVER (detached; removed on stop):
#   docker run -d --name fhir-validator --rm -p 3500:3500 markiantorno/validator-wrapper:latest
#
# HOW TO STOP:
#   docker stop fhir-validator
#
# WHAT THIS SCRIPT DOES:
#   • ping         → GET /validator/version, /versions, /txStatus, /packStatus
#   • validate-ok  → POST /validate with a valid Patient (R4) using JSON envelope
#   • validate-bad → POST /validate with an invalid Patient (bad birthDate)
#   • demo         → runs ping, validate-ok, validate-bad (default)
#
# IMPORTANT:
#   • The official wrapper API expects JSON at POST /validate with:
#       { cliContext, filesToValidate[], sessionId }
#   • We explicitly set cliContext.sv = "4.0.1" (FHIR R4) to match your project.
#   • No multipart, no NGINX, no JWT — we hit the container directly on :3500.
#   • Override the base URL or version if needed:
#       BASE=http://127.0.0.1:3500 FHIR_VERSION=4.0.1 ./test_val_server.sh demo
# ---------------------------------------------------------------------------

set -euo pipefail

BASE="${BASE:-http://localhost:3500}"
# Pin to R4 (NOT R4B). R4 == 4.0.1; R4B would be 4.3.0 (don’t use that here).
FHIR_VERSION="${FHIR_VERSION:-4.0.1}"
LOCALE="${LOCALE:-en}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need curl
need jq
need uuidgen

hdr() { printf "\n\033[36m==> %s\033[0m\n" "$*"; }

ping_server() {
  hdr "GET /validator/version"
  curl -sf "$BASE/validator/version" | jq .

  hdr "GET /versions"
  curl -sf "$BASE/versions" | jq .

  hdr "GET /txStatus"
  curl -sf "$BASE/txStatus" | jq .

  hdr "GET /packStatus"
  curl -sf "$BASE/packStatus" | jq .

  hdr "GET /ig (available IG packages)"
  curl -sf "$BASE/ig" | jq .
}

# Build /validate payload using the required JSON envelope (no multipart).
# Args: $1 = fileName, $2 = compactJsonString
make_payload() {
  local fname="$1"
  local rjson="$2"
  local sid; sid="$(uuidgen)"

  # Add IGs/profiles if you want to validate against specific guides.
  # Example (US Core R4): "hl7.fhir.us.core#5.0.1" — still R4 4.0.1.
  jq -n \
    --arg fileName   "$fname" \
    --arg fileType   "json" \
    --arg fileContent "$rjson" \
    --arg sv         "$FHIR_VERSION" \
    --arg locale     "$LOCALE" \
    --arg sessionId  "$sid" \
  '{
     cliContext: {
       sv: $sv,
       locale: $locale
       # Optional:
       # , txServer: "https://tx.fhir.org"
       # , igs: ["hl7.fhir.us.core#5.0.1"]
       # , profiles: ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"]
     },
     filesToValidate: [
       { fileName: $fileName, fileContent: $fileContent, fileType: $fileType }
     ],
     sessionId: $sessionId
   }'
}

post_validate() {
  local name="$1"
  local resource_json="$2"

  hdr "POST /validate ($name) [FHIR sv=$FHIR_VERSION]"
  local compact; compact="$(jq -c . <<<"$resource_json")"
  local payload; payload="$(make_payload "$name" "$compact")"

  # Send JSON envelope to /validate
  local resp
  resp="$(curl -sf -H "Content-Type: application/json" -d "$payload" "$BASE/validate")"

  echo "$resp" | jq .

  # Basic summary
  local issue_count
  issue_count="$(echo "$resp" | jq '[.outcomes[]?.issues[]?] | length // 0')"
  echo "Issues: $issue_count"
  echo "$resp" | jq -r '
    (.outcomes[]?.issues // []) as $i
    | if ($i|length) == 0 then "No issues."
      else
        ($i | map(.level // .severity // "UNKNOWN") | group_by(.) | map({(.[0]): length}) | add) as $by
        | "By level/severity: " + ($by | tojson)
      end
  '
}

validate_ok() {
  local patient='{
    "resourceType":"Patient",
    "name":[{"family":"Doe","given":["Jane"]}],
    "gender":"female",
    "birthDate":"1990-04-03"
  }'
  post_validate "patient-ok.json" "$patient"
}

validate_bad() {
  local bad='{
    "resourceType":"Patient",
    "birthDate":"not-a-date"
  }'
  post_validate "patient-bad.json" "$bad"
}

cmd="${1:-demo}"
case "$cmd" in
  ping)          ping_server ;;
  validate-ok)   validate_ok ;;
  validate-bad)  validate_bad ;;
  demo)          ping_server; validate_ok; validate_bad ;;
  *) echo "Usage: $0 [ping|validate-ok|validate-bad|demo]"; exit 1 ;;
esac
