"""Scoring of one trial (one model response to one case). Pure functions, no I/O.

A call is valid only if it passes the real argument models in the tool registry (the same
check `call_tool` applies), so "valid" here means what the agent would actually be able to run.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ValidationError

from watchtower.eval.tool_calling.cases import Case, ExpectKind
from watchtower.models.client import ChatResponse, ToolCall
from watchtower.tools.registry import TOOLS

# A tool call written into `content` instead of `tool_calls`: JSON naming one of our tools,
# or the <tool_call> wrapper Qwen's chat template uses.
_TEXT_CALL_RE = re.compile(
    r"<tool_call>|\"name\"\s*:\s*\"(" + "|".join(map(re.escape, TOOLS)) + r")\""
)


@dataclass(frozen=True)
class CallCheck:
    known_tool: bool
    valid: bool
    invented_field: bool
    args: dict[str, Any] | None  # validated arguments, defaults filled in


@dataclass(frozen=True)
class Score:
    errored: bool
    n_calls: int
    called: str | None  # first call's tool name
    text_tool_call: bool
    structural_ok: bool | None  # None: not applicable (case expects no specific call)
    selection_ok: bool | None
    args_valid: bool | None  # None also when no call was made
    args_correct: bool | None
    hallucinated: bool
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_call(call: ToolCall) -> CallCheck:
    spec = TOOLS.get(call.name)
    if spec is None:
        return CallCheck(known_tool=False, valid=False, invented_field=False, args=None)
    if call.arguments_unparsed is not None:
        return CallCheck(known_tool=True, valid=False, invented_field=False, args=None)
    try:
        parsed = spec.args_model.model_validate(call.arguments)
    except ValidationError as e:
        invented = any(err["type"] == "extra_forbidden" for err in e.errors())
        return CallCheck(known_tool=True, valid=False, invented_field=invented, args=None)
    return CallCheck(known_tool=True, valid=True, invented_field=False, args=parsed.model_dump())


def score(case: Case, response: ChatResponse | None) -> Score:
    expect = case.expect
    is_call = expect.kind == ExpectKind.CALL
    if response is None:  # request failed: counts as a failure everywhere
        na = False if is_call else None
        return Score(True, 0, None, False, na, na, None, na, False, False)

    calls = response.tool_calls
    checks = [check_call(c) for c in calls]
    hallucinated = any(not c.known_tool or c.invented_field for c in checks)
    text_call = not calls and bool(_TEXT_CALL_RE.search(response.content))
    first, first_check = (calls[0], checks[0]) if calls else (None, None)

    if is_call:
        structural_ok = bool(calls)
        selection_ok = first is not None and first.name == expect.tool
        args_valid = first_check.valid if first_check else None
        args_correct = bool(
            selection_ok
            and first_check.valid
            and all(first_check.args.get(k) == v for k, v in expect.args.items())
        )
        passed = args_correct
    else:
        structural_ok = selection_ok = args_correct = None
        args_valid = first_check.valid if first_check else None
        if expect.kind == ExpectKind.NO_CALL:
            passed = not calls and not text_call
        else:  # NO_INVALID_CALL: calling is allowed, inventing is not
            passed = all(c.valid for c in checks) and not hallucinated

    return Score(
        errored=False,
        n_calls=len(calls),
        called=first.name if first else None,
        text_tool_call=text_call,
        structural_ok=structural_ok,
        selection_ok=selection_ok,
        args_valid=args_valid,
        args_correct=args_correct,
        hallucinated=hallucinated,
        passed=passed,
    )
