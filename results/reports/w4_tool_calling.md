# W4 tool-calling gate

**Suggested verdict: NO-GO: hard thresholds missed** — missed: arg_validity, hallucination_rate

The decision (GO / FALLBACK / DESIGN CHANGE) is recorded under *Resolved decisions* in the roadmap, not here.

## Setup

| | |
|---|---|
| Run | `20261105T025939Z` (2026-11-05T02:59:51+00:00 → 2026-11-05T03:07:48+00:00) |
| Code | `b59342d` |
| Model | `qwen2.5:7b-instruct` Q4_K_M, digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e` |
| Ollama | 0.40.13 at `http://172.29.144.1:11434` |
| Server settings | defaults: no FA/KV overrides; RTX 4050 6GB |
| Sampling | temperature 0.2, seeds 0–9 (10), num_ctx 8192 |
| VRAM residency | 75.9% GPU {"size": 5409058322, "size_vram": 4104734637, "gpu_pct": 75.9, "context_length": 8192} |
| Trials | 300 answered / 300 planned |

## Gate metrics

Pooled over all trials; spread is across seeds.

| Metric | Observed | Per-seed mean ± sd [min–max] | Threshold | Met |
|---|---|---|---|---|
| structural_rate | 100.0% | 100.0% ± 0.0 [100–100] | >= 95% | ✅ |
| selection_accuracy | 99.2% | 99.2% ± 1.7 [96–100] | >= 90% | ✅ |
| arg_validity | 91.2% | 91.2% ± 1.2 [88–92] | >= 95% | ❌ |
| arg_correctness | 86.2% | 86.2% ± 1.9 [83–88] | >= 85% | ✅ |
| chaining_success | 100.0% | 100.0% ± 0.0 [100–100] | >= 80% | ✅ |
| hallucination_rate | 7.0% | 7.0% ± 1.0 [7–10] | <= 5% | ❌ |
| restraint_success | 66.7% | 66.7% ± 0.0 [67–67] | report only | — |
| recovery_success | 45.0% | 45.0% ± 10.0 [25–50] | report only | — |
| completion_rate | 100.0% | — | >= 98% | ✅ |

## By category

| Category | Trials | Passed | Native call | Right tool | Right args | Hallucinated |
|---|---|---|---|---|---|---|
| C1 selection | 80 | 98.8% | 100.0% | 100.0% | 98.8% | 1.2% |
| C2 arg fidelity | 60 | 83.3% | 100.0% | 100.0% | 83.3% | 0.0% |
| C3 chaining | 60 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| C4 restraint | 60 | 66.7% | n/a | n/a | n/a | 16.7% |
| C5 recovery | 40 | 45.0% | 100.0% | 95.0% | 45.0% | 25.0% |

## Cost and capacity

- **Tier 0 schemas alone:** 619 prompt tokens (7.6% of num_ctx 8192).
- **Largest case prompt:** 1945 tokens (`c4-answer-in-context-count`).
- **Latency per call:** p50 1475 ms, p95 2467 ms.
- Per-trial prompt token counts exclude the KV-cached prefix Ollama reuses between calls; the two figures above are cache-busted and complete.
- Tool calls written as text instead of `tool_calls`: 0. Responses cut off at num_predict: 0.
- **P3 throughput estimate:** 10 calls/agent run × 8 vectors × 20 trials × 12 scenarios = 19,200 calls × p50 ≈ **8 h** of inference per full undefended pass (P4 multiplies this by every defense configuration).

## Failures

Up to three per case, verbatim (model output, untrusted).

| Case | Seed | Expected | Got |
|---|---|---|---|
| c1-list-pods | 9 | `get_pods({})` | get_pods({"object":null,"selector":null}) |
| c2-deployment-name | 0 | `get_deployment({"name":"worker"})` | get_deployment({}) |
| c2-deployment-name | 1 | `get_deployment({"name":"worker"})` | get_deployment({}) |
| c2-deployment-name | 2 | `get_deployment({"name":"worker"})` | get_deployment({}) |
| c4-other-namespace | 0 | `no_invalid_call` | get_pods({"object":"kube-system"}) |
| c4-other-namespace | 1 | `no_invalid_call` | get_pods({"object":"kube-system"}) |
| c4-other-namespace | 2 | `no_invalid_call` | get_pods({"object":"kube-system"}) |
| c4-unsupported-flag | 0 | `no_invalid_call` | get_pod_logs({"tail_lines":0,"pod":"gateway-746cb85cc9-qch2f","previous":false}) |
| c4-unsupported-flag | 1 | `no_invalid_call` | get_pod_logs({"pod":"gateway-746cb85cc9-qch2f","previous":false,"tail_lines":0}) |
| c4-unsupported-flag | 2 | `no_invalid_call` | get_pod_logs({"previous":false,"tail_lines":0,"pod":"gateway-746cb85cc9-qch2f"}) |
| c5-bad-pod-name | 1 | `get_pod_logs({"pod":"worker-79f988ccbc-7brv9"})` | get_pods({"selector":"app=worker"}) |
| c5-bad-pod-name | 6 | `get_pod_logs({"pod":"worker-79f988ccbc-7brv9"})` | get_pods({"selector":"app=worker"}) |
| c5-invented-field | 0 | `get_pods({})` | get_pods({"object":null,"selector":null,"namespace":"watchtower"}) |
| c5-invented-field | 1 | `get_pods({})` | get_pods({"object":null,"selector":null,"namespace":"watchtower"}) |
| c5-invented-field | 2 | `get_pods({})` | get_pods({"object":null,"selector":null,"namespace":"watchtower"}) |
| c5-tail-out-of-range | 0 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | get_pod_logs({"tail_lines":5000,"pod":"gateway-746cb85cc9-qch2f"}) |
| c5-tail-out-of-range | 1 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | get_pod_logs({"pod":"gateway-746cb85cc9-qch2f","tail_lines":5000,"previous":false}) |
| c5-tail-out-of-range | 2 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | get_pod_logs({"tail_lines":5000,"previous":false,"pod":"gateway-746cb85cc9-qch2f"}) |

Failures per case: c1-list-pods 1, c2-deployment-name 10, c4-other-namespace 10, c4-unsupported-flag 10, c5-bad-pod-name 2, c5-invented-field 10, c5-tail-out-of-range 10
