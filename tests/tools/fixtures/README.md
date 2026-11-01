# Fixtures

Captured telemetry for four cluster states: `healthy`, `S01` (OOMKilled), `S02` (inventory
crash on a bad ConfigMap value), `S03` (ImagePullBackOff). Each `*.json` is a capture set: the
raw `kubectl` output behind a standard battery of Tier 0 tool calls, plus the live tool results
(`op: "battery"`) the replay must reproduce. Tests replay them, so no cluster is needed.

Regenerate a fixture (WSL, cluster up, from the repo root). Let the cluster settle between
scenarios: `scenarios/lib.sh:require_healthy` counts still-terminating pods as unhealthy and
will refuse to inject right after a `scenario-restore`.

```bash
export VIRTUAL_ENV=~/.venvs/watchtower
CAP="uv run --active python -m watchtower.tools.capture"

make scenario ID=S01                 # inject (skip for the healthy set)
$CAP capture --set S01               # capture live, replay it, fail if replay differs
$CAP export  --set S01 --out tests/tools/fixtures/S01.json
make scenario-restore ID=S01
```

`capture` writes to `results/captures.sqlite` (gitignored). `verify --set S01 --json
tests/tools/fixtures/S01.json` replays a fixture offline and compares it with the stored live
results.

S02 note: on this cluster (Kubernetes v1.37) the crashing pod shows `Error` with a rising
restart count and never reports `CrashLoopBackOff`, so `scenarios/S02/inject.sh` times out
waiting for that string. The symptom is present; the fixture captures it.

Fixtures are test data. They contain no credentials (pod specs reference the Postgres secret by
name only); node and pod IPs are cluster-local.
