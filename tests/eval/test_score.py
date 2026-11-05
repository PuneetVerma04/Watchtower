from watchtower.eval.tool_calling.cases import CASES
from watchtower.eval.tool_calling.score import check_call, score
from watchtower.models.client import ChatResponse, ToolCall

BY_ID = {c.id: c for c in CASES}


def _resp(*calls: tuple[str, dict], content: str = "", unparsed: str | None = None):
    return ChatResponse(
        model="m",
        content=content,
        latency_ms=1,
        tool_calls=[ToolCall(name=n, arguments=a, arguments_unparsed=unparsed) for n, a in calls],
    )


def test_exact_expected_call_passes():
    s = score(
        BY_ID["c2-previous-logs"],
        _resp(
            ("get_pod_logs", {"pod": "worker-79f988ccbc-7brv9", "previous": True, "tail_lines": 30})
        ),
    )
    assert s.passed and s.structural_ok and s.selection_ok and s.args_valid and s.args_correct
    assert not s.hallucinated


def test_defaults_count_toward_required_args():
    # c2-current-logs requires previous=False: omitting it (default False) is correct
    s = score(
        BY_ID["c2-current-logs"],
        _resp(("get_pod_logs", {"pod": "gateway-746cb85cc9-qch2f", "tail_lines": 10})),
    )
    assert s.passed


def test_wrong_value_is_valid_but_incorrect():
    s = score(
        BY_ID["c2-previous-logs"],
        _resp(
            (
                "get_pod_logs",
                {"pod": "worker-79f988ccbc-7brv9", "previous": False, "tail_lines": 30},
            )
        ),
    )
    assert s.selection_ok and s.args_valid and not s.args_correct and not s.passed


def test_wrong_tool():
    s = score(BY_ID["c1-events"], _resp(("get_pods", {})))
    assert s.structural_ok and not s.selection_ok and not s.passed


def test_invented_field_is_hallucination_and_invalid():
    s = score(BY_ID["c1-list-pods"], _resp(("get_pods", {"namespace": "kube-system"})))
    assert s.hallucinated and not s.args_valid and not s.passed
    assert check_call(ToolCall(name="get_pods", arguments={"namespace": "x"})).invented_field


def test_out_of_range_value_is_invalid_not_hallucinated():
    s = score(
        BY_ID["c2-current-logs"],
        _resp(("get_pod_logs", {"pod": "gateway-746cb85cc9-qch2f", "tail_lines": 5000})),
    )
    assert not s.args_valid and not s.hallucinated


def test_unknown_tool_is_hallucination():
    s = score(BY_ID["c4-nonexistent-tool"], _resp(("restart_deployment", {"name": "orders"})))
    assert s.hallucinated and not s.passed


def test_unparsed_arguments_are_invalid():
    s = score(BY_ID["c1-events"], _resp(("get_events", {}), unparsed="{oops"))
    assert s.selection_ok and not s.args_valid and not s.passed


def test_text_tool_call_is_a_structural_failure():
    s = score(
        BY_ID["c1-events"],
        _resp(content='<tool_call>\n{"name": "get_events", "arguments": {}}\n</tool_call>'),
    )
    assert s.text_tool_call and s.structural_ok is False and not s.passed


def test_no_call_cases():
    case = BY_ID["c4-no-tool-needed"]
    assert score(case, _resp(content="It gates traffic until the pod is ready.")).passed
    assert not score(case, _resp(("get_pods", {}))).passed
    s = score(case, _resp(content='{"name": "get_pods", "arguments": {}}'))
    assert s.text_tool_call and not s.passed
    assert s.structural_ok is None  # not a call case


def test_no_invalid_call_cases():
    case = BY_ID["c4-other-namespace"]
    assert score(case, _resp(content="I can only see the application namespace.")).passed
    assert score(case, _resp(("get_pods", {}))).passed  # a valid call is acceptable
    assert not score(case, _resp(("get_pods", {"namespace": "kube-system"}))).passed


def test_only_first_call_is_scored_for_selection_but_all_for_hallucination():
    s = score(BY_ID["c1-events"], _resp(("get_events", {}), ("delete_namespace", {})))
    assert s.n_calls == 2 and s.passed is True and s.hallucinated


def test_failed_request():
    s = score(BY_ID["c1-events"], None)
    assert s.errored and not s.passed and s.structural_ok is False and not s.hallucinated
    s = score(BY_ID["c4-no-tool-needed"], None)
    assert s.errored and not s.passed and s.structural_ok is None
