"""The runner + report end to end, against scripted fake models (no Ollama)."""

import json

from watchtower.eval.tool_calling import report
from watchtower.eval.tool_calling.cases import CASES, ExpectKind
from watchtower.eval.tool_calling.heldout import HELDOUT_CASES
from watchtower.eval.tool_calling.run import open_db, run_gate, select_cases
from watchtower.models.client import ChatResponse, ModelError, ToolCall


class FakeClient:
    """Answers each case by the last user/tool message it sees; `policy` decides how."""

    model = "fake:1"

    def __init__(self, policy):
        self.policy = policy
        self.calls = 0
        self.convos = {}

    def register(self, convos):
        self.convos = {json.dumps(m): cid for cid, m in convos.items()}

    def chat(self, messages, tools=None, options=None):
        self.calls += 1
        n_tokens = len(json.dumps(messages)) // 4 + (len(json.dumps(tools)) // 4 if tools else 0)
        case_id = self.convos.get(json.dumps(messages))
        case = next((c for c in CASES + HELDOUT_CASES if c.id == case_id), None)
        calls, content = self.policy(case, options.seed if options else None)
        return ChatResponse(
            model=self.model,
            content=content,
            tool_calls=calls,
            latency_ms=100,
            prompt_tokens=n_tokens,
            completion_tokens=5,
            done_reason="stop",
        )

    def residency(self):
        return {"gpu_pct": 100.0, "size": 1, "size_vram": 1, "context_length": 8192}


def oracle(case, seed):
    if case is None or case.expect.kind != ExpectKind.CALL:
        return [], "answer"
    return [ToolCall(name=case.expect.tool, arguments=dict(case.expect.args))], ""


def _run(tmp_path, policy, **kw):
    from watchtower.eval.tool_calling.run import build_messages, fixture_backends

    client = FakeClient(policy)
    backends = fixture_backends(c.fixture for c in CASES)
    client.register({c.id: build_messages(c, backends[c.fixture]) for c in CASES})
    db = open_db(tmp_path / "t.sqlite")
    run_id = run_gate(
        client,
        db,
        cases=list(CASES),
        seeds=range(2),
        meta={"model_digest": "d"},
        log=lambda s: None,
        **kw,
    )
    return client, db, run_id


def test_perfect_model_is_go(tmp_path):
    client, db, run_id = _run(tmp_path, oracle)
    run, rows = report.load(db, run_id)
    assert len(rows) == 2 * len(CASES) == run["n_planned"]
    assert all(r["passed"] for r in rows)
    assert all(r["model"] == "fake:1" and r["model_digest"] == "d" for r in rows)
    assert run["schema_tokens"] and run["schema_tokens"] > 0
    assert run["gpu_pct"] == 100.0
    assert report.verdict(report.metrics(rows, run["n_planned"]))[0].startswith("GO")
    text = report.render(db, run_id)
    assert "GO: all hard thresholds met" in text and "## Failures" in text


def test_suites_are_kept_apart():
    assert select_cases(None) == list(CASES)
    assert select_cases(None, "heldout") == list(HELDOUT_CASES)
    assert {c.category.value for c in select_cases("C5", "heldout")} == {"C5"}
    try:
        select_cases(CASES[0].id, "heldout")  # a gate case id is not in the held-out pool
    except SystemExit:
        pass
    else:
        raise AssertionError("gate case id accepted in the held-out suite")


def test_heldout_report_is_not_labelled_a_gate_verdict(tmp_path):
    from watchtower.eval.tool_calling.run import build_messages, fixture_backends

    client = FakeClient(oracle)
    backends = fixture_backends(c.fixture for c in HELDOUT_CASES)
    client.register({c.id: build_messages(c, backends[c.fixture]) for c in HELDOUT_CASES})
    db = open_db(tmp_path / "h.sqlite")
    run_id = run_gate(
        client,
        db,
        cases=list(HELDOUT_CASES),
        seeds=range(1),
        meta={"model_digest": "d"},
        log=lambda s: None,
    )
    text = report.render(db, run_id)
    assert text.startswith("# W4 tool-calling: held-out set (not part of the gate)")
    assert "Suggested verdict" not in text
    assert "never pooled" in text


def test_strict_arg_validity_counts_blank_arguments_as_invalid(tmp_path):
    def blank_selector(case, seed):
        if case is not None and case.id == "c2-selector":
            return [
                ToolCall(name="get_pods", arguments={"selector": "app=inventory", "name": ""})
            ], ""
        return oracle(case, seed)

    _, db, run_id = _run(tmp_path, blank_selector)
    run, rows = report.load(db, run_id)
    m = report.metrics(rows, run["n_planned"])
    strict, rescued = report.strict_arg_validity(rows)
    assert rescued == 2  # c2-selector, both seeds: accepted only because "" is read as omitted
    assert m["arg_validity"] == 1.0 and strict < 1.0
    assert "Strict arg_validity" in report.render(db, run_id)


def test_text_tool_calls_fail_structurally(tmp_path):
    def text_caller(case, seed):
        calls, content = oracle(case, seed)
        if calls:
            c = calls[0]
            return [], f'<tool_call>{{"name": "{c.name}", "arguments": {json.dumps(c.arguments)}}}'
        return calls, content

    _, db, run_id = _run(tmp_path, text_caller)
    run, rows = report.load(db, run_id)
    m = report.metrics(rows, run["n_planned"])
    assert m["structural_rate"] == 0.0
    label, failed = report.verdict(m)
    assert label.startswith("NO-GO") and "structural_rate" in failed
    assert "[tool call written as text]" in report.render(db, run_id)


def test_hallucinating_model(tmp_path):
    def inventor(case, seed):
        calls, content = oracle(case, seed)
        if calls:
            calls[0].arguments["namespace"] = "watchtower"
        return calls, content

    _, db, run_id = _run(tmp_path, inventor)
    run, rows = report.load(db, run_id)
    m = report.metrics(rows, run["n_planned"])
    assert m["hallucination_rate"] > 0.5 and m["arg_validity"] == 0.0


def test_failed_requests_make_the_run_invalid(tmp_path):
    class Flaky(FakeClient):
        def chat(self, messages, tools=None, options=None):
            if options is not None and options.seed == 1:
                raise ModelError("HTTP 503 from /api/chat: queue full")
            return super().chat(messages, tools, options)

    client = Flaky(oracle)
    from watchtower.eval.tool_calling.run import build_messages, fixture_backends

    backends = fixture_backends(c.fixture for c in CASES)
    client.register({c.id: build_messages(c, backends[c.fixture]) for c in CASES})
    db = open_db(tmp_path / "t.sqlite")
    run_id = run_gate(client, db, cases=list(CASES), seeds=range(2), log=lambda s: None)
    run, rows = report.load(db, run_id)
    m = report.metrics(rows, run["n_planned"])
    assert m["completion_rate"] == 0.5
    assert report.verdict(m)[0].startswith("INVALID")
    assert sum(r["errored"] for r in rows) == len(CASES)
