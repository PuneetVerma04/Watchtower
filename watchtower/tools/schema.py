"""What the model sees: flat tool schemas, and validation errors that say what to fix.

The pydantic argument models (args.py) stay the single source of truth and the security
boundary: `call_tool` still validates every call against them, strictly. This module only changes
how they are *presented* to the model.

Why flatten: pydantic renders an optional field as `anyOf: [{type: string, ...}, {type: null}]`
with no top-level `type`. Ollama's tool-schema handling does not carry that through to a small
model reliably: in the first W4 gate run qwen2.5:7b sent `{"object": null}` and
`{"object": "kube-system"}` for `get_pods(name=...)`, an argument that does not exist. So every
property is sent as a plain `{type, description}`; the bounds and defaults that the JSON Schema
keywords would have carried are written into the description instead.

Errors: `describe_error` returns one reason per invalid field, built only from pydantic's error
type and from constraint values in our own models. It never includes the offending value: that
value came from the model and may be hostile text (see test_invalid_args_report_fields_not_values).
"""

import re
from typing import Any

from pydantic import BaseModel
from pydantic_core import ErrorDetails

from watchtower.tools.args import HTTP_PATH_RE, K8S_NAME_RE, SELECTOR_RE

# A key the model invented is echoed back only if it looks like an identifier. Anything else is
# model-controlled text of arbitrary length and content.
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")

_PATTERN_HINTS = {
    K8S_NAME_RE.pattern: (
        "must be an exact Kubernetes object name: lowercase letters, digits, '-' and '.', "
        "no spaces, capitals or kind prefix such as 'pod/'; copy it from a tool result"
    ),
    SELECTOR_RE.pattern: "must be a label selector like app=worker (key=value, comma separated)",
    HTTP_PATH_RE.pattern: "must start with '/' and use only letters, digits and . _ ~ / -",
}

_TYPE_HINTS = {
    "string_type": "must be a string",
    "int_type": "must be an integer",
    "int_parsing": "must be an integer",
    "int_from_float": "must be an integer",
    "bool_type": "must be true or false",
    "bool_parsing": "must be true or false",
    "missing": "is required",
}


def _range_text(prop: dict[str, Any]) -> str:
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if lo is not None and hi is not None:
        return f"{lo}-{hi}"
    if lo is not None:
        return f">= {lo}"
    if hi is not None:
        return f"<= {hi}"
    return ""


def _flat_property(prop: dict[str, Any]) -> dict[str, Any]:
    merged = dict(prop)
    if "anyOf" in prop:
        non_null = [b for b in prop["anyOf"] if b.get("type") != "null"]
        if len(non_null) != 1:
            raise ValueError(f"cannot flatten a union of {len(non_null)} non-null types")
        merged = {**{k: v for k, v in prop.items() if k != "anyOf"}, **non_null[0]}

    kind = merged["type"]
    facts = []
    if kind == "integer":
        rng = _range_text(merged)
        facts.append(f"Integer {rng}" if rng else "Integer")
    if merged.get("default") is not None:
        default = str(merged["default"]).lower() if kind == "boolean" else merged["default"]
        facts.append(f"default {default}")
    description = merged.get("description", "").strip()
    if facts:
        description = f"{description} ({', '.join(facts)})".strip()
    flat: dict[str, Any] = {"type": kind}
    if description:
        flat["description"] = description
    return flat


def flatten_parameters(model: type[BaseModel]) -> dict[str, Any]:
    """The `parameters` object for one tool: plain types and descriptions, nothing nested."""
    schema = model.model_json_schema()
    out: dict[str, Any] = {
        "type": "object",
        "properties": {n: _flat_property(p) for n, p in schema.get("properties", {}).items()},
        "additionalProperties": False,
    }
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def _reason(err: ErrorDetails) -> str:
    kind = err["type"]
    ctx = err.get("ctx") or {}
    if kind in _TYPE_HINTS:
        return _TYPE_HINTS[kind]
    if kind == "string_pattern_mismatch":
        return _PATTERN_HINTS.get(ctx.get("pattern", ""), "has the wrong format")
    if kind == "string_too_short":
        return f"must be at least {ctx.get('min_length')} character(s)"
    if kind == "string_too_long":
        return f"must be at most {ctx.get('max_length')} characters"
    if kind == "greater_than_equal":
        return f"must be at least {ctx.get('ge')}"
    if kind == "less_than_equal":
        return f"must be at most {ctx.get('le')}"
    if kind == "value_error":  # our own AfterValidator messages, e.g. "'..' is not allowed"
        return str(err["msg"]).removeprefix("Value error, ")
    return f"is invalid ({kind})"


def describe_error(model: type[BaseModel], errors: list[ErrorDetails]) -> str:
    """'field: reason; field: reason', sorted, one entry per invalid field."""
    valid = ", ".join(model.model_fields) or "none"
    reasons: dict[str, str] = {}
    for err in errors:
        field = str(err["loc"][0]) if err["loc"] else "<root>"
        if err["type"] == "extra_forbidden":
            shown = field if _SAFE_KEY.match(field) else "<unrecognised parameter>"
            reasons[shown] = f"is not a parameter of this tool (valid parameters: {valid})"
            if field == "namespace":
                reasons[shown] += (
                    "; there is no namespace parameter, "
                    "every tool always uses the watchtower namespace"
                )
        else:
            reasons.setdefault(field, _reason(err))
    return "; ".join(f"{f}: {r}" for f, r in sorted(reasons.items()))
