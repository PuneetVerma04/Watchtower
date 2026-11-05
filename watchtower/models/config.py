"""Model endpoint configuration, from the environment only (Conventions §1.1: never hardcoded).

WATCHTOWER_MODEL_BASE_URL   explicit URL, e.g. http://localhost:11434 (WSL mirrored mode), or
                            `auto` (default): the WSL default gateway, i.e. the Windows host,
                            on port 11434. In WSL's NAT mode the gateway IP can change across
                            `wsl --shutdown`, which is why it is resolved at startup.
WATCHTOWER_MODEL            model tag (default qwen2.5:7b-instruct)
WATCHTOWER_MODEL_TIMEOUT_S  per-request timeout (default 300; cold loads take ~25 s on the
                            dev laptop, a CPU/GPU split makes long prompts slow)
"""

import os
import socket
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "qwen2.5:7b-instruct"
OLLAMA_PORT = 11434
PROC_ROUTE = Path("/proc/net/route")


class ModelConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    timeout_s: float


def default_gateway(route_table: str | None = None) -> str | None:
    """IPv4 default gateway from /proc/net/route (no `ip` binary or subprocess needed)."""
    if route_table is None:
        try:
            route_table = PROC_ROUTE.read_text()
        except OSError:
            return None
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":  # destination 0.0.0.0 = default route
            continue
        try:
            gateway = int(fields[2], 16)
        except ValueError:
            continue
        if gateway:
            return socket.inet_ntoa(struct.pack("<L", gateway))  # the table is little-endian
    return None


def resolve_base_url(value: str | None, route_table: str | None = None) -> str:
    value = (value or "auto").strip()
    if value.lower() != "auto":
        if not value.startswith(("http://", "https://")):
            raise ModelConfigError(f"base URL must start with http:// or https://: {value!r}")
        return value.rstrip("/")
    gateway = default_gateway(route_table)
    if gateway is None:
        raise ModelConfigError(
            "WATCHTOWER_MODEL_BASE_URL=auto but no default gateway was found; set it explicitly"
        )
    return f"http://{gateway}:{OLLAMA_PORT}"


def from_env(
    env: Mapping[str, str] | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> ModelConfig:
    """Build the config; explicit arguments (CLI flags) win over the environment."""
    env = os.environ if env is None else env
    try:
        timeout_s = float(env.get("WATCHTOWER_MODEL_TIMEOUT_S", "300"))
    except ValueError:
        raise ModelConfigError("WATCHTOWER_MODEL_TIMEOUT_S must be a number") from None
    return ModelConfig(
        base_url=resolve_base_url(base_url or env.get("WATCHTOWER_MODEL_BASE_URL")),
        model=model or env.get("WATCHTOWER_MODEL") or DEFAULT_MODEL,
        timeout_s=timeout_s,
    )
