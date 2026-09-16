# Watchtower

An autonomous Kubernetes incident-response agent, plus an adversarial evaluation harness that measures how reliably the agent can be hijacked through the telemetry it reads, and how much of that risk defenses actually remove.

> **Status:** Planning. No code yet. Build starts with Phase 0 (substrate).

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

## Roadmap

| Phase | Focus |
|---|---|
| P0 | Substrate: cluster, fragile app, first failure scenarios |
| P1 | Baseline agent and read-only tools |
| P2 | Evaluation harness and baseline accuracy |
| P3 | Red team: attack suite and measurement |
| P4 | Defenses and before/after measurement |
| P5 | Multi-model comparison and published findings |

The evaluation baseline (P2) ships before any red-teaming begins.

## Setup

Not available yet. Setup instructions will be added once Phase 0 is complete.

## Ethics

All work runs on local, self-owned infrastructure with synthetic data. Payloads target the agent's reasoning, not real CVEs. Defenses are published alongside attacks. Any vulnerability found in a third-party framework is reported privately to its maintainers first.
