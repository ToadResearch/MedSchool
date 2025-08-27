#!/busybox sh
# -----------------------------------------------------------------------------
# PID-1 entrypoint that:
#  1) launches upstream validator-wrapper ($@) in background
#  2) waits for it to serve on :3500
#  3) prewarms R4 (or chosen sv) by POSTing a tiny Patient to /validate
#  4) waits on the server process
# Tunables:
#   VALIDATOR_PREWARM=true|false
#   VALIDATOR_PREWARM_SV=4.0.1
#   VALIDATOR_PREWARM_TIMEOUT=90   (seconds to wait for HTTP readiness)
#   VALIDATOR_STARTUP_WAIT=60      (optional grace period before readiness loop)
# -----------------------------------------------------------------------------
set -eu

log() { echo "[validator-entrypoint] $*"; }

PREWARM="${VALIDATOR_PREWARM:-true}"
SV="${VALIDATOR_PREWARM_SV:-4.0.1}"
READY_TIMEOUT="${VALIDATOR_PREWARM_TIMEOUT:-90}"
STARTUP_GRACE="${VALIDATOR_STARTUP_WAIT:-60}"

# Forward TERM/INT to child
child_pid=""
term() { if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then kill -TERM "$child_pid"; fi; }
trap term INT TERM

# 1) Launch upstream server (whatever CMD the base image defines)
#    If no CMD was provided, fail fast with a helpful message.
if [ $# -eq 0 ]; then
  log "ERROR: no command passed to entrypoint (base image CMD missing?)."
  exit 127
fi

log "Starting validator-wrapper: $*"
"$@" &
child_pid=$!

# 2) Give the JVM/ktor a moment before we begin readiness checks
if [ "$STARTUP_GRACE" -gt 0 ]; then
  sleep "$STARTUP_GRACE" || true
fi

# Wait for HTTP readiness on /versions (max READY_TIMEOUT)
i=0
until /busybox wget -q -O- http://localhost:3500/versions >/dev/null 2>&1; then
  i=$((i+1))
  if [ "$i" -ge "$READY_TIMEOUT" ]; then
    log "Server did not become ready within ${READY_TIMEOUT}s; continuing anyway."
    break
  fi
  sleep 1
done

# 3) Prewarm (optional)
if [ "$PREWARM" = "true" ]; then
  log "Prewarming FHIR sv=${SV} cache via /validate..."
  PREWARM_JSON='{"cliContext":{"sv":"'"$SV"'","locale":"en"},"filesToValidate":[{"fileName":"ping.json","fileType":"json","fileContent":"{\"resourceType\":\"Patient\",\"name\":[{\"family\":\"Prewarm\"}]}"}],"sessionId":"PREWARM-'"$SV"'"}'
  # Busybox wget supports --header and --post-data
  /busybox wget -q -O- \
    --header='Content-Type: application/json' \
    --post-data="$PREWARM_JSON" \
    http://localhost:3500/validate >/dev/null 2>&1 || log "Prewarm request failed (continuing)."
  log "Prewarm done."
else
  log "Prewarm disabled."
fi

# 4) Wait on the JVM
wait "$child_pid"
