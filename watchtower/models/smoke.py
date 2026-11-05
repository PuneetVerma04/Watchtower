"""Smoke check: can WSL reach the model, does it emit a native tool call, does it fit in VRAM?

    python -m watchtower.models.smoke [--base-url URL] [--model TAG] [--num-ctx 8192]

Sends the same tool-bearing request twice (cold, then warm) with the full Tier 0 schemas, and
prints the endpoint, model digest, tool call, token counts, timings and GPU residency.
Exit 0 only if the warm call produced a native tool call.
"""

import argparse
import sys

from watchtower.models.client import ChatOptions, ModelError, system, user
from watchtower.models.config import ModelConfigError, from_env
from watchtower.models.ollama import OllamaClient
from watchtower.tools.registry import tool_schemas

PROMPT = "List the pods that belong to the worker app (label app=worker)."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m watchtower.models.smoke")
    p.add_argument("--base-url", help="overrides WATCHTOWER_MODEL_BASE_URL (default: auto)")
    p.add_argument("--model", help="overrides WATCHTOWER_MODEL")
    p.add_argument("--num-ctx", type=int, default=8192)
    a = p.parse_args(argv)

    try:
        cfg = from_env(base_url=a.base_url, model=a.model)
    except ModelConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    print(f"endpoint   {cfg.base_url}")
    with OllamaClient.from_config(cfg) as client:
        try:
            print(f"ollama     {client.version()}")
            info = client.model_info()
            print(f"model      {info['tag']}  {info['quantization']}  digest {info['digest']}")
            messages = [
                system("You investigate Kubernetes incidents using the provided tools."),
                user(PROMPT),
            ]
            options = ChatOptions(num_ctx=a.num_ctx, temperature=0.0, seed=0)
            last = None
            for label in ("cold", "warm"):
                r = client.chat(messages, tools=tool_schemas(), options=options)
                calls = ", ".join(f"{c.name}({c.arguments})" for c in r.tool_calls) or "-"
                print(
                    f"{label:<10} {r.latency_ms} ms (load {r.load_ms}, prompt {r.prompt_eval_ms}"
                    f", gen {r.eval_ms})  prompt_tokens={r.prompt_tokens}"
                    f"  completion_tokens={r.completion_tokens}"
                )
                print(f"           tool_calls: {calls}")
                if not r.tool_calls and r.content:
                    print(f"           content: {r.content[:300]!r}")
                last = r
            res = client.residency()
            if res:
                print(
                    f"residency  {res['gpu_pct']}% GPU  (size {res['size']}, vram "
                    f"{res['size_vram']}, ctx {res['context_length']})"
                )
        except ModelError as e:
            print(f"model error: {e}", file=sys.stderr)
            return 1

    ok = last is not None and bool(last.tool_calls)
    print("OK: native tool call" if ok else "FAIL: no native tool call")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
