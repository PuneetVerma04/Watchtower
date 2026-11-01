"""Run the W4 tool-calling gate against a live model.

    python -m watchtower.eval.tool_calling.run [--server-note "FA=1 KV=q8_0"] [--num-ctx 8192]
        [--seeds 10] [--cases c1-events,C3] [--base-url URL] [--model TAG]

Needs only Ollama: every scripted tool call replays the committed fixtures, so the cluster can
stay down (Conventions §1 memory budget). Seeds are the outer loop, so an interrupted run still
covers every case evenly. One SQLite row per call in results/tool_calling.sqlite (gitignored);
the markdown report goes to results/reports/ (committed).

Before the trials the runner records: Ollama version, model tag + digest, prompt tokens taken
by the Tier 0 schemas alone, the largest case prompt, and VRAM residency.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watchtower.eval.tool_calling import report
from watchtower.eval.tool_calling.cases import CASES, SYSTEM_PROMPT, Call, Case, User
from watchtower.eval.tool_calling.score import score
from watchtower.eval.tool_calling.thresholds import NUM_CTX, SEEDS, TEMPERATURE
from watchtower.models.client import (
    ChatOptions,
    ChatResponse,
    ModelClient,
    ModelError,
    ToolCall,
    assistant_tool_calls,
    system,
    tool_result,
    user,
)
from watchtower.tools.backend import Backend
from watchtower.tools.capture import ReplayBackend, load_json
from watchtower.tools.registry import call_tool, tool_schemas

REPO = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO / "tests" / "tools" / "fixtures"
DEFAULT_DB = REPO / "results" / "tool_calling.sqlite"
DEFAULT_REPORT_DIR = REPO / "results" / "reports"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, git_commit TEXT,
    model TEXT, model_digest TEXT, quantization TEXT, ollama_version TEXT, base_url TEXT,
    temperature REAL, num_ctx INTEGER, seeds TEXT, case_ids TEXT, server_note TEXT,
    schema_tokens INTEGER, max_prompt_tokens INTEGER, max_prompt_case TEXT,
    gpu_pct REAL, residency TEXT, n_planned INTEGER
);
CREATE TABLE IF NOT EXISTS trials (
    run_id TEXT, case_id TEXT, category TEXT, seed INTEGER, started_at TEXT,
    model TEXT, model_digest TEXT,
    expected TEXT, response TEXT, error TEXT,
    errored INTEGER, n_calls INTEGER, called TEXT, text_tool_call INTEGER,
    structural_ok INTEGER, selection_ok INTEGER, args_valid INTEGER, args_correct INTEGER,
    hallucinated INTEGER, passed INTEGER,
    prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER, load_ms INTEGER,
    prompt_eval_ms INTEGER, eval_ms INTEGER, done_reason TEXT,
    PRIMARY KEY (run_id, case_id, seed)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_db(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# --------------------------------------------------------------------------- conversations


def build_messages(case: Case, backend: Backend, system_prompt: str = SYSTEM_PROMPT) -> list[dict]:
    """The conversation up to the point the model must act. Call turns run on replay."""
    messages = [system(system_prompt)]
    for turn in case.turns:
        if isinstance(turn, User):
            messages.append(user(turn.text))
        else:
            assert isinstance(turn, Call)
            result = call_tool(backend, turn.tool, turn.args)
            messages.append(assistant_tool_calls([ToolCall(name=turn.tool, arguments=turn.args)]))
            messages.append(
                tool_result(turn.tool, result.model_dump(mode="json", exclude_none=True))
            )
    return messages


def fixture_backends(names: Iterable[str]) -> dict[str, Backend]:
    return {n: ReplayBackend(load_json(FIXTURES_DIR / f"{n}.json")) for n in set(names)}


def select_cases(spec: str | None) -> list[Case]:
    if not spec:
        return list(CASES)
    wanted = {s.strip() for s in spec.split(",") if s.strip()}
    chosen = [c for c in CASES if c.id in wanted or c.category.value in wanted]
    unknown = wanted - {c.id for c in chosen} - {c.category.value for c in chosen}
    if unknown:
        raise SystemExit(f"unknown case ids/categories: {sorted(unknown)}")
    return chosen


# --------------------------------------------------------------------------- preflight


def _nonce_system(prompt: str) -> str:
    # Ollama reuses the KV cache for a repeated prompt prefix and then reports only the newly
    # evaluated tokens. A unique first line forces a full evaluation, so counts are complete.
    return f"[run {uuid.uuid4().hex[:8]}]\n{prompt}"


def schema_token_cost(client: ModelClient, options: ChatOptions) -> int | None:
    """Prompt tokens added by the Tier 0 schemas (same prompt with and without tools)."""
    probe = ChatOptions(**{**options.model_dump(), "num_predict": 1})
    msg = [user("Reply with the word ok.")]
    base = client.chat([system(_nonce_system(SYSTEM_PROMPT)), *msg], options=probe)
    full = client.chat(
        [system(_nonce_system(SYSTEM_PROMPT)), *msg], tools=tool_schemas(), options=probe
    )
    if base.prompt_tokens is None or full.prompt_tokens is None:
        return None
    return full.prompt_tokens - base.prompt_tokens


def largest_prompt(
    client: ModelClient, cases: list[Case], convos: dict[str, list[dict]], options: ChatOptions
) -> tuple[str, int | None]:
    """Full prompt token count of the longest case (by characters), cache-busted."""
    case = max(cases, key=lambda c: len(json.dumps(convos[c.id])))
    messages = list(convos[case.id])
    messages[0] = system(_nonce_system(messages[0]["content"]))
    probe = ChatOptions(**{**options.model_dump(), "num_predict": 1})
    return case.id, client.chat(messages, tools=tool_schemas(), options=probe).prompt_tokens


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "watchtower"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip() or None
    return f"{sha}-dirty" if sha and dirty.stdout.strip() else sha


# --------------------------------------------------------------------------- run


def run_gate(
    client: ModelClient,
    db: sqlite3.Connection,
    *,
    cases: list[Case],
    seeds: Iterable[int] = SEEDS,
    temperature: float = TEMPERATURE,
    num_ctx: int = NUM_CTX,
    server_note: str = "",
    run_id: str | None = None,
    meta: dict[str, Any] | None = None,
    preflight: bool = True,
    log: Callable[[str], None] = print,
) -> str:
    """Execute cases x seeds; returns the run_id. Never raises on a single failed request."""
    seeds = list(seeds)
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta = dict(meta or {})
    backends = fixture_backends(c.fixture for c in cases)
    convos = {c.id: build_messages(c, backends[c.fixture]) for c in cases}
    tools = tool_schemas()
    base_options = ChatOptions(num_ctx=num_ctx, temperature=temperature)

    if preflight:
        log("preflight: warm-up, schema token cost, largest prompt")
        try:
            client.chat(convos[cases[0].id], tools=tools, options=base_options)  # load model
            meta["schema_tokens"] = schema_token_cost(client, base_options)
            meta["max_prompt_case"], meta["max_prompt_tokens"] = largest_prompt(
                client, cases, convos, base_options
            )
        except ModelError as e:
            log(f"preflight failed: {e}")
            raise
        residency = getattr(client, "residency", lambda: None)()
        meta["residency"] = json.dumps(residency) if residency else None
        meta["gpu_pct"] = residency.get("gpu_pct") if residency else None

    db.execute(
        """INSERT INTO runs (run_id, started_at, git_commit, model, model_digest, quantization,
               ollama_version, base_url, temperature, num_ctx, seeds, case_ids, server_note,
               schema_tokens, max_prompt_tokens, max_prompt_case, gpu_pct, residency, n_planned)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            _now(),
            meta.get("git_commit"),
            client.model,
            meta.get("model_digest"),
            meta.get("quantization"),
            meta.get("ollama_version"),
            meta.get("base_url"),
            temperature,
            num_ctx,
            json.dumps(seeds),
            json.dumps([c.id for c in cases]),
            server_note,
            meta.get("schema_tokens"),
            meta.get("max_prompt_tokens"),
            meta.get("max_prompt_case"),
            meta.get("gpu_pct"),
            meta.get("residency"),
            len(seeds) * len(cases),
        ),
    )
    db.commit()

    total, i = len(seeds) * len(cases), 0
    try:
        for seed in seeds:
            options = ChatOptions(num_ctx=num_ctx, temperature=temperature, seed=seed)
            for case in cases:
                i += 1
                started = _now()
                response: ChatResponse | None = None
                error = None
                try:
                    response = client.chat(convos[case.id], tools=tools, options=options)
                except ModelError as e:
                    error = str(e)
                s = score(case, response)
                _insert_trial(
                    db, run_id, case, seed, started, client.model, meta, response, error, s
                )
                verdict = "ERROR" if error else ("pass" if s.passed else "FAIL")
                latency = f"{response.latency_ms} ms" if response else error
                log(f"[{i:>4}/{total}] seed {seed} {case.id:<28} {verdict:<5} {latency}")
    except KeyboardInterrupt:
        log("interrupted: the run is recorded as incomplete")
    finally:
        db.execute("UPDATE runs SET finished_at = ? WHERE run_id = ?", (_now(), run_id))
        db.commit()
    return run_id


def _b(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _insert_trial(db, run_id, case, seed, started, model, meta, response, error, s) -> None:
    db.execute(
        """INSERT OR REPLACE INTO trials VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            case.id,
            case.category.value,
            seed,
            started,
            model,
            meta.get("model_digest"),
            json.dumps(
                {"kind": case.expect.kind.value, "tool": case.expect.tool, "args": case.expect.args}
            ),
            response.model_dump_json() if response else None,
            error,
            int(s.errored),
            s.n_calls,
            s.called,
            int(s.text_tool_call),
            _b(s.structural_ok),
            _b(s.selection_ok),
            _b(s.args_valid),
            _b(s.args_correct),
            int(s.hallucinated),
            int(s.passed),
            response.prompt_tokens if response else None,
            response.completion_tokens if response else None,
            response.latency_ms if response else None,
            response.load_ms if response else None,
            response.prompt_eval_ms if response else None,
            response.eval_ms if response else None,
            response.done_reason if response else None,
        ),
    )
    db.commit()


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    from watchtower.models.config import ModelConfigError, from_env
    from watchtower.models.ollama import OllamaClient

    p = argparse.ArgumentParser(prog="python -m watchtower.eval.tool_calling.run")
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--seeds", type=int, default=len(SEEDS), help="seeds 0..N-1")
    p.add_argument("--temperature", type=float, default=TEMPERATURE)
    p.add_argument("--num-ctx", type=int, default=NUM_CTX)
    p.add_argument("--cases", help="comma-separated case ids and/or categories (C1..C5)")
    p.add_argument("--server-note", default="", help='Ollama server settings, e.g. "KV=q8_0"')
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    a = p.parse_args(argv)

    try:
        cfg = from_env(base_url=a.base_url, model=a.model)
    except ModelConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    cases = select_cases(a.cases)
    db = open_db(a.db)
    with OllamaClient.from_config(cfg) as client:
        try:
            info = client.model_info()
            meta = {
                "git_commit": _git_commit(),
                "base_url": cfg.base_url,
                "ollama_version": client.version(),
                "model_digest": info["digest"],
                "quantization": info["quantization"],
            }
            run_id = run_gate(
                client,
                db,
                cases=cases,
                seeds=range(a.seeds),
                temperature=a.temperature,
                num_ctx=a.num_ctx,
                server_note=a.server_note,
                meta=meta,
            )
        except ModelError as e:
            print(f"model error: {e}", file=sys.stderr)
            return 1

    full = not a.cases and a.seeds == len(SEEDS)
    out = Path(a.report_dir) / (
        "w4_tool_calling.md" if full else f"w4_tool_calling_partial_{run_id}.md"
    )
    text = report.render(db, run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"\nrun {run_id} -> {out}")
    print(report.verdict_line(db, run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
