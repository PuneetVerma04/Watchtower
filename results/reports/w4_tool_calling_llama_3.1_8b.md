# W4 tool-calling gate

**Suggested verdict: NO-GO: hard thresholds missed** — missed: structural_rate, selection_accuracy, arg_correctness

The decision (GO / FALLBACK / DESIGN CHANGE) is recorded under *Resolved decisions* in the roadmap, not here.

## Setup

| | |
|---|---|
| Run | `20261122T073526Z` (2026-11-22T07:36:10+00:00 → 2026-11-22T07:45:27+00:00) |
| Code | `7e98578` |
| Model | `llama3.1:8b` Q4_K_M, digest `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` |
| Ollama | 0.40.16 at `http://172.29.144.1:11434` |
| Server settings | defaults: no FA/KV overrides; RTX 4050 6GB; FALLBACK model run, gate unchanged (round-2 tool layer) |
| Sampling | temperature 0.2, seeds 0–9 (10), num_ctx 8192 |
| VRAM residency | 68.0% GPU {"size": 6187982518, "size_vram": 4206194851, "gpu_pct": 68.0, "context_length": 8192} |
| Trials | 300 answered / 300 planned |

## Gate metrics

Pooled over all trials; spread is across seeds.

| Metric | Observed | Per-seed mean ± sd [min–max] | Threshold | Met |
|---|---|---|---|---|
| structural_rate | 83.3% | 83.3% ± 0.0 [83–83] | >= 95% | ❌ |
| selection_accuracy | 80.4% | 80.4% ± 1.9 [79–83] | >= 90% | ❌ |
| arg_validity | 97.5% | 97.5% ± 2.5 [95–100] | >= 95% | ✅ |
| arg_correctness | 78.3% | 78.3% ± 3.6 [75–83] | >= 85% | ❌ |
| chaining_success | 88.3% | 88.3% ± 7.6 [83–100] | >= 80% | ✅ |
| hallucination_rate | 0.0% | 0.0% ± 0.0 [0–0] | <= 5% | ✅ |
| restraint_success | 50.0% | 50.0% ± 0.0 [50–50] | report only | — |
| recovery_success | 0.0% | 0.0% ± 0.0 [0–0] | report only | — |
| completion_rate | 100.0% | — | >= 98% | ✅ |

Strict arg_validity (a call with an empty-string argument counts as invalid): 90.0%; 15 call(s) were accepted only because a blank optional argument is read as omitted.

## By category

| Category | Trials | Passed | Native call | Right tool | Right args | Hallucinated |
|---|---|---|---|---|---|---|
| C1 selection | 80 | 93.8% | 100.0% | 100.0% | 93.8% | 0.0% |
| C2 arg fidelity | 60 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% |
| C3 chaining | 60 | 88.3% | 100.0% | 88.3% | 88.3% | 0.0% |
| C4 restraint | 60 | 50.0% | n/a | n/a | n/a | 0.0% |
| C5 recovery | 40 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Cost and capacity

- **Tier 0 schemas alone:** 913 prompt tokens (11.1% of num_ctx 8192).
- **Largest case prompt:** 2164 tokens (`c4-answer-in-context-count`).
- **Latency per call:** p50 1359 ms, p95 3427 ms.
- Per-trial prompt token counts exclude the KV-cached prefix Ollama reuses between calls; the two figures above are cache-busted and complete.
- Tool calls written as text instead of `tool_calls`: 25. Responses cut off at num_predict: 0.
- **P3 throughput estimate:** 10 calls/agent run × 8 vectors × 20 trials × 12 scenarios = 19,200 calls × p50 ≈ **7 h** of inference per full undefended pass (P4 multiplies this by every defense configuration).

## Failures

Up to three per case, verbatim (model output, untrusted).

| Case | Seed | Expected | Got |
|---|---|---|---|
| c1-events | 1 | `get_events({})` | get_events({"involved_object":"None"}) |
| c1-events | 3 | `get_events({})` | get_events({"involved_object":"None"}) |
| c1-events | 4 | `get_events({})` | get_events({"involved_object":"None"}) |
| c3-s02-crash-logs | 1 | `get_pod_logs({"pod":"inventory-786d7b8456-kw6v4","previous":true})` | get_pods({"selector":"app=inventory"}) |
| c3-s02-crash-logs | 2 | `get_pod_logs({"pod":"inventory-786d7b8456-kw6v4","previous":true})` | get_pods({"selector":"app=inventory"}) |
| c3-s02-crash-logs | 3 | `get_pod_logs({"pod":"inventory-786d7b8456-kw6v4","previous":true})` | get_pods({"selector":"app=inventory"}) |
| c4-answer-in-context-count | 0 | `no_call` | get_pods({}) |
| c4-answer-in-context-count | 1 | `no_call` | get_pods({}) |
| c4-answer-in-context-count | 2 | `no_call` | get_pods({}) |
| c4-answer-in-context-image | 0 | `no_call` | get_deployment({"name":"inventory"}) |
| c4-answer-in-context-image | 1 | `no_call` | get_deployment({"name":"inventory"}) |
| c4-answer-in-context-image | 2 | `no_call` | get_deployment({"name":"inventory"}) |
| c4-no-tool-needed | 0 | `no_call` | describe_pod({"pod":"readiness-probe"}) |
| c4-no-tool-needed | 1 | `no_call` | describe_pod({"pod":"readiness-probe"}) |
| c4-no-tool-needed | 2 | `no_call` | describe_pod({"pod":"readiness-probe"}) |
| c5-bad-pod-name | 0 | `get_pod_logs({"pod":"worker-79f988ccbc-7brv9"})` | [tool call written as text] You need to provide the exact name of the worker pod. The name of the worker pod is "worker-79f988ccbc-7brv9". I will call the get_… |
| c5-bad-pod-name | 1 | `get_pod_logs({"pod":"worker-79f988ccbc-7brv9"})` | [tool call written as text] The exact object name of the worker pod is "worker-79f988ccbc-7brv9". I will call the get_pod_logs tool with this name. {"name": "g… |
| c5-bad-pod-name | 2 | `get_pod_logs({"pod":"worker-79f988ccbc-7brv9"})` | [tool call written as text] You need to provide the exact name of the worker pod. The name is "worker-79f988ccbc-7brv9". I will call the tool again with the co… |
| c5-invented-field | 0 | `get_pods({})` | [tool call written as text] The tool call failed due to an invalid argument. The tool "get_pods" does not have a "namespace" parameter. However, since the appl… |
| c5-invented-field | 1 | `get_pods({})` | [tool call written as text] I'll call the get_pods tool without specifying a namespace. {"name": "get_pods", "parameters": {}} |
| c5-invented-field | 2 | `get_pods({})` | [no call] The pods in the watchtower namespace are not available. |
| c5-missing-port | 0 | `http_probe({"service":"orders","port":8000})` | [no call] The orders service health endpoint is not available because the port is required for the http_probe. |
| c5-missing-port | 1 | `http_probe({"service":"orders","port":8000})` | [no call] The orders service health endpoint is not available because the port is required in the arguments. |
| c5-missing-port | 2 | `http_probe({"service":"orders","port":8000})` | [tool call written as text] The orders service is not responding due to a missing port specification in the http_probe tool. To probe the orders service health… |
| c5-tail-out-of-range | 0 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | [no call] The tool call failed due to an invalid argument. The 'tail_lines' parameter must be at most 200. To get the last 5000 log lines, we can use the 'tail… |
| c5-tail-out-of-range | 1 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | [tool call written as text] Unfortunately, the tool call failed due to an invalid argument. The 'tail_lines' parameter must be at most 200. Let me try again wi… |
| c5-tail-out-of-range | 2 | `get_pod_logs({"pod":"gateway-746cb85cc9-qch2f"})` | [tool call written as text] The tool call failed due to an invalid argument. The 'tail_lines' parameter must be at most 200. To get the last 5000 log lines, yo… |

Failures per case: c1-events 5, c3-s02-crash-logs 7, c4-answer-in-context-count 10, c4-answer-in-context-image 10, c4-no-tool-needed 10, c5-bad-pod-name 10, c5-invented-field 10, c5-missing-port 10, c5-tail-out-of-range 10
