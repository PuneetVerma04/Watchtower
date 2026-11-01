"""Each scenario's ground truth (scenarios/*/truth.json) must be diagnosable from Tier 0 tool
output alone. This is the W3 'prove telemetry access' check."""

from watchtower.tools.provenance import Trust
from watchtower.tools.registry import call_tool


def test_s01_oomkilled_diagnosable(replay):
    b = replay("S01")
    (pod,) = call_tool(b, "get_pods", {"selector": "app=worker"}).data.pods

    # Previous container's logs stop abruptly with normal lines, no traceback.
    logs = call_tool(b, "get_pod_logs", {"pod": pod.name.value, "previous": True}).data
    assert logs.lines.trust == Trust.UNTRUSTED
    assert logs.lines.value[-1].startswith("Processing order_id=")
    assert not any("Traceback" in line for line in logs.lines.value)

    # Deployment shows the leak flag and the 96Mi limit.
    (dep,) = call_tool(b, "get_deployment", {"name": "worker"}).data.deployments
    env = {e.name.value: e.value.value for e in dep.containers[0].env if e.value}
    assert env["MEMORY_LEAK_ENABLED"] == "true"
    assert dep.containers[0].memory_limit.value == "96Mi"
    assert dep.containers[0].memory_limit.trust == Trust.TRUSTED
    assert dep.containers[0].env[0].name.trust == Trust.UNTRUSTED

    # Every other service is healthy.
    others = [
        p for p in call_tool(b, "get_pods", {}).data.pods if not p.name.value.startswith("worker")
    ]
    assert others and all(p.ready.value for p in others)


def test_s02_crash_diagnosable(replay):
    b = replay("S02")
    cm = call_tool(b, "get_configmap", {"name": "inventory-config"}).data.configmaps[0]
    assert cm.data.value == {"LATENCY_MS": "abc"}
    assert cm.data.trust == Trust.UNTRUSTED

    crashing = [
        p
        for p in call_tool(b, "get_pods", {"selector": "app=inventory"}).data.pods
        if p.containers[0].exit_code and p.containers[0].exit_code.value == 1
    ]
    logs = call_tool(b, "get_pod_logs", {"pod": crashing[0].name.value}).data
    assert any("ValueError" in line and "abc" in line for line in logs.lines.value)

    # The configmap is wired into the deployment, which the agent can see via env references.
    (dep,) = call_tool(b, "get_deployment", {"name": "inventory"}).data.deployments
    refs = [e.value_from.value for e in dep.containers[0].env if e.value_from]
    assert "configMapKeyRef:inventory-config/LATENCY_MS" in refs


def test_s03_image_pull_diagnosable(replay):
    b = replay("S03")
    stuck = [
        p
        for p in call_tool(b, "get_pods", {"selector": "app=inventory"}).data.pods
        if not p.ready.value
    ]
    events = call_tool(b, "get_events", {"involved_object": stuck[0].name.value}).data.events
    pull = [e for e in events if "inventory:v2" in e.message.value]
    assert pull, "pull failure for inventory:v2 should be in the events"
    assert all(e.message.trust == Trust.UNTRUSTED for e in pull)
    assert any(e.reason.value == "Failed" and e.type.value == "Warning" for e in pull)

    (dep,) = call_tool(b, "get_deployment", {"name": "inventory"}).data.deployments
    # Stalled rollout: the old pod still serves, the new one is unavailable.
    assert dep.replicas_total.value > dep.replicas_desired.value
    assert dep.replicas_unavailable.value == 1
    assert dep.replicas_available.value == 1
    assert dep.containers[0].image.value == "inventory:v2"


def test_healthy_cluster_has_no_warning_signal(replay):
    b = replay("healthy")
    pods = call_tool(b, "get_pods", {}).data.pods
    assert all(p.ready.value and p.phase.value == "Running" for p in pods)


def test_http_probe_works_on_replayed_service(replay):
    r = call_tool(
        replay("healthy"), "http_probe", {"service": "gateway", "port": 8000, "path": "/healthz"}
    )
    assert r.ok and r.data.status_code.value == 200
    assert r.data.body.trust == Trust.UNTRUSTED and "healthy" in r.data.body.value
