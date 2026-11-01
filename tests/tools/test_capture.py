"""Capture/replay (W3 target 2): the cluster is not needed to replay a scenario."""

import json

import pytest

from watchtower.tools.backend import BackendError
from watchtower.tools.capture import (
    BATTERY_OP,
    CaptureStore,
    CapturingBackend,
    ReplayBackend,
    diff_labels,
    dump_results,
    export_json,
    load_json,
    run_battery,
)
from watchtower.tools.registry import call_tool

from .conftest import SETS


class FakeLive:
    """Stand-in for KubectlBackend with deterministic content and one failing call."""

    def __init__(self):
        self.calls = []

    def get_json(self, kind, name=None, selector=None):
        self.calls.append(("get_json", kind, name, selector))
        if kind == "boom":
            raise BackendError("kubectl exited 1: boom")
        pods = [
            {"metadata": {"name": "a-1", "labels": {"app": "a"}}},
            {"metadata": {"name": "b-1", "labels": {"app": "b", "tier": "x"}}},
        ]
        return {"kind": "List", "items": pods}

    def logs(self, pod, container, previous, tail):
        self.calls.append(("logs", pod, container, previous, tail))
        if previous:
            raise BackendError("no previous container")
        return "\n".join(f"line {i}" for i in range(1, 101))

    def raw_get(self, path):
        self.calls.append(("raw_get", path))
        return '{"ok":true}'


@pytest.fixture
def store(tmp_path):
    s = CaptureStore(tmp_path / "c.sqlite")
    yield s
    s.close()


def test_capture_then_replay_without_live_backend(store):
    live = FakeLive()
    cap = CapturingBackend(live, store, "demo")
    expected = cap.get_json("pods")
    cap.logs("a-1", None, False, 100)
    cap.raw_get("/x")
    n_live_calls = len(live.calls)

    replay = ReplayBackend(store.load("demo"))
    assert replay.get_json("pods") == expected
    assert replay.raw_get("/x") == '{"ok":true}'
    assert len(live.calls) == n_live_calls  # replay never touched the live backend


def test_replay_derives_name_and_selector_from_the_list(store):
    CapturingBackend(FakeLive(), store, "demo").get_json("pods")
    replay = ReplayBackend(store.load("demo"))
    assert replay.get_json("pods", name="b-1")["metadata"]["name"] == "b-1"
    assert [i["metadata"]["name"] for i in replay.get_json("pods", selector="app=a")["items"]] == [
        "a-1"
    ]
    assert replay.get_json("pods", selector="app=b,tier=x")["items"][0]["metadata"]["name"] == "b-1"
    assert replay.get_json("pods", selector="app=nope")["items"] == []
    with pytest.raises(BackendError, match="NotFound"):
        replay.get_json("pods", name="missing")


def test_replay_slices_logs_to_any_shorter_tail(store):
    CapturingBackend(FakeLive(), store, "demo").logs("a-1", None, False, 100)
    replay = ReplayBackend(store.load("demo"))
    assert replay.logs("a-1", None, False, 3).splitlines() == ["line 98", "line 99", "line 100"]
    assert len(replay.logs("a-1", "main", False, 50).splitlines()) == 50  # container falls back


def test_errors_are_captured_and_replayed(store):
    cap = CapturingBackend(FakeLive(), store, "demo")
    for call in (lambda: cap.get_json("boom"), lambda: cap.logs("a-1", None, True, 10)):
        with pytest.raises(BackendError):
            call()
    replay = ReplayBackend(store.load("demo"))
    with pytest.raises(BackendError, match="boom"):
        replay.get_json("boom")
    with pytest.raises(BackendError, match="no previous container"):
        replay.logs("a-1", None, True, 10)


def test_missing_capture_is_a_clear_error(store):
    replay = ReplayBackend(store.load("empty"))
    with pytest.raises(BackendError, match="not captured"):
        replay.get_json("pods")
    r = call_tool(replay, "get_pods", {})
    assert not r.ok and "not captured" in r.error.value  # tools never raise


def test_store_sets_and_replace(store):
    store.put("s", "op", "k", "one", None)
    store.put("s", "op", "k", "two", None)
    assert store.load("s") == {("op", "k"): ("two", None)}
    assert store.sets() == ["s"]
    store.delete_set("s")
    assert store.sets() == []


def test_json_export_round_trip(store, tmp_path):
    CapturingBackend(FakeLive(), store, "demo").get_json("pods")
    records = store.load("demo")
    export_json(records, "demo", tmp_path / "demo.json")
    assert load_json(tmp_path / "demo.json") == records
    assert json.loads((tmp_path / "demo.json").read_text())["capture_set"] == "demo"


@pytest.mark.parametrize("name", SETS)
def test_offline_replay_reproduces_the_stored_live_results(records, name):
    """The W3 exit check: replaying a captured scenario gives exactly what the live tools gave."""
    stored = {
        key: json.loads(resp)
        for (op, key), (resp, _) in records[name].items()
        if op == BATTERY_OP and resp
    }
    assert len(stored) >= 20
    replayed = dump_results(run_battery(ReplayBackend(records[name])))
    assert diff_labels(stored, replayed) == []


def test_replay_is_deterministic(records):
    a = dump_results(run_battery(ReplayBackend(records["S01"])))
    b = dump_results(run_battery(ReplayBackend(records["S01"])))
    assert a == b
