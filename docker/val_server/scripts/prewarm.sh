#!/bin/sh
# docker/val_server/prewarm.sh
# Purpose: pull R4 (4.0.1) packages into the validator’s cache by hitting /validate
# Notes:
#   - Runs inside the curlimages/curl container (not the validator), so avoid /busybox paths.
#   - Waits for the validator API to respond, then validates a good + bad Patient.
#   - Uses sessionId to keep the IGs cached between calls.
set -eu

HOST="${VALIDATOR_HOST:-validator}"
PORT="${VALIDATOR_PORT:-3500}"
SV="${VALIDATOR_SV:-4.0.1}"
TIMEOUT="${PREWARM_TIMEOUT_SECS:-180}"
BASE="http://$HOST:$PORT"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# Generate a session id without relying on uuidgen/busybox
SESSION_ID="$( (cat /proc/sys/kernel/random/uuid 2>/dev/null) || echo "sess-$(date +%s)-$$" )"

wait_for() {
  path="$1"
  deadline=$(( $(date +%s) + TIMEOUT ))
  while :; do
    if curl -fsS "$BASE$path" >/dev/null 2>&1; then
      return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      return 1
    fi
    sleep 2
  done
}

log "Waiting for validator at $BASE ..."
wait_for "/versions" || { log "Timeout waiting for /versions"; exit 1; }
wait_for "/validator/version" || { log "Timeout waiting for /validator/version"; exit 1; }
log "Validator API responding."

# Minimal payloads to force-load R4 packages
payload_ok=$(cat <<JSON
{
  "cliContext": { "sv": "$SV", "locale": "en" },
  "filesToValidate": [
    {
      "fileName": "patient-ok.json",
      "fileType": "json",
      "fileContent": "{\"resourceType\":\"Patient\",\"name\":[{\"family\":\"Doe\",\"given\":[\"Jane\"]}],\"gender\":\"female\",\"birthDate\":\"1990-04-03\"}"
    }
  ],
  "sessionId": "$SESSION_ID"
}
JSON
)

payload_bad=$(cat <<JSON
{
  "cliContext": { "sv": "$SV", "locale": "en" },
  "filesToValidate": [
    {
      "fileName": "patient-bad.json",
      "fileType": "json",
      "fileContent": "{\"resourceType\":\"Patient\",\"birthDate\":\"not-a-date\"}"
    }
  ],
  "sessionId": "$SESSION_ID"
}
JSON
)

log "Warm-up 1/2 → POST /validate (ok)"
curl -fsS -H "Content-Type: application/json" -d "$payload_ok" "$BASE/validate" >/dev/null || log "ok warm-up failed (non-fatal)"

log "Warm-up 2/2 → POST /validate (bad)"
curl -fsS -H "Content-Type: application/json" -d "$payload_bad" "$BASE/validate" >/dev/null || log "bad warm-up failed (non-fatal)"

log "Prewarm complete. Session: $SESSION_ID"
