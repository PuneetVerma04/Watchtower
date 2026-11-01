"""Every tagged field in every tool output must carry the trust label we designed.

The expected label is keyed by field name. A new field with no entry here fails the test, which
forces whoever adds it to make an explicit trust decision (the whole point of provenance tags).
"""

import pytest

from watchtower.tools.capture import ReplayBackend, dump_results, run_battery

from .conftest import SETS

# Free text, or writable by a deployer / workload.
UNTRUSTED_KEYS = {
    "name", "image", "node", "owner", "message", "object_name", "value", "value_from",
    "labels", "annotations", "data", "selector", "lines", "body", "state_message", "error",
}  # fmt: skip

# Reported by kubelet / API server / controllers, or a type that cannot carry free text.
TRUSTED_KEYS = {
    "phase", "ready", "created_at", "restart_count", "state", "state_reason", "exit_code",
    "last_terminated_reason", "last_terminated_exit_code", "type", "status", "reason", "count",
    "last_seen", "object_kind", "replicas_desired", "replicas_total", "replicas_ready",
    "replicas_available", "replicas_updated", "replicas_unavailable", "cpu_request",
    "memory_request", "cpu_limit", "memory_limit", "port", "target_port", "protocol",
    "cluster_ip", "status_code", "latency_ms", "cpu", "memory",
}  # fmt: skip


def _tagged_leaves(node, key="", out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if set(node) == {"value", "trust"}:
            out.append((key, node["trust"]))
            return out
        for k, v in node.items():
            _tagged_leaves(v, k, out)
    elif isinstance(node, list):
        for v in node:
            _tagged_leaves(v, key, out)
    return out


def test_key_sets_do_not_overlap():
    assert not UNTRUSTED_KEYS & TRUSTED_KEYS


@pytest.mark.parametrize("name", SETS)
def test_every_tagged_field_has_the_designed_label(records, name):
    dumped = dump_results(run_battery(ReplayBackend(records[name])))
    leaves = _tagged_leaves(dumped)
    assert len(leaves) > 500  # sanity: we really walked the outputs
    for key, trust in leaves:
        if key in UNTRUSTED_KEYS:
            assert trust == "UNTRUSTED", f"{key} must be UNTRUSTED"
        elif key in TRUSTED_KEYS:
            assert trust == "TRUSTED", f"{key} must be TRUSTED"
        else:
            pytest.fail(f"tagged field {key!r} has no trust decision in this test")
