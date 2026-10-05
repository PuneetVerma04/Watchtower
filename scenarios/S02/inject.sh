#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib.sh"

SERVICE=inventory
CONFIGMAP=inventory-config
BAD_VALUE=abc

symptom_visible() {
  kubectl get pods -n "$NAMESPACE" -l "app=$SERVICE" \
    -o jsonpath='{.items[*].status.containerStatuses[*].state.waiting.reason}' \
    | grep -Eq 'CrashLoopBackOff'
}

require_healthy

kubectl patch configmap "$CONFIGMAP" -n "$NAMESPACE" --type merge \
  -p "{\"data\":{\"LATENCY_MS\":\"$BAD_VALUE\"}}"

kubectl rollout restart "deployment/$SERVICE" -n "$NAMESPACE"

wait_for "CrashLoopBackOff on $SERVICE" 90 symptom_visible
