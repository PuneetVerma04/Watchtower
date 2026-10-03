#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib.sh"

SERVICE=inventory
BAD_IMAGE=inventory:v2

symptom_visible() {
  kubectl get pods -n "$NAMESPACE" -l "app=$SERVICE" \
    -o jsonpath='{.items[*].status.containerStatuses[*].state.waiting.reason}' \
    | grep -Eq 'ErrImagePull|ImagePullBackOff'
}

require_healthy

kubectl set image "deployment/$SERVICE" "$SERVICE=$BAD_IMAGE" -n "$NAMESPACE"

wait_for "ImagePullBackOff on $SERVICE" 90 symptom_visible
