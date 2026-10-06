#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib.sh"

SERVICE=worker
LEAK_LIMIT=96Mi
LEAK_REQUEST=64Mi
MAX_JOBS=120
BATCH=5
# Not 8000, so a leftover port-forward from `make verify` cannot collide with this one.
GATEWAY_PORT=8100

symptom_visible() {
  kubectl get pods -n "$NAMESPACE" -l "app=$SERVICE" \
    -o jsonpath='{.items[*].status.containerStatuses[*].lastState.terminated.reason}' \
    | grep -Eq 'OOMKilled'
}

post_order() {
  curl -sf -o /dev/null -X POST "http://localhost:$GATEWAY_PORT/orders" \
    -H "Content-Type: application/json" \
    -d '{"item": "s01-load", "quantity": 1}'
}

require_healthy

# A pod from a previous injection keeps its OOMKilled state, which would make the loop below pass instantly.
if symptom_visible; then
  echo "S01 already appears to be injected; run 'make scenario-restore ID=S01' first." >&2
  exit 1
fi

# Turn the leak on and shrink the memory limit in one patch, so there is a single rollout.
# The request must not exceed the limit, so both are set.
kubectl patch deployment "$SERVICE" -n "$NAMESPACE" -p "{
  \"spec\": {\"template\": {\"spec\": {\"containers\": [{
    \"name\": \"$SERVICE\",
    \"env\": [{\"name\": \"MEMORY_LEAK_ENABLED\", \"value\": \"true\"}],
    \"resources\": {
      \"requests\": {\"memory\": \"$LEAK_REQUEST\"},
      \"limits\": {\"memory\": \"$LEAK_LIMIT\"}
    }
  }]}}}
}"
kubectl rollout status "deployment/$SERVICE" -n "$NAMESPACE" --timeout=120s

# Drive throughput through the real order path; the leak grows per processed job.
kubectl port-forward svc/gateway "$GATEWAY_PORT:8000" -n "$NAMESPACE" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
wait_for "gateway reachable on port $GATEWAY_PORT" 20 \
  curl -sf -o /dev/null "http://localhost:$GATEWAY_PORT/healthz"

posted=0
until symptom_visible; do
  if ((posted >= MAX_JOBS)); then
    echo "No OOMKilled after $posted jobs; the memory limit ($LEAK_LIMIT) may be too high." >&2
    exit 1
  fi
  for _ in $(seq "$BATCH"); do
    post_order
  done
  posted=$((posted + BATCH))
  sleep 1 # let the worker process the batch before checking
done

echo "Observed: OOMKilled on $SERVICE after about $posted jobs"
