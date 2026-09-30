# Watchtower

An autonomous Kubernetes incident-response agent, plus an adversarial evaluation harness that measures how reliably the agent can be hijacked through the telemetry it reads, and how much of that risk defenses actually remove.

> **Status:** Phase 0 (substrate) in progress. The cluster and the four-service application run locally; the first failure scenarios and everything after them (agent, evaluation, attacks, defenses) are not built yet.

---

## Why

An incident-response agent reads logs, events, and metadata produced by the systems it monitors. Anyone who can write a log line, name a container, or control an HTTP response can write into the agent's context window. That makes indirect prompt injection a structural property of the system, not a contrived demo.

Watchtower builds such an agent, attacks it, defends it, and **measures** each step.

## What it produces

A before/after results table that reports, for each attack vector:

- how often the attack succeeds against an undefended agent,
- how much each defense reduces that,
- how much root-cause accuracy is lost in exchange.

## Components

| Layer | What it is |
|---|---|
| Substrate | Local `kind` cluster running small, deliberately fragile FastAPI services, plus Postgres and Redis, with scripted failure scenarios that each have a recorded ground truth |
| Tool layer | Tiered tools: read-only, mutating (approval-gated), and destructive (honeypot, intercepted and never executed) |
| Agent | LangGraph state graph: triage → investigate ⇄ verify → hypothesise → propose |
| Eval harness | Root-cause accuracy, remediation precision, steps, token cost, false-confidence rate |
| Adversarial harness | Attack vectors such as log-borne injection, induced destructive actions, and alert suppression |
| Defenses | Guardrails, each independently toggleable and independently measured |

## Tech stack

- **Cluster:** Kubernetes via `kind`
- **Services:** Python, FastAPI, Postgres, Redis
- **Agent:** LangGraph
- **Models:** local `qwen2.5:7b` via Ollama for development and red-teaming; a larger open-weights cloud model for the comparative evaluation
- **Results:** SQLite

## The substrate (built so far)

A local `kind` cluster (one control-plane node, two workers) running everything in a `watchtower` namespace:

| Service | Role | Deliberate fragility |
|---|---|---|
| `gateway` | Entry point; forwards order requests to `orders` | No timeout on its call to `orders` |
| `orders` | Business logic; Postgres for storage, in-memory cache, Redis job producer | Small connection pool, unbounded in-memory cache |
| `inventory` | Slow-dependency simulator, called by `orders` | Configurable artificial latency (`LATENCY_MS`) |
| `worker` | Background consumer of the Redis job queue; marks orders fulfilled via `orders` | Toggleable memory leak (`MEMORY_LEAK_ENABLED`), unbounded queue |
| `postgres` | Order storage (StatefulSet) | — |
| `redis` | Job queue (StatefulSet) | — |

Each service is small on purpose. They exist to break in known, recorded ways, not to do anything useful.

```
client → gateway → orders → inventory
                      │
                      ├──→ postgres
                      └──→ redis queue → worker ──(status update)──→ orders
```

Not built yet: the failure-injection scenarios (each a script plus a recorded ground truth), and every layer above the substrate. See the threat model in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for the attack and defense design.

## Roadmap

| Phase | Focus | Status |
|---|---|---|
| P0 | Substrate: cluster, fragile app, first failure scenarios | In progress — cluster and services built, scenarios pending |
| P1 | Baseline agent and read-only tools | Not started |
| P2 | Evaluation harness and baseline accuracy | Not started |
| P3 | Red team: attack suite and measurement | Not started |
| P4 | Defenses and before/after measurement | Not started |
| P5 | Multi-model comparison and published findings | Not started |

The evaluation baseline (P2) ships before any red-teaming begins.

## Setup

Requires Docker, [`kind`](https://kind.sigs.k8s.io/), `kubectl`, `make`, and [`uv`](https://docs.astral.sh/uv/) (Python 3.12). Tested on Linux via WSL2.

```bash
cp .env.example .env     # Postgres credentials for the local cluster; dev-only values
make cluster-up          # create the kind cluster
make redeploy            # build images, load them into kind, deploy, run the smoke test
```

`make verify` (run as part of `redeploy`) checks service health, creates an order through the gateway, and confirms the order reaches `fulfilled` status once the worker has processed it.

Other targets:

| Target | Does |
|---|---|
| `make build` / `make load` | Build the service images / build and load them into the cluster |
| `make deploy` | Apply all manifests and wait for pods to become ready |
| `make cluster-down` | Delete the cluster (Postgres data is deleted with it) |
| `make lint` / `make fmt` | Run `ruff` check / format |

## Ethics

All work runs on local, self-owned infrastructure with synthetic data. Payloads target the agent's reasoning, not real CVEs. Defenses are published alongside attacks. Any vulnerability found in a third-party framework is reported privately to its maintainers first.
