"""Telemetry capture and replay (W3 target 2).

Capture a scenario's raw telemetry once, replay it to the tools without a cluster:

    CapturingBackend(KubectlBackend(), store, "S01")   # live, records raw kubectl output
    ReplayBackend(store.load("S01"))                    # no cluster needed

Both sit behind the Backend protocol, so the tools' parsing code runs identically on live and
replayed data. That is what makes >=20 trials per attack vector feasible on one laptop (P3).

Replay derives narrower queries from list snapshots: a capture of `get pods` also answers
`get pods <name>` and `get pods -l app=x`, and a log capture answers any shorter `tail`.

CLI (from the repo root, in WSL):
    python -m watchtower.tools.capture capture --set S01    # live: capture, then self-verify
    python -m watchtower.tools.capture verify  --set S01    # offline: replay vs stored results
    python -m watchtower.tools.capture export  --set S01 --out tests/tools/fixtures/S01.json
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watchtower.tools.backend import Backend, BackendError, KubectlBackend
from watchtower.tools.provenance import ToolResult
from watchtower.tools.registry import call_tool

DEFAULT_DB = Path(__file__).resolve().parents[2] / "results" / "captures.sqlite"
LOG_TAIL = 200  # capture the maximum the tool allows; replay slices down to the requested tail
HTTP_SERVICES = ("gateway", "orders", "inventory")  # postgres/redis don't speak HTTP

Record = tuple[str | None, str | None]  # (response text, error text)
Records = dict[tuple[str, str], Record]  # (op, key) -> Record

BATTERY_OP = "battery"  # live tool results stored next to the raw data, for offline verify


def _key(*parts: Any) -> str:
    return json.dumps(parts, separators=(",", ":"))


# --------------------------------------------------------------------------- storage


class CaptureStore:
    """SQLite store. One row per (capture_set, op, key); a later write replaces an earlier one."""

    def __init__(self, path: Path | str = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS captures (
                   capture_set TEXT NOT NULL,
                   op          TEXT NOT NULL,
                   key         TEXT NOT NULL,
                   response    TEXT,
                   error       TEXT,
                   captured_at TEXT NOT NULL,
                   PRIMARY KEY (capture_set, op, key)
               )"""
        )
        self.db.commit()

    def put(self, capture_set: str, op: str, key: str, response: str | None, error: str | None):
        self.db.execute(
            "INSERT OR REPLACE INTO captures VALUES (?, ?, ?, ?, ?, ?)",
            (capture_set, op, key, response, error, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def load(self, capture_set: str) -> Records:
        rows = self.db.execute(
            "SELECT op, key, response, error FROM captures WHERE capture_set = ?", (capture_set,)
        )
        return {(op, key): (response, error) for op, key, response, error in rows}

    def delete_set(self, capture_set: str) -> None:
        self.db.execute("DELETE FROM captures WHERE capture_set = ?", (capture_set,))
        self.db.commit()

    def sets(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT capture_set FROM captures ORDER BY 1")
        return [r[0] for r in rows]

    def close(self) -> None:
        self.db.close()


def export_json(records: Records, capture_set: str, path: Path | str) -> None:
    """Write a capture set as a committed-friendly JSON fixture."""
    doc = {
        "capture_set": capture_set,
        "records": [
            {"op": op, "key": key, "response": resp, "error": err}
            for (op, key), (resp, err) in sorted(records.items())
        ],
    }
    Path(path).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")


def load_json(path: Path | str) -> Records:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(r["op"], r["key"]): (r["response"], r["error"]) for r in doc["records"]}


# --------------------------------------------------------------------------- backends


class CapturingBackend:
    """Wraps a live backend and records every call's raw output (or error)."""

    def __init__(self, inner: Backend, store: CaptureStore, capture_set: str) -> None:
        self.inner, self.store, self.capture_set = inner, store, capture_set

    def _record(self, op: str, key: str, call) -> Any:
        try:
            result = call()
        except BackendError as e:
            self.store.put(self.capture_set, op, key, None, str(e))
            raise
        text = result if isinstance(result, str) else json.dumps(result)
        self.store.put(self.capture_set, op, key, text, None)
        return result

    def get_json(self, kind: str, name: str | None = None, selector: str | None = None) -> dict:
        return self._record(
            "get_json",
            _key(kind, name, selector),
            lambda: self.inner.get_json(kind, name, selector),
        )

    def logs(self, pod: str, container: str | None, previous: bool, tail: int) -> str:
        # `tail` is deliberately not part of the key; replay slices to any shorter tail.
        return self._record(
            "logs",
            _key(pod, container, previous),
            lambda: self.inner.logs(pod, container, previous, tail),
        )

    def raw_get(self, path: str) -> str:
        return self._record("raw_get", _key(path), lambda: self.inner.raw_get(path))


def _label_match(item: dict, selector: str) -> bool:
    labels = (item.get("metadata") or {}).get("labels") or {}
    return all(
        labels.get(k) == v for k, _, v in (pair.partition("=") for pair in selector.split(","))
    )


class ReplayBackend:
    """Serves captured records; never touches a cluster."""

    def __init__(self, records: Records) -> None:
        self.records = records

    def _lookup(self, op: str, key: str) -> Record | None:
        return self.records.get((op, key))

    @staticmethod
    def _unwrap(record: Record) -> str:
        response, error = record
        if error is not None:
            raise BackendError(error)
        return response or ""

    def get_json(self, kind: str, name: str | None = None, selector: str | None = None) -> dict:
        exact = self._lookup("get_json", _key(kind, name, selector))
        if exact is not None:
            return json.loads(self._unwrap(exact))

        listing = self._lookup("get_json", _key(kind, None, None))
        if listing is None:
            raise BackendError(f"not captured: get {kind}")
        doc = json.loads(self._unwrap(listing))
        items = doc.get("items", [])
        if selector is not None:
            items = [i for i in items if _label_match(i, selector)]
        if name is not None:
            items = [i for i in items if i["metadata"]["name"] == name]
            if not items:
                raise BackendError(
                    f'kubectl exited 1: Error from server (NotFound): {kind} "{name}" not found'
                )
            return items[0]
        return {**doc, "items": items}

    def logs(self, pod: str, container: str | None, previous: bool, tail: int) -> str:
        record = self._lookup("logs", _key(pod, container, previous)) or self._lookup(
            "logs", _key(pod, None, previous)
        )
        if record is None:
            raise BackendError(f"not captured: logs {pod} previous={previous}")
        text = self._unwrap(record)
        return "\n".join(text.splitlines()[-tail:])

    def raw_get(self, path: str) -> str:
        record = self._lookup("raw_get", _key(path))
        if record is None:
            raise BackendError(f"not captured: {path[:80]}")
        return self._unwrap(record)


# --------------------------------------------------------------------------- battery


def run_battery(backend: Backend) -> dict[str, ToolResult[Any]]:
    """The standard sweep of Tier 0 calls. Running it through a CapturingBackend is what
    records a scenario's telemetry; running it through a ReplayBackend must reproduce it."""
    out: dict[str, ToolResult[Any]] = {}

    def call(label: str, tool: str, args: dict[str, Any]) -> ToolResult[Any]:
        out[label] = call_tool(backend, tool, args)
        return out[label]

    pods = call("get_pods", "get_pods", {})
    call("get_events", "get_events", {})
    call("get_deployment", "get_deployment", {})
    call("get_configmap", "get_configmap", {})
    services = call("get_service", "get_service", {})
    call("get_metrics", "get_metrics", {})

    for pod in pods.data.pods if pods.ok else []:
        name = pod.name.value
        call(f"describe_pod:{name}", "describe_pod", {"pod": name})
        for previous in (False, True):
            call(
                f"get_pod_logs:{name}:previous={previous}",
                "get_pod_logs",
                {"pod": name, "previous": previous, "tail_lines": LOG_TAIL},
            )

    for svc in services.data.services if services.ok else []:
        if svc.name.value not in HTTP_SERVICES:
            continue
        for port in svc.ports:
            call(
                f"http_probe:{svc.name.value}:{port.port.value}",
                "http_probe",
                {"service": svc.name.value, "port": port.port.value, "path": "/healthz"},
            )
    return out


def _normalize(obj: Any) -> Any:
    """Drop fields that legitimately differ between a live call and its replay."""
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items() if k != "latency_ms"}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def dump_results(results: dict[str, ToolResult[Any]]) -> dict[str, Any]:
    return {label: _normalize(r.model_dump(mode="json")) for label, r in results.items()}


def diff_labels(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


# --------------------------------------------------------------------------- CLI


def _summary(results: dict[str, ToolResult[Any]]) -> str:
    ok_n = sum(r.ok for r in results.values())
    return f"{len(results)} tool calls, {ok_n} ok, {len(results) - ok_n} failed"


def cmd_capture(args: argparse.Namespace) -> int:
    store = CaptureStore(args.db)
    store.delete_set(args.set)
    live = run_battery(CapturingBackend(KubectlBackend(), store, args.set))
    live_dump = dump_results(live)
    for label, dumped in live_dump.items():
        store.put(args.set, BATTERY_OP, label, json.dumps(dumped, sort_keys=True), None)
    print(f"captured set {args.set!r} -> {store.path}: {_summary(live)}")

    replayed = dump_results(run_battery(ReplayBackend(store.load(args.set))))
    bad = diff_labels(live_dump, replayed)
    if bad:
        print(f"REPLAY MISMATCH on {len(bad)} calls: {bad}")
        return 1
    print(f"replay reproduces all {len(live_dump)} live results exactly")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    records = load_json(args.json) if args.json else CaptureStore(args.db).load(args.set)
    stored = {
        key: json.loads(resp)
        for (op, key), (resp, _) in records.items()
        if op == BATTERY_OP and resp
    }
    if not stored:
        print(f"no stored battery results for {args.set!r}")
        return 1
    replayed = dump_results(run_battery(ReplayBackend(records)))
    bad = diff_labels(stored, replayed)
    if bad:
        print(f"REPLAY MISMATCH on {len(bad)} calls: {bad}")
        return 1
    print(f"offline replay of {args.set!r} reproduces all {len(stored)} stored results")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    records = CaptureStore(args.db).load(args.set)
    if not records:
        print(f"no capture set {args.set!r} in {args.db}")
        return 1
    export_json(records, args.set, args.out)
    print(f"exported {len(records)} records -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watchtower.tools.capture")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("capture", cmd_capture), ("verify", cmd_verify), ("export", cmd_export)):
        p = sub.add_parser(name)
        p.add_argument("--set", required=True, help="capture set name, e.g. S01")
        p.add_argument("--db", default=str(DEFAULT_DB))
        if name == "verify":
            p.add_argument("--json", help="verify a JSON fixture instead of the SQLite db")
        if name == "export":
            p.add_argument("--out", required=True)
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
