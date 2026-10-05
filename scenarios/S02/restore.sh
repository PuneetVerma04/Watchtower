#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../lib.sh"

kubectl apply -f "$REPO_ROOT/cluster/manifests/inventory-configmap.yaml"
kubectl rollout restart deployment/inventory -n "$NAMESPACE"
kubectl rollout status deployment/inventory -n "$NAMESPACE" --timeout=90s
