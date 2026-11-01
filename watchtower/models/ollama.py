"""Ollama implementation of ModelClient, over plain httpx (no SDK to pin).

Wire format: POST /api/chat with stream=false. Every call sends `options` explicitly:
Ollama's default context window is smaller than ours and it truncates over-long prompts
silently, so a gate run on defaults would test a model that never saw the full prompt.
"""

import json
import time
from typing import Any

import httpx

from watchtower.models.client import ChatOptions, ChatResponse, ModelError, ToolCall
from watchtower.models.config import ModelConfig

MAX_ERROR_BODY = 300


def _ms(ns: Any) -> int | None:
    return int(ns) // 1_000_000 if isinstance(ns, int | float) else None


def parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls = []
    for raw in message.get("tool_calls") or []:
        fn = (raw or {}).get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str):
            continue
        args = fn.get("arguments")
        unparsed = None
        if isinstance(args, str):  # some models/providers send a JSON string
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                unparsed, args = fn.get("arguments"), {}
        elif args is None:
            args = {}
        elif not isinstance(args, dict):
            unparsed, args = json.dumps(args), {}
        calls.append(ToolCall(name=name, arguments=args, arguments_unparsed=unparsed))
    return calls


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        # trust_env=False: Ollama is a LAN/loopback service; a corporate or sandbox proxy from
        # the environment must not intercept it.
        self._http = httpx.Client(
            base_url=base_url, timeout=timeout_s, transport=transport, trust_env=False
        )

    @classmethod
    def from_config(cls, cfg: ModelConfig) -> "OllamaClient":
        return cls(cfg.base_url, cfg.model, cfg.timeout_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ http

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.TimeoutException:
            raise ModelError(f"timeout calling {path}") from None
        except httpx.HTTPError as e:
            raise ModelError(f"transport error calling {path}: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise ModelError(f"HTTP {resp.status_code} from {path}: {resp.text[:MAX_ERROR_BODY]}")
        try:
            body = resp.json()
        except ValueError:
            raise ModelError(f"non-JSON response from {path}") from None
        if not isinstance(body, dict):
            raise ModelError(f"unexpected response shape from {path}")
        return body

    # ------------------------------------------------------------------ ModelClient

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: ChatOptions | None = None,
    ) -> ChatResponse:
        options = options or ChatOptions()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": options.model_dump(exclude_none=True),
        }
        if tools:
            payload["tools"] = tools

        started = time.perf_counter()
        body = self._request("POST", "/api/chat", json=payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if body.get("error"):
            raise ModelError(f"model error: {str(body['error'])[:MAX_ERROR_BODY]}")
        message = body.get("message")
        if not isinstance(message, dict):
            raise ModelError("response has no message")
        return ChatResponse(
            model=str(body.get("model", self.model)),
            content=str(message.get("content") or ""),
            tool_calls=parse_tool_calls(message),
            done_reason=body.get("done_reason"),
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
            latency_ms=latency_ms,
            load_ms=_ms(body.get("load_duration")),
            prompt_eval_ms=_ms(body.get("prompt_eval_duration")),
            eval_ms=_ms(body.get("eval_duration")),
        )

    # ------------------------------------------------------------------ run metadata

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", "unknown"))

    def model_info(self) -> dict[str, Any]:
        """Tag, digest and quantisation of the configured model, from /api/tags."""
        for m in self._request("GET", "/api/tags").get("models", []):
            if m.get("name") == self.model or m.get("model") == self.model:
                details = m.get("details") or {}
                return {
                    "tag": m.get("name"),
                    "digest": m.get("digest"),
                    "size": m.get("size"),
                    "quantization": details.get("quantization_level"),
                    "parameter_size": details.get("parameter_size"),
                }
        raise ModelError(f"model {self.model!r} is not pulled on {self.base_url}")

    def residency(self) -> dict[str, Any] | None:
        """How much of the loaded model sits in VRAM (from /api/ps); None if not loaded."""
        for m in self._request("GET", "/api/ps").get("models", []):
            if m.get("name") == self.model or m.get("model") == self.model:
                size, vram = m.get("size") or 0, m.get("size_vram") or 0
                return {
                    "size": size,
                    "size_vram": vram,
                    "gpu_pct": round(100 * vram / size, 1) if size else None,
                    "context_length": m.get("context_length"),
                }
        return None
