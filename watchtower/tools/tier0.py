"""Tier 0 (read-only) tools. Always allowed.

Shape of every tool:  (backend, args) -> ToolResult[<model>]
  1. args are already validated by the args model (registry.call_tool does that)
  2. ask the backend for raw output
  3. parse into typed models, tagging every field
  4. cap item counts / bytes and set `truncated`
  5. never raise to the agent: failures become a failed ToolResult with an UNTRUSTED error
"""

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from watchtower.tools import parsers
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
from watchtower.tools.backend import NAMESPACE, Backend, BackendError
from watchtower.tools.models import (
    ConfigMapList,
    DeploymentList,
    EventList,
    LogLines,
    MetricsResult,
    PodDescription,
    PodList,
    PodMetrics,
    ProbeResult,
    ServiceList,
)
from watchtower.tools.parsers import Budget
from watchtower.tools.provenance import ToolResult, fail, ok, trusted, untrusted

MAX_PODS = 50
MAX_EVENTS = 30
MAX_OBJECTS = 30  # deployments / configmaps / services
MAX_LINE_CHARS = 500
MAX_LOG_BYTES = 8000
MAX_BODY_CHARS = 2000
NOISE_CONFIGMAPS = {"kube-root-ca.crt"}  # injected into every namespace; certificate noise


def _items(raw: dict) -> list[dict]:
    """`kubectl get <kind>` returns a List; `kubectl get <kind> <name>` returns one object."""
    return raw["items"] if "items" in raw else [raw]


def _safe(
    tool: str, args: BaseModel, body: Callable[[Budget], tuple[Any, bool]]
) -> ToolResult[Any]:
    """Run a tool body; turn every expected failure into a failed ToolResult.

    `body` returns (data, capped) where `capped` says an item/byte cap was hit.
    """
    budget = Budget()
    arg_dict = args.model_dump(exclude_none=True)
    try:
        data, capped = body(budget)
    except BackendError as e:
        return fail(tool, arg_dict, str(e))
    except (KeyError, TypeError, ValueError, IndexError) as e:
        # ValueError covers pydantic.ValidationError. The message is ours, not cluster text.
        return fail(tool, arg_dict, f"unexpected response shape ({type(e).__name__})")
    return ok(tool, arg_dict, data, truncated=budget.truncated or capped)


def _cap[T](items: list[T], limit: int) -> tuple[list[T], bool]:
    return items[:limit], len(items) > limit


# --------------------------------------------------------------------------- pods


def get_pods(backend: Backend, args: GetPodsArgs) -> ToolResult[PodList]:
    """List pods with the status fields needed to diagnose S01-S03."""

    def body(budget: Budget):
        pods = [
            parsers.parse_pod(p, budget)
            for p in _items(backend.get_json("pods", args.name, args.selector))
        ]
        pods, capped = _cap(pods, MAX_PODS)
        return PodList(pods=pods), capped

    return _safe("get_pods", args, body)


def describe_pod(backend: Backend, args: DescribePodArgs) -> ToolResult[PodDescription]:
    """Spec-level detail for one pod: labels, annotations, env, resources, conditions."""

    def body(budget: Budget):
        return parsers.parse_pod_description(backend.get_json("pods", args.pod), budget), False

    return _safe("describe_pod", args, body)


def get_pod_logs(backend: Backend, args: GetPodLogsArgs) -> ToolResult[LogLines]:
    """Recent log lines (every line is UNTRUSTED)."""

    def body(budget: Budget):
        text = backend.logs(args.pod, args.container, args.previous, args.tail_lines)
        lines = text.splitlines()[-args.tail_lines :]  # replay may hand back more than asked
        lines = [budget.clip(line, MAX_LINE_CHARS) for line in lines]

        # Keep the newest lines that fit the byte budget.
        kept: list[str] = []
        size = 0
        for line in reversed(lines):
            size += len(line.encode()) + 1
            if size > MAX_LOG_BYTES:
                budget.truncated = True
                break
            kept.append(line)
        kept.reverse()
        return LogLines(lines=untrusted(kept), previous=args.previous), False

    return _safe("get_pod_logs", args, body)


# --------------------------------------------------------------------------- events


def get_events(backend: Backend, args: GetEventsArgs) -> ToolResult[EventList]:
    """Recent namespace events, newest first. The only place S03's pull failure message appears."""

    def body(budget: Budget):
        events = [parsers.parse_event(e, budget) for e in _items(backend.get_json("events"))]
        if args.involved_object is not None:
            events = [
                e for e in events if e.object_name and e.object_name.value == args.involved_object
            ]
        events.sort(key=parsers.event_sort_key, reverse=True)
        events, capped = _cap(events, MAX_EVENTS)
        return EventList(events=events), capped

    return _safe("get_events", args, body)


# --------------------------------------------------------------------------- workloads/config


def get_deployment(backend: Backend, args: GetDeploymentArgs) -> ToolResult[DeploymentList]:
    """Deployments: replica status, container images, env, resource limits, conditions."""

    def body(budget: Budget):
        items = [
            parsers.parse_deployment(d, budget)
            for d in _items(backend.get_json("deployments", args.name))
        ]
        items, capped = _cap(items, MAX_OBJECTS)
        return DeploymentList(deployments=items), capped

    return _safe("get_deployment", args, body)


def get_configmap(backend: Backend, args: GetConfigMapArgs) -> ToolResult[ConfigMapList]:
    """ConfigMaps (keys and values are UNTRUSTED). S02's bad LATENCY_MS lives here."""

    def body(budget: Budget):
        raw = _items(backend.get_json("configmaps", args.name))
        if args.name is None:
            raw = [c for c in raw if c["metadata"]["name"] not in NOISE_CONFIGMAPS]
        items = [parsers.parse_configmap(c, budget) for c in raw]
        items, capped = _cap(items, MAX_OBJECTS)
        return ConfigMapList(configmaps=items), capped

    return _safe("get_configmap", args, body)


def get_service(backend: Backend, args: GetServiceArgs) -> ToolResult[ServiceList]:
    def body(budget: Budget):
        items = [
            parsers.parse_service(s, budget)
            for s in _items(backend.get_json("services", args.name))
        ]
        items, capped = _cap(items, MAX_OBJECTS)
        return ServiceList(services=items), capped

    return _safe("get_service", args, body)


# --------------------------------------------------------------------------- probes/metrics


def http_probe(backend: Backend, args: HttpProbeArgs) -> ToolResult[ProbeResult]:
    """GET a path on a service through the API-server service proxy (read-only).

    Only 2xx responses come back as data; anything else is a failed ToolResult.
    """

    def body(budget: Budget):
        path = (
            f"/api/v1/namespaces/{NAMESPACE}/services/{args.service}:{args.port}/proxy{args.path}"
        )
        started = time.perf_counter()
        text = backend.raw_get(path)
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = ProbeResult(
            status_code=trusted(200),  # the proxy only returns the body for 2xx
            latency_ms=trusted(latency_ms),
            body=untrusted(budget.clip(text, MAX_BODY_CHARS)),
        )
        return result, False

    return _safe("http_probe", args, body)


def get_metrics(backend: Backend, args: GetMetricsArgs) -> ToolResult[MetricsResult]:
    """Pod CPU/memory usage, if metrics-server is installed (it is not in this cluster)."""

    def body(budget: Budget):
        path = f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods"
        try:
            raw = json.loads(backend.raw_get(path))
        except (BackendError, json.JSONDecodeError):
            return MetricsResult(available=False, note="metrics API not available"), False
        pods = []
        for item in raw.get("items", []):
            usage = (item.get("containers") or [{}])[0].get("usage") or {}
            pods.append(
                PodMetrics(
                    name=untrusted(item["metadata"]["name"]),
                    cpu=trusted(str(usage.get("cpu", ""))),
                    memory=trusted(str(usage.get("memory", ""))),
                )
            )
        pods, capped = _cap(pods, MAX_PODS)
        return MetricsResult(available=True, pods=pods), capped

    return _safe("get_metrics", args, body)
