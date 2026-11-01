import json

from watchtower.tools.provenance import Trust
from watchtower.tools.registry import TOOLS, Tier, call_tool, tool_schemas

EXPECTED = {
    "get_pods",
    "describe_pod",
    "get_pod_logs",
    "get_events",
    "get_deployment",
    "get_configmap",
    "get_service",
    "http_probe",
    "get_metrics",
}


def test_all_nine_tier0_tools_registered():
    assert set(TOOLS) == EXPECTED
    assert all(spec.tier == Tier.READ_ONLY for spec in TOOLS.values())


def test_schemas_have_no_namespace_and_forbid_extra_keys():
    schemas = tool_schemas()
    assert {s["function"]["name"] for s in schemas} == EXPECTED
    for s in schemas:
        params = s["function"]["parameters"]
        assert "namespace" not in params.get("properties", {})
        assert params.get("additionalProperties") is False
    # small enough to leave room in an ~8k context
    assert len(json.dumps(schemas)) < 8000


def test_unknown_tool_fails_without_raising(replay):
    r = call_tool(replay("healthy"), "delete_namespace", {})
    assert not r.ok and r.error.trust == Trust.UNTRUSTED


def test_invalid_args_report_fields_not_values(replay):
    hostile = "--all-namespaces"
    r = call_tool(replay("healthy"), "get_pods", {"name": hostile})
    assert not r.ok
    assert "name" in r.error.value
    assert hostile not in r.error.value  # don't echo hostile text back to the model


def test_extra_keys_rejected(replay):
    r = call_tool(replay("healthy"), "get_pods", {"namespace": "kube-system"})
    assert not r.ok and "namespace" in r.error.value


def test_none_args_ok(replay):
    assert call_tool(replay("healthy"), "get_pods", None).ok


def test_get_metrics_reports_unavailable_without_metrics_server(replay):
    r = call_tool(replay("healthy"), "get_metrics", {})
    assert r.ok and r.data.available is False
