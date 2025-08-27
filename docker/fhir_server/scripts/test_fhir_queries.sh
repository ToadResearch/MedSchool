#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_fhir_queries.sh – Unified smoke + validation tests for the MedSchool
# stack *through the NGINX gateway* (JWT-protected).
#
# It explicitly exercises ALL THREE validation levels with clear labels:
#   [SERVER]   POST [base]/$validate
#              • Service: HAPI JPA (only if advertised in CapabilityStatement)
#              • Payload: Parameters { mode, resource, ... }
#   [TYPE]     POST [base]/{type}/$validate
#              • Service: HAPI JPA
#              • Payload: raw resource (or Parameters; raw is fine)
#   [INSTANCE] POST [base]/{type}/{id}/$validate
#              • Service: HAPI JPA
#              • Payload: none (validate stored copy) OR Parameters { mode=update, resource }
#
# PLUS a server-level validation via the **HL7 validator-wrapper** behind the gateway:
#   [WRAPPER]  POST /validator/validate  (sync)  → OperationOutcome    (expects RAW FHIR JSON)
#              POST /validator/requests (async) → poll until OO       (expects JSON envelope)
#
# IMPORTANT BEHAVIOR:
#   • Per spec/HAPI, $validate returns HTTP 200 when validation *runs*, even if
#     the resource is invalid. Pass/fail lives in OperationOutcome.issue[].
#   • Two different failure surfaces exist:
#       - PARSE-BAD  → HTTP 400 + OperationOutcome (parser rejects payload)
#       - VALIDATOR-BAD (parse-good but breaks FHIR rules) → HTTP 200 +
#         OperationOutcome with issue.severity == "error" or "fatal"
#   • Escape the literal "$" in shell strings as "\$". Avoid "%24" via NGINX.
#   • Many stock HAPI JPA deployments do NOT implement server-level $validate.
#     We auto-detect via CapabilityStatement and [SKIP] those tests if absent.
#   • NGINX 400 vs HAPI 4xx:
#       - An HTML "nginx/1.28.0" page means the gateway rejected the request
#         before HAPI saw it (not an auth failure; your gateway returns 401 JSON).
#
# Quality-of-life:
#   • Only print the Location header when we *capture* it (avoid stray URLs).
#   • Create uses a temp file and `-H "Expect:"` so NGINX never balks at
#     chunking/100-continue shenanigans.
#
# NOTE ON THE HL7 VALIDATOR ENDPOINTS (critical to this fix):
#   • /validator/validate  expects a RAW FHIR resource body (Content-Type: application/fhir+json).
#   • /validator/requests expects a JSON envelope: { "resource": <FHIR JSON> } and returns a job you can poll.
#   • This script now tries RAW sync first; if the server rejects that mode, it falls back to the async flow.
#   • All multipart/form-data permutations were removed per request.
# ---------------------------------------------------------------------------

set -euo pipefail
IFS=$'\n\t'

# ───────────────────────── Repository root + .env ──────────────────────────
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ENV_FILE="$REPO_ROOT/.env"
if [[ -z "${FHIR_BEARER_TOKEN:-}" && -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

# ─────────────────────────── Dependency checks ─────────────────────────────
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need curl; need jq; need tput || true; need sed

# ─────────────────────────────── Colours ───────────────────────────────────
if tput setaf 1 >/dev/null 2>&1; then
  RED=$(tput setaf 1); GRN=$(tput setaf 2); BLU=$(tput setaf 4)
  MAG=$(tput setaf 5); YLW=$(tput setaf 3); CYN=$(tput setaf 6); RST=$(tput sgr0)
else
  RED=""; GRN=""; BLU=""; MAG=""; YLW=""; CYN=""; RST=""
fi

# ─────────────────────────── Env sanity checks ─────────────────────────────
[[ -z "${FHIR_BEARER_TOKEN:-}" ]] && { echo "${RED}FHIR_BEARER_TOKEN not set${RST}"; exit 1; }

HOST="localhost"
PORT="${HAPI_PORT:-8080}"
BASE_URL="http://$HOST:$PORT/fhir"

# Derive the gateway origin (strip trailing /fhir[/] if present) and validator base
ORIGIN=$(printf '%s' "$BASE_URL" | sed -E 's#/fhir/?$##')
VALIDATOR_BASE="${ORIGIN}/validator"

# Optional: allow override of BASE_URL via --url
if [[ ${1:-} == "--url" && -n ${2:-} ]]; then
  BASE_URL="$2"
  ORIGIN=$(printf '%s' "$BASE_URL" | sed -E 's#/fhir/?$##')
  VALIDATOR_BASE="${ORIGIN}/validator"
  shift 2
fi

echo -e "${BLU}🏥  HAPI FHIR at:  ${BASE_URL}${RST}"
echo -e "${BLU}🧪  Validator at:  ${VALIDATOR_BASE}${RST}\n"

# ───────────────────── CapabilityStatement helpers ─────────────────────────
CAPS_JSON="$(curl -sS -H 'Accept: application/fhir+json' "$BASE_URL/metadata")" || CAPS_JSON=""

supports_system_validate() {
  [[ -z "$CAPS_JSON" ]] && return 1
  jq -e 'any(.rest[]?.operation[]?; .name=="validate" or .name=="$validate")' >/dev/null 2>&1 <<<"$CAPS_JSON"
}

# ─────────────────────────── Core request helper ───────────────────────────
# request <label> <method> <url> <data-var-name|-> <expect-http-code>
# NOTE: This echos the Location header to stdout (so callers can capture it).
# For calls where you don't want Location printed, redirect stdout to /dev/null.
request() {
  local label="$1"; local method="$2"; local url="$3"; local data_ref="$4"; local want="$5"
  local data=""
  [[ "$data_ref" != "-" ]] && data="${!data_ref}"

  printf "%-80s" "$label" >&2

  local body; body=$(mktemp); local hdrs; hdrs=$(mktemp)
  local curl_opts=( -sS -w "%{http_code}" -X "$method" "$url"
                    -H "Authorization: Bearer $FHIR_BEARER_TOKEN"
                    -H "Accept: application/fhir+json"
                    -D "$hdrs" )
  [[ -n "$data" ]] && curl_opts+=( -H "Content-Type: application/fhir+json" --data-binary "$data" )

  local http_code; http_code=$(curl "${curl_opts[@]}" -o "$body")

  if [[ "$http_code" == "$want" ]]; then
    echo -e " ${GRN}✔${RST} (HTTP $http_code)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP $http_code, wanted $want)" >&2
    if jq -e . "$body" >/dev/null 2>&1; then
      jq -C . <"$body" | sed 's/^/   /' >&2
    else
      sed 's/^/   /' <"$body" >&2
    fi
  fi

  # Echo Location for callers that want to capture it
  awk 'BEGIN{IGNORECASE=1} /^Location:/ {sub(/^[^:]*: /,""); gsub(/\r/,""); print}' "$hdrs"

  rm -f "$body" "$hdrs"
}

# ───────────────────── $validate helpers (with level tags) ─────────────────
validate_type_expect_200() { # label jsonVar expectError(0|1)
  local label="$1" var="$2" expect_err="$3"
  local data="${!var}"
  printf "%-80s" "$label" >&2
  local body; body=$(mktemp)
  local code; code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "$BASE_URL/Patient/\$validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json" \
        -H "Content-Type: application/fhir+json" \
        --data-binary "$data")
  if [[ "$code" != "200" ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code, wanted 200)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || sed 's/^/   /' <"$body" >&2
    rm -f "$body"; return 1
  fi
  local has_error=1
  jq -e '.issue[]? | select(.severity=="error" or .severity=="fatal")' <"$body" >/dev/null || has_error=0
  if [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; validator errors present as expected)" >&2
  elif [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; no validator errors as expected)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP 200; OperationOutcome mismatch)" >&2
    jq -C . <"$body" | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  rm -f "$body"
}

validate_type_expect_http() { # label jsonVar expectHttp
  local label="$1" var="$2" want="$3"
  local data="${!var}"
  printf "%-80s" "$label" >&2
  local code; code=$(curl -sS -o /dev/null -w "%{http_code}" \
        -X POST "$BASE_URL/Patient/\$validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json" \
        -H "Content-Type: application/fhir+json" \
        --data-binary "$data")
  if [[ "$code" == "$want" ]]; then
    echo -e " ${GRN}✔${RST} (HTTP $code as expected)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP $code, wanted $want)" >&2
  fi
}

# [SERVER] Parameters wrapper → /$validate  (NOTE: many JPA servers do NOT implement this)
validate_server_if_supported() { # label jsonVar mode expectError(0|1)
  local label="$1" var="$2" mode="$3" expect_err="$4"
  if ! supports_system_validate; then
    printf "%-80s" "$label" >&2
    echo " ${YLW}SKIP${RST} (server-level \$validate not advertised in CapabilityStatement)" >&2
    return 0
  fi
  local resource_json="${!var}"
  local params; params=$(jq -n --arg m "$mode" --argjson r "$resource_json" \
    '{resourceType:"Parameters",parameter:[{"name":"mode","valueCode":$m},{"name":"resource","resource":$r}] }')
  printf "%-80s" "$label" >&2
  local body; body=$(mktemp)
  local code; code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "$BASE_URL/\$validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json" \
        -H "Content-Type: application/fhir+json" \
        --data-binary "$params")
  if [[ "$code" != "200" ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code, wanted 200)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  local has_error=1
  jq -e '.issue[]? | select(.severity=="error" or .severity=="fatal")' <"$body" >/dev/null || has_error=0
  if [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; errors present as expected)" >&2
  elif [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; no errors as expected)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP 200; OperationOutcome mismatch)" >&2
    jq -C . <"$body" | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  rm -f "$body"
}

# [INSTANCE] Validate stored instance → /Patient/{id}/$validate
validate_instance_existing() { # label id expectError(0|1)
  local label="$1" patient_id="$2" expect_err="$3"
  printf "%-80s" "$label" >&2
  local body; body=$(mktemp)
  local code; code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "$BASE_URL/Patient/${patient_id}/\$validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json")
  if [[ "$code" != "200" ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code, wanted 200)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  local has_error=1
  jq -e '.issue[]? | select(.severity=="error" or .severity=="fatal")' <"$body" >/dev/null || has_error=0
  if [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; stored instance has no errors)" >&2
  elif [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; stored instance reports errors as expected)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP 200; OperationOutcome mismatch for stored instance)" >&2
    jq -C . <"$body" | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  rm -f "$body"
}

# Validate a proposed update against a stored instance using Parameters/mode=update
validate_instance_update() { # label id badJsonVar expectError(0|1)
  local label="$1" patient_id="$2" var="$3" expect_err="$4"
  local bad_json="${!var}"
  local proposed; proposed=$(jq --arg id "$patient_id" '. + {id:$id}' <<<"$bad_json")
  local params; params=$(jq -n --argjson r "$proposed" \
    '{resourceType:"Parameters",parameter:[{"name":"mode","valueCode":"update"},{"name":"resource","resource":$r}] }')
  printf "%-80s" "$label" >&2
  local body; body=$(mktemp)
  local code; code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "$BASE_URL/Patient/${patient_id}/\$validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json" \
        -H "Content-Type: application/fhir+json" \
        --data-binary "$params")
  if [[ "$code" != "200" ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code, wanted 200)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  local has_error=1
  jq -e '.issue[]? | select(.severity=="error" or .severity=="fatal")' <"$body" >/dev/null || has_error=0
  if [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; proposed update correctly reports errors)" >&2
  elif [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST} (HTTP 200; proposed update has no errors)" >&2
  else
    echo -e " ${RED}✖${RST} (HTTP 200; OperationOutcome mismatch for proposed update)" >&2
    jq -C . <"$body" | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi
  rm -f "$body"
}

# ──────────────── Validator-wrapper helpers (server-level) ─────────────────
# NOTE: /validator/validate expects RAW FHIR JSON.
#       /validator/requests expects { "resource": <FHIR JSON> } and returns a job we poll.

validator_available() {
  # Expect 200/204 when authorized
  curl -sS -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
    "${VALIDATOR_BASE}/" | grep -qE '^(200|204)$'
}

# Try sync: POST /validator/validate with raw resource → OperationOutcome
validator_validate_sync() { # label jsonVar expectError(0|1)
  local label="$1" var="$2" expect_err="$3"
  local data="${!var}"
  printf "%-80s" "$label (sync RAW)" >&2

  local body; body=$(mktemp)
  local code; code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "${VALIDATOR_BASE}/validate" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json, application/json, text/plain, */*" \
        -H "Content-Type: application/fhir+json" \
        --data-binary "$data")

  if [[ "$code" != "200" && "$code" != "201" ]]; then
    echo -e " ${YLW}SKIP${RST} (HTTP $code on /validate; falling back to async /requests)" >&2
    rm -f "$body"
    return 99
  fi

  local is_oo=0; local has_error=0
  if jq -e '.resourceType=="OperationOutcome"' <"$body" >/dev/null 2>&1; then
    is_oo=1
    jq -e '.issue[]? | select((.severity|ascii_downcase)=="error" or (.severity|ascii_downcase)=="fatal")' <"$body" >/dev/null 2>&1 && has_error=1 || true
  elif jq -e '.outcome.resourceType=="OperationOutcome"' <"$body" >/dev/null 2>&1; then
    is_oo=1
    jq -e '.outcome.issue[]? | select((.severity|ascii_downcase)=="error" or (.severity|ascii_downcase)=="fatal")' <"$body" >/dev/null 2>&1 && has_error=1 || true
  fi

  if [[ "$is_oo" -ne 1 ]]; then
    echo -e " ${YLW}SKIP${RST} (/validate did not return OperationOutcome JSON; trying async /requests)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || sed 's/^/   /' <"$body" >&2
    rm -f "$body"
    return 99
  fi

  if [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST}" >&2
  elif [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST}" >&2
  else
    echo -e " ${RED}✖${RST} (sync OO severity mismatch)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || true
    rm -f "$body"; return 1
  fi

  rm -f "$body"
  return 0
}

# Async flow: POST /validator/requests → poll Location or id → OperationOutcome
validator_validate_async() { # label jsonVar expectError(0|1)
  local label="$1" var="$2" expect_err="$3"
  local data="${!var}"
  printf "%-80s" "$label (async /requests)" >&2

  local payload; payload=$(jq -n --argjson r "$data" '{resource: $r}')

  local body; body=$(mktemp); local hdrs; hdrs=$(mktemp)
  local code; code=$(curl -sS -o "$body" -D "$hdrs" -w "%{http_code}" \
        -X POST "${VALIDATOR_BASE}/requests" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/json, application/fhir+json, text/plain, */*" \
        -H "Content-Type: application/json" \
        --data-binary "$payload")

  if ! [[ "$code" =~ ^(200|201)$ ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code on /requests)" >&2
    jq -C . <"$body" 2>/dev/null | sed 's/^/   /' >&2 || sed 's/^/   /' <"$body" >&2
    rm -f "$body" "$hdrs"
    return 1
  fi

  local loc; loc=$(awk 'BEGIN{IGNORECASE=1} /^Location:/ {sub(/^[^:]*: /,""); gsub(/\r/,""); print}' "$hdrs")
  local id=""
  if [[ -z "$loc" ]]; then
    id=$(jq -r '(.id // .requestId // .token // empty)' <"$body")
  fi
  rm -f "$body" "$hdrs"

  local poll_url=""
  if [[ -n "$loc" ]]; then
    if [[ "$loc" =~ ^https?:// ]]; then
      poll_url="$loc"
    else
      poll_url="${VALIDATOR_BASE%/}/${loc#/}"
    fi
  elif [[ -n "$id" ]]; then
    poll_url="${VALIDATOR_BASE}/requests/${id}"
  else
    poll_url="${VALIDATOR_BASE}/requests"
  fi

  local tries=0; local max_tries=10; local sleep_s=1
  local got_oo=0; local has_error=0

  while (( tries < max_tries )); do
    local poll_body; poll_body=$(mktemp)
    local pcode; pcode=$(curl -sS -o "$poll_body" -w "%{http_code}" \
                      -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
                      -H "Accept: application/json, application/fhir+json, text/plain, */*" \
                      "$poll_url")

    if jq -e '.resourceType=="OperationOutcome"' <"$poll_body" >/dev/null 2>&1; then
      got_oo=1
      jq -e '.issue[]? | select((.severity|ascii_downcase)=="error" or (.severity|ascii_downcase)=="fatal")' <"$poll_body" >/dev/null 2>&1 && has_error=1 || true
      rm -f "$poll_body"; break
    elif jq -e '.outcome.resourceType=="OperationOutcome"' <"$poll_body" >/dev/null 2>&1; then
      got_oo=1
      jq -e '.outcome.issue[]? | select((.severity|ascii_downcase)=="error" or (.severity|ascii_downcase)=="fatal")' <"$poll_body" >/dev/null 2>&1 && has_error=1 || true
      rm -f "$poll_body"; break
    fi

    if jq -e '(.status? // "") | IN("failed","error")' <"$poll_body" >/dev/null 2>&1; then
      echo -e " ${RED}✖${RST} (async job failed)" >&2
      jq -C . <"$poll_body" | sed 's/^/   /' >&2
      rm -f "$poll_body"
      return 1
    fi

    rm -f "$poll_body"
    tries=$((tries+1))
    sleep "$sleep_s"
  done

  if [[ "$got_oo" -ne 1 ]]; then
    echo -e " ${RED}✖${RST} (no OperationOutcome returned by async job)" >&2
    echo "   Poll URL: $poll_url" >&2
    return 1
  fi

  if [[ "$expect_err" -eq 1 && "$has_error" -eq 1 ]]; then
    echo -e " ${GRN}✔${RST}" >&2
  elif [[ "$expect_err" -eq 0 && "$has_error" -eq 0 ]]; then
    echo -e " ${GRN}✔${RST}" >&2
  else
    echo -e " ${RED}✖${RST} (async OO severity mismatch)" >&2
    return 1
  fi
}

validator_validate() { # label jsonVar expectError(0|1)
  local label="$1" var="$2" expect_err="$3"
  # Try RAW sync at /validate; if not supported, fall back to /requests
  validator_validate_sync "$label" "$var" "$expect_err" && return 0
  [[ $? -eq 99 ]] && validator_validate_async "$label" "$var" "$expect_err" && return 0
  return 1
}

# ───────────────────────────── Fixtures (shared) ────────────────────────────
PATIENT_GOOD=$(cat <<'JSON'
{
  "resourceType":"Patient",
  "identifier":[{"system":"http://hospital.medschool/patients","value":"12345"}],
  "name":[{"family":"Doe","given":["Jane"]}],
  "gender":"female",
  "birthDate":"1990-04-03"
}
JSON
)

# VALIDATOR-bad but PARSE-good:
# - Includes two elements of the same choice[x] (deceased[x]) which violates the spec.
PATIENT_BAD_VALIDATOR=$(cat <<'JSON'
{
  "resourceType":"Patient",
  "name":[{"family":"Doe","given":["Jane"]}],
  "deceasedBoolean": false,
  "deceasedDateTime": "2020-01-01T00:00:00Z"
}
JSON
)

# PARSE-bad:
# - Invalid date format; parser will reject before validator runs → HTTP 400.
PATIENT_BAD_PARSE=$(cat <<'JSON'
{
  "resourceType":"Patient",
  "birthDate":"not-a-date"
}
JSON
)

# ──────────────────── Robust creator (temp file + no Expect) ───────────────
create_patient_id() { # label jsonVar -> echoes id or empty
  local label="$1" var="$2"
  local data="${!var}"
  printf "%-80s" "$label" >&2

  local tmp; tmp=$(mktemp)
  printf '%s' "$data" >"$tmp"

  local body hdrs code
  body=$(mktemp); hdrs=$(mktemp)
  code=$(curl -sS -o "$body" -w "%{http_code}" \
        -X POST "$BASE_URL/Patient" \
        -H "Authorization: Bearer $FHIR_BEARER_TOKEN" \
        -H "Accept: application/fhir+json" \
        -H "Content-Type: application/fhir+json" \
        -H "Expect:" \
        --data-binary "@$tmp" \
        -D "$hdrs")

  if [[ "$code" != "201" ]]; then
    echo -e " ${RED}✖${RST} (HTTP $code, wanted 201)" >&2
    if jq -e . "$body" >/dev/null 2>&1; then jq -C . <"$body" | sed 's/^/   /' >&2
    else sed 's/^/   /' <"$body" >&2; fi
    rm -f "$tmp" "$body" "$hdrs"
    echo "" # echo empty so caller knows to skip instance-level tests
    return 1
  fi

  echo -e " ${GRN}✔${RST} (HTTP 201)" >&2

  local loc id
  loc=$(awk 'BEGIN{IGNORECASE=1} /^Location:/ {sub(/^[^:]*: /,""); gsub(/\r/,""); print}' "$hdrs")
  id=$(echo "$loc" | sed -E 's#.*/Patient/([^/]+)/.*#\1#')

  rm -f "$tmp" "$body" "$hdrs"
  printf '%s' "$id"
}

# ===========================================================================#
#                                   TESTS                                    #
# ===========================================================================#

# 0) Info
request "[INFO]    GET  /metadata (CapabilityStatement)" GET "$BASE_URL/metadata" - 200 >/dev/null

# 1) [SERVER] HAPI $validate (Parameters) – only if advertised
validate_server_if_supported "[SERVER] POST /\$validate (mode=create) — Good Patient (expect no errors)" PATIENT_GOOD create 0
validate_server_if_supported "[SERVER] POST /\$validate (mode=create) — Bad Patient (expect errors)"     PATIENT_BAD_VALIDATOR create 1

# 2) [TYPE] HAPI $validate (raw Patient)
validate_type_expect_200  "[TYPE]   POST /Patient/\$validate — Good Patient (expect no validator errors)"        PATIENT_GOOD           0
validate_type_expect_200  "[TYPE]   POST /Patient/\$validate — Bad Patient (choice[x] conflict → expect errors)" PATIENT_BAD_VALIDATOR  1
validate_type_expect_http "[TYPE]   POST /Patient/\$validate — Parse-bad Patient (invalid date → expect 400)"     PATIENT_BAD_PARSE      400

# 3) CRUD + [INSTANCE] HAPI validations
PID="$(create_patient_id "[CRUD]   POST /Patient — Create Patient for instance-level tests" PATIENT_GOOD || true)"
if [[ -z "$PID" ]]; then
  printf "%-80s" "[CRUD]   GET  /Patient/<id> — Read back created Patient" >&2;        echo " ${YLW}SKIP${RST} (create failed)" >&2
  printf "%-80s" "[INSTANCE] POST /Patient/<id>/\$validate — Validate stored Patient" >&2; echo " ${YLW}SKIP${RST} (create failed)" >&2
  printf "%-80s" "[INSTANCE] POST /Patient/<id>/\$validate (mode=update) — Bad update" >&2; echo " ${YLW}SKIP${RST} (create failed)" >&2
  printf "%-80s" "[CRUD]   DELETE /Patient/<id> — Delete Patient" >&2;                 echo " ${YLW}SKIP${RST} (create failed)" >&2
  printf "%-80s" "[CRUD]   GET  /Patient/<id> — Confirm deletion (expect 410 Gone)" >&2; echo " ${YLW}SKIP${RST} (create failed)" >&2
else
  request "[CRUD]   GET  /Patient/$PID — Read back created Patient" GET "$BASE_URL/Patient/$PID" - 200 >/dev/null
  validate_instance_existing "[INSTANCE] POST /Patient/$PID/\$validate — Validate stored Patient (expect no errors)" "$PID" 0
  validate_instance_update   "[INSTANCE] POST /Patient/$PID/\$validate (mode=update) — Bad update (expect errors)"   "$PID" PATIENT_BAD_VALIDATOR 1
  request "[CRUD]   DELETE /Patient/$PID — Delete Patient" DELETE "$BASE_URL/Patient/$PID" - 200 >/dev/null
  request "[CRUD]   GET  /Patient/$PID — Confirm deletion (expect 410 Gone)" GET "$BASE_URL/Patient/$PID" - 410 >/dev/null
fi

# 4) [WRAPPER] HL7 validator-wrapper (server-level)
printf "%-80s" "[WRAPPER] GET  /validator/ — UI/API root" >&2
if validator_available; then
  echo -e " ${GRN}✔${RST}" >&2
else
  echo -e " ${RED}✖${RST} (validator unavailable; check gateway proxy and container health)" >&2
fi

# Validate via wrapper:
#   • Try RAW at /validator/validate (sync). If server says nope, fall back to /validator/requests (async).
validator_validate "[WRAPPER] POST /validator/(validate|requests) — Good Patient (expect no errors)" PATIENT_GOOD 0
validator_validate "[WRAPPER] POST /validator/(validate|requests) — Bad Patient (expect errors)"     PATIENT_BAD_VALIDATOR 1

echo -e "\n${MAG}✨  All tests finished${RST}"
