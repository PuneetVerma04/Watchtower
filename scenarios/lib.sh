NAMESPACE=watchtower
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Ground truth is only valid if the cluster was healthy before injection.
require_healthy() {
  if ! kubectl wait --for=condition=Ready pod --all -n "$NAMESPACE" --timeout=10s >/dev/null 2>&1; then
    echo "Cluster is not healthy; refusing to inject." >&2
    kubectl get pods -n "$NAMESPACE" >&2
    exit 1
  fi
}

# wait_for <description> <timeout-seconds> <command...>
# Polls until the command succeeds, so injection returns only once the symptom is observable.
wait_for() {
  local description=$1 timeout=$2
  shift 2
  local deadline=$((SECONDS + timeout))
  until "$@"; do
    if ((SECONDS >= deadline)); then
      echo "Timed out waiting for: $description" >&2
      return 1
    fi
    sleep 2
  done
  echo "Observed: $description"
}
