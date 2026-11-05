"""Provider-neutral chat interface (Architecture §6).

Agent and eval code depend on `ModelClient` only, never on a provider SDK or HTTP shape, so the
P5 comparative pass can swap the local 7B for a cloud model by configuration alone.

Messages are plain dicts in the common chat shape ({"role", "content", ...}). Helper
constructors below build the ones that need provider-specific fields; providers translate if
their wire format differs.
"""

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ModelError(Exception):
    """Every client failure (transport, timeout, HTTP status, bad response) is this one type."""


class ChatOptions(BaseModel):
    """Sampling and context settings. Sent explicitly on every call, never left to defaults."""

    num_ctx: int = Field(8192, ge=512, le=131072)
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    seed: int | None = None
    num_predict: int = Field(512, ge=1, le=8192, description="Max generated tokens")


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    # Set when the provider sent arguments as a string that did not parse as a JSON object.
    # `arguments` is then empty and the call counts as invalid.
    arguments_unparsed: str | None = None


class ChatResponse(BaseModel):
    model: str
    content: str
    tool_calls: list[ToolCall]
    done_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int  # wall clock measured by the client
    load_ms: int | None = None  # provider-reported, when available
    prompt_eval_ms: int | None = None
    eval_ms: int | None = None


class ModelClient(Protocol):
    model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: ChatOptions | None = None,
    ) -> ChatResponse:
        """One non-streaming chat turn. Raises ModelError, nothing else."""
        ...


# --------------------------------------------------------------------------- message helpers


def system(text: str) -> dict[str, Any]:
    return {"role": "system", "content": text}


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def assistant_tool_calls(calls: list[ToolCall], content: str = "") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls],
    }


def tool_result(name: str, payload: Any) -> dict[str, Any]:
    """A tool's output fed back to the model. `payload` is serialised as compact JSON."""
    content = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    return {"role": "tool", "tool_name": name, "content": content}
