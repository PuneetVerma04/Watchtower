#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib.sh"

kubectl apply -f "$REPO_ROOT/cluster/manifests/worker-deployment.yaml"
kubectl rollout status deployment/worker -n "$NAMESPACE" --timeout=90s
