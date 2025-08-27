#!/usr/bin/env bash
# docker/sandbox/scripts/standalone_test.sh
# ---------------------------------------------------------------------------
# Portable smoke-test + micro-bench for the Python sandbox image (medschool-sandbox).
# Works on macOS /bin/bash 3.2 and on GNU Bash.
# ---------------------------------------------------------------------------
set -euo pipefail

IMAGE="medschool-sandbox"

# Resolve repo root so we can call docker compose from anywhere
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.yaml"

# ---- catalogue:  name|code (single-line) ---------------------------------
TESTS=(
  "hello|print('hello from sandbox')"
  "numpy_norm|import json, numpy as np; print(json.dumps({'norm': float(np.linalg.norm([3,4]))}))"
  "pandas_shape|import pandas as pd, json; df=pd.DataFrame({'a':[1,2,3]}); print(json.dumps({'shape': df.shape}))"
)

# ---- ensure image exists --------------------------------------------------
if ! docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
  echo "⏳  Image '$IMAGE' not found – building via docker compose…"
  docker compose -f "$COMPOSE_FILE" --env-file "$ROOT/.env" build sandbox
  echo "✅  Build complete."
fi

# ---- helper: monotonic timestamp (float seconds) --------------------------
now() {
  python - <<'PY'
import time, sys
sys.stdout.write("{:.6f}".format(time.time()))
PY
}

# ---- run a single test ----------------------------------------------------
run_test() {
  local name="$1" code="$2"
  printf "\n\033[1m▶ Test: %s\033[0m\n" "$name"

  local t0 t1 runtime out exit_code
  t0=$(now)

  set +e
  out=$(echo "$code" | docker run --rm -i \
            --network none \
            --pids-limit 64 \
            --memory 512m \
            --cpus 1 \
            --read-only \
            --cap-drop ALL \
            --security-opt no-new-privileges:true \
            --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
            --tmpfs /home:rw,noexec,nosuid,nodev,size=64m \
            -e PYTHONUNBUFFERED=1 \
            -e MPLBACKEND=Agg \
            -e MPLCONFIGDIR=/tmp \
            -e XDG_CACHE_HOME=/tmp \
            -e OPENBLAS_NUM_THREADS=1 \
            -e OMP_NUM_THREADS=1 \
            -e NUMEXPR_MAX_THREADS=1 \
            "$IMAGE" python - 2>&1)
  exit_code=$?
  set -e

  t1=$(now)
  runtime=$(python - <<PY
import sys, decimal
print(format(decimal.Decimal("$t1") - decimal.Decimal("$t0"), ".3f"))
PY
)

  local out_len=${#out}
  echo "exit-code           : $exit_code"
  echo "runtime             : ${runtime}s"
  echo "stdout/stderr bytes : $out_len"
  echo "------------------------------------"
  printf '%s\n' "${out:0:200}"
  if (( out_len > 200 )); then
    echo "[…truncated…]"
  fi
}

# ---- loop through catalogue ----------------------------------------------
for entry in "${TESTS[@]}"; do
  name="${entry%%|*}"
  code="${entry#*|}"
  run_test "$name" "$code"
done
