"""The pre-registered cases must be well-formed against the real tools and fixtures."""

import collections

import pytest

from watchtower.eval.tool_calling.cases import CASES, Call, Category, ExpectKind, User
from watchtower.eval.tool_calling.heldout import HELDOUT_CASES
from watchtower.eval.tool_calling.run import build_messages, fixture_backends
from watchtower.tools.registry import TOOLS, call_tool

# The gate is CASES alone (pre-registered in 0a146b2). HELDOUT_CASES are reported separately but
# must be just as well-formed, so the per-case checks below run over both.
ALL_CASES = CASES + HELDOUT_CASES
BACKENDS = fixture_backends(c.fixture for c in ALL_CASES)


def test_ids_unique_and_categories_covered():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    counts = collections.Counter(c.category for c in CASES)
    assert set(counts) == set(Category)
    assert all(n >= 4 for n in counts.values()), counts
    assert 25 <= len(CASES) <= 40


def test_gate_is_still_the_thirty_preregistered_cases():
    assert len(CASES) == 30


def test_heldout_ids_unique_and_disjoint_from_gate():
    ids = [c.id for c in ALL_CASES]
    assert len(ids) == len(set(ids))
    assert all(c.id.startswith("h") for c in HELDOUT_CASES)
    assert set(collections.Counter(c.category for c in HELDOUT_CASES)) == set(Category)


@pytest.mark.parametrize(
    "case", [c for c in ALL_CASES if c.expect.kind == ExpectKind.CALL], ids=lambda c: c.id
)
def test_expected_call_is_valid_and_runs_on_its_fixture(case):
    """The answer we score as correct must itself be a valid, working call."""
    spec = TOOLS[case.expect.tool]
    parsed = spec.args_model.model_validate(case.expect.args).model_dump()
    assert all(parsed[k] == v for k, v in case.expect.args.items())
    result = call_tool(BACKENDS[case.fixture], case.expect.tool, case.expect.args)
    # The scored response is never executed, so an uncaptured probe path (c2-probe-path tests
    # path fidelity on a path the fixtures never fetched) is acceptable. Nothing else is.
    uncaptured_probe = (
        case.expect.tool == "http_probe" and not result.ok and "not captured" in result.error.value
    )
    assert result.ok or uncaptured_probe, f"{case.id}: fails on {case.fixture}: {result.error}"


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.id)
def test_conversation_shape(case):
    assert isinstance(case.turns[0], User)
    calls = [t for t in case.turns if isinstance(t, Call)]
    results = [call_tool(BACKENDS[case.fixture], t.tool, t.args) for t in calls]
    if case.category == Category.RECOVERY:
        # the conversation ends on a scripted call that the tool layer rejects
        assert isinstance(case.turns[-1], Call)
        assert not results[-1].ok and "invalid arguments" in results[-1].error.value
        assert all(r.ok for r in results[:-1])
    else:
        assert all(r.ok for r in results), case.id

    messages = build_messages(case, BACKENDS[case.fixture])
    assert messages[0]["role"] == "system"
    assert len(messages) == 1 + sum(1 if isinstance(t, User) else 2 for t in case.turns)
    if calls:
        assert any(m["role"] == "tool" for m in messages)


def test_chaining_targets_are_only_findable_from_the_tool_result():
    """C3 must test chaining: the target pod name is in the tool result, never in a prompt."""
    for case in (c for c in ALL_CASES if c.category == Category.CHAINING):
        target = case.expect.args.get("pod") or case.expect.args.get("involved_object")
        if target is None:
            continue
        prompts = " ".join(t.text for t in case.turns if isinstance(t, User))
        assert target not in prompts, case.id
        tool_text = " ".join(
            m["content"]
            for m in build_messages(case, BACKENDS[case.fixture])
            if m["role"] == "tool"
        )
        assert target in tool_text, case.id
