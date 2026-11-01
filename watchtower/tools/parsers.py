"""Raw kubectl JSON -> typed, provenance-tagged models.

Pure functions: no I/O, so they run identically on live and replayed data.
Every parser takes a Budget; clipping free text through it records that something was cut, and
the tool then sets `truncated` on its ToolResult.
"""

from datetime import UTC, datetime
from typing import Any

from watchtower.tools.models import (
    Condition,
    ConfigMapInfo,
    ContainerInfo,
    ContainerSpec,
    ContainerState,
    DeploymentInfo,
    EnvVar,
    EventInfo,
    PodDescription,
    PodInfo,
    ServiceInfo,
    ServicePort,
)
from watchtower.tools.provenance import trusted, untrusted

MAX_VALUE_CHARS = 500  # any single free-text value
MAX_MESSAGE_CHARS = 300  # event / condition / state messages
MAX_MAP_ENTRIES = 50  # labels, annotations, configmap keys


class Budget:
    """Clips free text and remembers whether anything was cut."""

    def __init__(self) -> None:
        self.truncated = False

    def clip(self, text: str, limit: int = MAX_VALUE_CHARS) -> str:
        text = str(text)
        if len(text) <= limit:
            return text
        self.truncated = True
        return text[:limit] + "...[truncated]"

    def clip_map(self, mapping: dict[str, Any] | None, value_limit: int = MAX_VALUE_CHARS) -> dict:
        items = sorted((mapping or {}).items())
        if len(items) > MAX_MAP_ENTRIES:
            self.truncated = True
            items = items[:MAX_MAP_ENTRIES]
        return {self.clip(k, 100): self.clip(v, value_limit) for k, v in items}


def _first_key(mapping: dict) -> str | None:
    return next(iter(mapping), None)


# --------------------------------------------------------------------------- pods


def parse_container_status(cs: dict, budget: Budget) -> ContainerInfo:
    state = cs.get("state") or {}
    kind = _first_key(state)
    body = (state.get(kind) or {}) if kind else {}
    try:
        container_state = ContainerState(kind)
    except ValueError:
        container_state = ContainerState.UNKNOWN
    last = (cs.get("lastState") or {}).get("terminated") or {}

    return ContainerInfo(
        name=untrusted(cs["name"]),
        image=untrusted(cs.get("image", "")),
        ready=trusted(bool(cs.get("ready", False))),
        restart_count=trusted(int(cs.get("restartCount", 0))),
        state=trusted(container_state),
        state_reason=trusted(body["reason"]) if "reason" in body else None,
        exit_code=trusted(int(body["exitCode"])) if "exitCode" in body else None,
        last_terminated_reason=trusted(last["reason"]) if "reason" in last else None,
        last_terminated_exit_code=(trusted(int(last["exitCode"])) if "exitCode" in last else None),
        state_message=(
            untrusted(budget.clip(body["message"], MAX_MESSAGE_CHARS))
            if "message" in body
            else None
        ),
    )


def parse_conditions(conditions: list[dict] | None, budget: Budget) -> list[Condition]:
    out = []
    for c in conditions or []:
        out.append(
            Condition(
                type=trusted(c.get("type", "")),
                status=trusted(c.get("status", "")),
                reason=trusted(c["reason"]) if c.get("reason") else None,
                message=(
                    untrusted(budget.clip(c["message"], MAX_MESSAGE_CHARS))
                    if c.get("message")
                    else None
                ),
            )
        )
    return out


def parse_pod(pod: dict, budget: Budget) -> PodInfo:
    meta, spec, status = pod["metadata"], pod.get("spec") or {}, pod.get("status") or {}
    ready = any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in status.get("conditions") or []
    )
    node = spec.get("nodeName")
    return PodInfo(
        name=untrusted(meta["name"]),
        node=untrusted(node) if node else None,
        phase=trusted(status.get("phase", "Unknown")),
        ready=trusted(ready),
        created_at=trusted(meta["creationTimestamp"]),
        containers=[
            parse_container_status(cs, budget) for cs in status.get("containerStatuses") or []
        ],
    )


# --------------------------------------------------------------------------- specs


def _env_var(env: dict, budget: Budget) -> EnvVar:
    ref = None
    if "valueFrom" in env:
        source = env["valueFrom"] or {}
        kind = _first_key(source) or "unknown"
        target = source.get(kind) or {}
        if kind == "fieldRef":
            ref = f"fieldRef:{target.get('fieldPath', '')}"
        else:  # configMapKeyRef / secretKeyRef: name + key only, never a secret's value
            ref = f"{kind}:{target.get('name', '')}/{target.get('key', '')}"
    return EnvVar(
        name=untrusted(env.get("name", "")),
        value=untrusted(budget.clip(env["value"])) if "value" in env else None,
        value_from=untrusted(budget.clip(ref, 200)) if ref else None,
    )


def parse_container_spec(c: dict, budget: Budget) -> ContainerSpec:
    resources = c.get("resources") or {}
    limits, requests = resources.get("limits") or {}, resources.get("requests") or {}

    def quantity(mapping: dict, key: str):
        return trusted(str(mapping[key])) if key in mapping else None

    return ContainerSpec(
        name=untrusted(c.get("name", "")),
        image=untrusted(c.get("image", "")),
        env=[_env_var(e, budget) for e in c.get("env") or []],
        cpu_request=quantity(requests, "cpu"),
        memory_request=quantity(requests, "memory"),
        cpu_limit=quantity(limits, "cpu"),
        memory_limit=quantity(limits, "memory"),
    )


def parse_pod_description(pod: dict, budget: Budget) -> PodDescription:
    meta = pod["metadata"]
    owners = meta.get("ownerReferences") or []
    owner = f"{owners[0].get('kind', '')}/{owners[0].get('name', '')}" if owners else None
    return PodDescription(
        pod=parse_pod(pod, budget),
        owner=untrusted(owner) if owner else None,
        labels=untrusted(budget.clip_map(meta.get("labels"))),
        annotations=untrusted(budget.clip_map(meta.get("annotations"))),
        containers=[
            parse_container_spec(c, budget) for c in (pod.get("spec") or {}).get("containers") or []
        ],
        conditions=parse_conditions((pod.get("status") or {}).get("conditions"), budget),
    )


# --------------------------------------------------------------------------- others


def parse_event(ev: dict, budget: Budget) -> EventInfo:
    obj = ev.get("involvedObject") or {}
    seen = (
        ev.get("lastTimestamp")
        or ev.get("eventTime")
        or ev.get("firstTimestamp")
        or (ev.get("metadata") or {}).get("creationTimestamp")
    )
    count = ev.get("count") or (ev.get("series") or {}).get("count") or 1
    return EventInfo(
        type=trusted(ev.get("type", "")),
        reason=trusted(ev.get("reason", "")),
        count=trusted(int(count)),
        last_seen=trusted(seen) if seen else None,
        object_kind=trusted(obj["kind"]) if obj.get("kind") else None,
        object_name=untrusted(obj["name"]) if obj.get("name") else None,
        message=untrusted(budget.clip(ev.get("message", ""), MAX_MESSAGE_CHARS)),
    )


def event_sort_key(ev: EventInfo) -> datetime:
    # Timestamps parse as timezone-aware (they end in 'Z'), so the fallback must be aware too.
    return ev.last_seen.value if ev.last_seen else datetime.min.replace(tzinfo=UTC)


def parse_deployment(d: dict, budget: Budget) -> DeploymentInfo:
    spec, status = d.get("spec") or {}, d.get("status") or {}
    template_spec = (spec.get("template") or {}).get("spec") or {}
    return DeploymentInfo(
        name=untrusted(d["metadata"]["name"]),
        replicas_desired=trusted(int(spec.get("replicas", 1))),
        replicas_total=trusted(int(status.get("replicas", 0))),
        replicas_ready=trusted(int(status.get("readyReplicas", 0))),
        replicas_available=trusted(int(status.get("availableReplicas", 0))),
        replicas_updated=trusted(int(status.get("updatedReplicas", 0))),
        replicas_unavailable=trusted(int(status.get("unavailableReplicas", 0))),
        selector=untrusted(budget.clip_map((spec.get("selector") or {}).get("matchLabels"))),
        containers=[parse_container_spec(c, budget) for c in template_spec.get("containers") or []],
        conditions=parse_conditions(status.get("conditions"), budget),
    )


def parse_configmap(cm: dict, budget: Budget) -> ConfigMapInfo:
    return ConfigMapInfo(
        name=untrusted(cm["metadata"]["name"]),
        data=untrusted(budget.clip_map(cm.get("data"), value_limit=1000)),
    )


def parse_service(svc: dict, budget: Budget) -> ServiceInfo:
    spec = svc.get("spec") or {}
    return ServiceInfo(
        name=untrusted(svc["metadata"]["name"]),
        type=trusted(spec.get("type", "ClusterIP")),
        cluster_ip=trusted(spec["clusterIP"]) if spec.get("clusterIP") else None,
        ports=[
            ServicePort(
                port=trusted(int(p["port"])),
                target_port=trusted(str(p.get("targetPort", p["port"]))),
                protocol=trusted(p.get("protocol", "TCP")),
            )
            for p in spec.get("ports") or []
        ],
        selector=untrusted(budget.clip_map(spec.get("selector"))),
    )
