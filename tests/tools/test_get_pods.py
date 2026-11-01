"""get_pods against real captured telemetry (tests/tools/fixtures/*.json)."""

from watchtower.tools.models import ContainerState
from watchtower.tools.provenance import Trust
from watchtower.tools.registry import call_tool


def _container(replay, scenario, selector):
    r = call_tool(replay(scenario), "get_pods", {"selector": selector})
    assert r.ok, r.error
    return r.data.pods


def test_s01_oomkilled_pod_is_trusted_status(replay):
    (pod,) = _container(replay, "S01", "app=worker")
    c = pod.containers[0]
    assert c.restart_count.value >= 1
    assert c.last_terminated_reason.value == "OOMKilled"
    assert c.last_terminated_exit_code.value == 137
    for field in (c.restart_count, c.last_terminated_reason, c.last_terminated_exit_code, c.state):
        assert field.trust == Trust.TRUSTED


def test_names_and_images_are_untrusted(replay):
    for pod in _container(replay, "S01", "app=worker"):
        assert pod.name.trust == Trust.UNTRUSTED
        assert pod.node.trust == Trust.UNTRUSTED
        assert pod.containers[0].name.trust == Trust.UNTRUSTED
        assert pod.containers[0].image.trust == Trust.UNTRUSTED


def test_s02_crash_shows_terminated_error_not_crashloopbackoff(replay):
    crashing = [
        p
        for p in _container(replay, "S02", "app=inventory")
        if p.containers[0].state.value == ContainerState.TERMINATED
    ]
    assert len(crashing) == 1
    c = crashing[0].containers[0]
    assert c.state_reason.value == "Error" and c.exit_code.value == 1
    assert not crashing[0].ready.value


def test_s03_image_pull_failure_visible(replay):
    waiting = [
        p.containers[0]
        for p in _container(replay, "S03", "app=inventory")
        if p.containers[0].state.value == ContainerState.WAITING
    ]
    assert len(waiting) == 1
    assert waiting[0].state_reason.value in {"ErrImagePull", "ImagePullBackOff"}
    assert waiting[0].image.value == "inventory:v2"


def test_selector_and_name_filters_come_from_the_snapshot(replay):
    everything = call_tool(replay("healthy"), "get_pods", {}).data.pods
    assert len(everything) == 6
    one = call_tool(replay("healthy"), "get_pods", {"name": everything[0].name.value})
    assert [p.name.value for p in one.data.pods] == [everything[0].name.value]


def test_unknown_pod_is_a_failed_result_not_an_exception(replay):
    r = call_tool(replay("healthy"), "get_pods", {"name": "no-such-pod"})
    assert not r.ok
    assert r.error.trust == Trust.UNTRUSTED and "NotFound" in r.error.value
