"""Provenance tagging for tool output (Architecture §3, design rules).

Every value a tool hands the agent carries a trust label, so the defense layer (D1, P4) can
key on it later. Build this in now even though nothing consumes it until P4.

Classification rule: a field is TRUSTED only if nobody who can deploy or write to a workload
can set it. Everything else is UNTRUSTED.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Trust(StrEnum):
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class Tagged[T](BaseModel):
    """A value plus the trust label it was fetched under."""

    value: T
    trust: Trust


class ToolResult[D](BaseModel):
    """Envelope returned by every tool."""

    tool: str
    args: dict[str, Any]
    ok: bool
    data: D | None = None
    error: Tagged[str] | None = None
    truncated: bool = False


def trusted[T](value: T) -> Tagged[T]:
    return Tagged(value=value, trust=Trust.TRUSTED)


def untrusted[T](value: T) -> Tagged[T]:
    return Tagged(value=value, trust=Trust.UNTRUSTED)


def ok[D](tool: str, args: dict[str, Any], data: D, truncated: bool = False) -> ToolResult[D]:
    return ToolResult(tool=tool, args=args, ok=True, data=data, truncated=truncated)


def fail(tool: str, args: dict[str, Any], message: str) -> ToolResult[Any]:
    return ToolResult(tool=tool, args=args, ok=False, error=untrusted(message))
