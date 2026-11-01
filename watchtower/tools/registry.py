"""Tool registry: name -> (tier, args model, function), plus the schemas shown to the LLM.

The tier is the security boundary (Architecture §3). Only Tier 0 exists so far; Tier 1 and the
Tier 2 honeypot are registered in W6.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from watchtower.tools import tier0
from watchtower.tools.args import (
    DescribePodArgs,
    GetConfigMapArgs,
    GetDeploymentArgs,
    GetEventsArgs,
    GetMetricsArgs,
    GetPodLogsArgs,
    GetPodsArgs,
    GetServiceArgs,
    HttpProbeArgs,
)
from watchtower.tools.backend import Backend
from watchtower.tools.provenance import ToolResult, fail


class Tier(IntEnum):
    READ_ONLY = 0
    MUTATING = 1
    DESTRUCTIVE = 2


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Backend, Any], ToolResult[Any]]


def _spec(name: str, description: str, args_model: type[BaseModel], fn) -> ToolSpec:
    return ToolSpec(name, Tier.READ_ONLY, description, args_model, fn)


TOOLS: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        _spec(
            "get_pods",
            "List pods with phase, readiness, restarts and container state.",
            GetPodsArgs,
            tier0.get_pods,
        ),
        _spec(
            "describe_pod",
            "Labels, annotations, env, resource limits and conditions of one pod.",
            DescribePodArgs,
            tier0.describe_pod,
        ),
        _spec(
            "get_pod_logs",
            "Recent log lines of a pod; previous=true for the crashed container.",
            GetPodLogsArgs,
            tier0.get_pod_logs,
        ),
        _spec(
            "get_events", "Recent namespace events, newest first.", GetEventsArgs, tier0.get_events
        ),
        _spec(
            "get_deployment",
            "Deployments: replicas, images, env, resource limits, conditions.",
            GetDeploymentArgs,
            tier0.get_deployment,
        ),
        _spec("get_configmap", "ConfigMap contents.", GetConfigMapArgs, tier0.get_configmap),
        _spec("get_service", "Services: type, ports, selector.", GetServiceArgs, tier0.get_service),
        _spec(
            "http_probe",
            "HTTP GET a path on a service and return the status and body.",
            HttpProbeArgs,
            tier0.http_probe,
        ),
        _spec(
            "get_metrics",
            "Pod CPU and memory usage, if the metrics API is available.",
            GetMetricsArgs,
            tier0.get_metrics,
        ),
    ]
}


def call_tool(backend: Backend, name: str, raw_args: dict[str, Any] | None) -> ToolResult[Any]:
    """Validate the model-supplied arguments, run the tool, and never raise."""
    spec = TOOLS.get(name)
    if spec is None:
        return fail(name[:64], {}, "unknown tool")  # the name came from the model: keep it short
    try:
        args = spec.args_model.model_validate(raw_args or {})
    except ValidationError as e:
        # Report which fields failed, not the offending values (they may be hostile text).
        fields = sorted({".".join(str(p) for p in err["loc"]) or "<root>" for err in e.errors()})
        return fail(name, {}, f"invalid arguments: {', '.join(fields)}")
    return spec.fn(backend, args)


def tool_schemas(max_tier: Tier = Tier.READ_ONLY) -> list[dict[str, Any]]:
    """Tool definitions in the shape Ollama's chat API expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.args_model.model_json_schema(),
            },
        }
        for s in TOOLS.values()
        if s.tier <= max_tier
    ]
