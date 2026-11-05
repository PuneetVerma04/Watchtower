"""OllamaClient against a mocked transport: no Ollama needed."""

import json

import httpx
import pytest

from watchtower.models.client import ChatOptions, ModelError, user
from watchtower.models.ollama import OllamaClient, parse_tool_calls


def _client(handler) -> OllamaClient:
    return OllamaClient(
        "http://ollama.test:11434",
        "qwen2.5:7b-instruct",
        5.0,
        transport=httpx.MockTransport(handler),
    )


def _chat_body(message: dict, **extra) -> dict:
    return {
        "model": "qwen2.5:7b-instruct",
        "message": {"role": "assistant", "content": "", **message},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 1234,
        "eval_count": 20,
        "load_duration": 25_000_000_000,
        "prompt_eval_duration": 2_000_000_000,
        "eval_duration": 500_000_000,
        **extra,
    }


def test_chat_sends_options_explicitly_and_parses_tool_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=_chat_body(
                {
                    "tool_calls": [
                        {"function": {"name": "get_pods", "arguments": {"selector": "app=worker"}}}
                    ]
                }
            ),
        )

    r = _client(handler).chat(
        [user("hi")],
        tools=[{"type": "function"}],
        options=ChatOptions(num_ctx=8192, temperature=0.2, seed=3),
    )
    assert seen["stream"] is False
    assert seen["options"] == {"num_ctx": 8192, "temperature": 0.2, "seed": 3, "num_predict": 512}
    assert seen["tools"] == [{"type": "function"}]
    assert [(c.name, c.arguments) for c in r.tool_calls] == [
        ("get_pods", {"selector": "app=worker"})
    ]
    assert (r.prompt_tokens, r.completion_tokens) == (1234, 20)
    assert (r.load_ms, r.prompt_eval_ms, r.eval_ms) == (25000, 2000, 500)
    assert r.done_reason == "stop"


def test_no_tools_key_when_no_tools():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_body({"content": "ok"}))

    r = _client(handler).chat([user("hi")])
    assert "tools" not in seen
    assert r.content == "ok" and r.tool_calls == []


def test_string_arguments_are_parsed_and_garbage_is_kept_unparsed():
    calls = parse_tool_calls(
        {
            "tool_calls": [
                {"function": {"name": "get_pods", "arguments": '{"name": "redis-0"}'}},
                {"function": {"name": "get_events", "arguments": "{not json"}},
                {"function": {"name": "get_service", "arguments": [1, 2]}},
                {"function": {"name": "get_metrics"}},
                {"function": {"arguments": {}}},  # no name: dropped
            ]
        }
    )
    assert [c.name for c in calls] == ["get_pods", "get_events", "get_service", "get_metrics"]
    assert calls[0].arguments == {"name": "redis-0"} and calls[0].arguments_unparsed is None
    assert calls[1].arguments == {} and calls[1].arguments_unparsed == "{not json"
    assert calls[2].arguments == {} and calls[2].arguments_unparsed == "[1, 2]"
    assert calls[3].arguments == {}


@pytest.mark.parametrize(
    "handler, fragment",
    [
        (lambda r: httpx.Response(404, text='{"error":"model not found"}'), "HTTP 404"),
        (lambda r: httpx.Response(200, text="<html>"), "non-JSON"),
        (lambda r: httpx.Response(200, json={"error": "out of memory"}), "out of memory"),
        (lambda r: httpx.Response(200, json={"done": True}), "no message"),
    ],
)
def test_failures_become_model_error(handler, fragment):
    with pytest.raises(ModelError, match=fragment):
        _client(handler).chat([user("hi")])


def test_transport_errors_become_model_error():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ModelError, match="timeout"):
        _client(timeout).chat([user("hi")])
    with pytest.raises(ModelError, match="ConnectError"):
        _client(refused).chat([user("hi")])


def test_model_info_and_residency():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "digest": "845dbda0ea48",
                            "size": 4683087332,
                            "details": {"quantization_level": "Q4_K_M", "parameter_size": "7.6B"},
                        }
                    ]
                },
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5:7b-instruct",
                            "size": 5409058322,
                            "size_vram": 4104734637,
                            "context_length": 8192,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"version": "0.40.13"})

    c = _client(handler)
    assert c.version() == "0.40.13"
    info = c.model_info()
    assert info["digest"] == "845dbda0ea48" and info["quantization"] == "Q4_K_M"
    res = c.residency()
    assert res["gpu_pct"] == 75.9 and res["context_length"] == 8192  # the 1 Nov observation


def test_model_info_errors_when_model_not_pulled():
    c = _client(lambda r: httpx.Response(200, json={"models": []}))
    with pytest.raises(ModelError, match="not pulled"):
        c.model_info()
    assert c.residency() is None
