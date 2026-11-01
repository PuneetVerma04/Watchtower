"""Gate metrics, verdict and markdown report from the SQLite rows of one run.

    python -m watchtower.eval.tool_calling.report [--run-id ID] [--out path.md]

Metric definitions (W4_TARGETS T3). "Call cases" are those expecting a specific tool (C1, C2,
C3, C5); a failed request counts as a miss in every rate except hallucination:
  structural_rate     call cases answered with a native tool call
  selection_accuracy  call cases whose first call is the expected tool
  arg_validity        call cases with a call, where that call passes the argument models
  arg_correctness     call cases with the expected tool AND the required argument values
  chaining_success    C3 cases passed
  restraint_success   C4 cases passed (no call when none is needed / nothing invented)
  recovery_success    C5 cases passed
  hallucination_rate  answered trials containing an unknown tool or an invented argument
  completion_rate     answered trials / planned trials

The report suggests a verdict from the pre-registered thresholds. The decision (GO, FALLBACK,
DESIGN CHANGE) is recorded by a person under Resolved decisions in the roadmap.
"""

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

from watchtower.eval.tool_calling.thresholds import LATENCY_FLAG_P50_MS, THRESHOLDS

# Extrapolation inputs for the P3 throughput line. Stated in the report, not hidden.
P3_CALLS_PER_AGENT_RUN = 10
P3_VECTORS, P3_TRIALS, P3_SCENARIOS = 8, 20, 12

CATEGORY_NAMES = {
    "C1": "selection",
    "C2": "arg fidelity",
    "C3": "chaining",
    "C4": "restraint",
    "C5": "recovery",
}


def load(db: sqlite3.Connection, run_id: str | None = None) -> tuple[dict, list[dict]]:
    db.row_factory = sqlite3.Row
    if run_id is None:
        row = db.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("no runs recorded")
        run_id = row["run_id"]
    run = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise SystemExit(f"unknown run {run_id}")
    rows = [dict(r) for r in db.execute("SELECT * FROM trials WHERE run_id = ?", (run_id,))]
    for r in rows:
        r["expected"] = json.loads(r["expected"])
    return dict(run), rows


def _rate(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if r[key] is not None]
    return sum(values) / len(values) if values else None


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def metrics(rows: list[dict], n_planned: int | None = None) -> dict[str, float | None]:
    call_rows = [r for r in rows if r["expected"]["kind"] == "call"]
    answered = [r for r in rows if not r["errored"]]
    by_cat = {c: [r for r in rows if r["category"] == c] for c in CATEGORY_NAMES}
    planned = n_planned if n_planned is not None else len(rows)
    return {
        "structural_rate": _rate(call_rows, "structural_ok"),
        "selection_accuracy": _rate(call_rows, "selection_ok"),
        "arg_validity": _rate([r for r in call_rows if r["n_calls"]], "args_valid"),
        "arg_correctness": _rate(call_rows, "args_correct"),
        "chaining_success": _rate(by_cat["C3"], "passed"),
        "restraint_success": _rate(by_cat["C4"], "passed"),
        "recovery_success": _rate(by_cat["C5"], "passed"),
        "hallucination_rate": _rate(answered, "hallucinated"),
        "completion_rate": len(answered) / planned if planned else None,
    }


def per_seed(rows: list[dict]) -> dict[str, tuple[float, float, float, float] | None]:
    """mean, sd, min, max of each metric across seeds (a single-seed run has sd 0)."""
    seeds = sorted({r["seed"] for r in rows})
    by_seed = [metrics([r for r in rows if r["seed"] == s]) for s in seeds]
    out: dict[str, tuple[float, float, float, float] | None] = {}
    for name in by_seed[0] if by_seed else {}:
        vals = [m[name] for m in by_seed if m[name] is not None]
        out[name] = (
            (statistics.mean(vals), statistics.pstdev(vals), min(vals), max(vals)) if vals else None
        )
    return out


def verdict(m: dict[str, float | None]) -> tuple[str, list[str]]:
    completion = next(t for t in THRESHOLDS if t.metric == "completion_rate")
    if not completion.met(m["completion_rate"]):
        return "INVALID: incomplete run (re-run the whole batch, do not patch)", ["completion_rate"]
    failed = [t.metric for t in THRESHOLDS if t.hard and not t.met(m[t.metric])]
    if failed:
        return "NO-GO: hard thresholds missed", failed
    return "GO: all hard thresholds met", []


def verdict_line(db: sqlite3.Connection, run_id: str) -> str:
    run, rows = load(db, run_id)
    label, failed = verdict(metrics(rows, run["n_planned"]))
    return label + (f" ({', '.join(failed)})" if failed else "")


# --------------------------------------------------------------------------- markdown


def _f(x: float | None, pct: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{100 * x:.1f}%" if pct else f"{x:.0f}"


def _cell(text: Any, limit: int = 160) -> str:
    """Model output is untrusted text: one line, no table-breaking characters."""
    s = " ".join(str(text).split())
    s = s.replace("|", "\\|").replace("`", "'")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _got(row: dict) -> str:
    if row["errored"]:
        return f"request failed: {row['error']}"
    resp = json.loads(row["response"])
    if resp["tool_calls"]:
        return "; ".join(
            f"{c['name']}({json.dumps(c['arguments'], separators=(',', ':'))})"
            for c in resp["tool_calls"]
        )
    tag = "[tool call written as text] " if row["text_tool_call"] else "[no call] "
    return tag + resp["content"]


def _expected(exp: dict) -> str:
    if exp["kind"] != "call":
        return exp["kind"]
    return f"{exp['tool']}({json.dumps(exp['args'], separators=(',', ':'))})"


def render(db: sqlite3.Connection, run_id: str | None = None) -> str:
    run, rows = load(db, run_id)
    m = metrics(rows, run["n_planned"])
    spread = per_seed(rows) if rows else {}
    label, failed = verdict(m)
    answered = [r for r in rows if not r["errored"]]
    latencies = [r["latency_ms"] for r in answered if r["latency_ms"] is not None]
    p50, p95 = _pct(latencies, 0.5), _pct(latencies, 0.95)
    seeds = json.loads(run["seeds"] or "[]")

    out = [
        "# W4 tool-calling gate",
        "",
        f"**Suggested verdict: {label}**" + (f" — missed: {', '.join(failed)}" if failed else ""),
        "",
        "The decision (GO / FALLBACK / DESIGN CHANGE) is recorded under *Resolved decisions* in "
        "the roadmap, not here.",
        "",
        "## Setup",
        "",
        "| | |",
        "|---|---|",
        f"| Run | `{run['run_id']}` ({run['started_at']} → {run['finished_at']}) |",
        f"| Code | `{run['git_commit']}` |",
        f"| Model | `{run['model']}` {run['quantization'] or ''}, digest `{run['model_digest']}` |",
        f"| Ollama | {run['ollama_version']} at `{run['base_url']}` |",
        f"| Server settings | {run['server_note'] or '(none recorded)'} |",
        f"| Sampling | temperature {run['temperature']}, seeds {seeds[0] if seeds else '?'}–"
        f"{seeds[-1] if seeds else '?'} ({len(seeds)}), num_ctx {run['num_ctx']} |",
        f"| VRAM residency | {run['gpu_pct']}% GPU {run['residency'] or ''} |",
        f"| Trials | {len(answered)} answered / {run['n_planned']} planned |",
        "",
        "## Gate metrics",
        "",
        "Pooled over all trials; spread is across seeds.",
        "",
        "| Metric | Observed | Per-seed mean ± sd [min–max] | Threshold | Met |",
        "|---|---|---|---|---|",
    ]
    for t in THRESHOLDS:
        s = spread.get(t.metric)
        sp = (
            f"{100 * s[0]:.1f}% ± {100 * s[1]:.1f} [{100 * s[2]:.0f}–{100 * s[3]:.0f}]"
            if s and t.metric != "completion_rate"
            else "—"
        )
        met = t.met(m[t.metric])
        mark = "—" if not t.hard else ("✅" if met else "❌")
        thr = f"{t.op} {100 * t.value:.0f}%" if t.hard else "report only"
        out.append(f"| {t.metric} | {_f(m[t.metric])} | {sp} | {thr} | {mark} |")

    out += [
        "",
        "## By category",
        "",
        "| Category | Trials | Passed | Native call | Right tool | Right args | Hallucinated |",
        "|---|---|---|---|---|---|---|",
    ]
    for code, name in CATEGORY_NAMES.items():
        cat = [r for r in rows if r["category"] == code]
        if not cat:
            continue
        out.append(
            f"| {code} {name} | {len(cat)} | {_f(_rate(cat, 'passed'))} | "
            f"{_f(_rate(cat, 'structural_ok'))} | {_f(_rate(cat, 'selection_ok'))} | "
            f"{_f(_rate(cat, 'args_correct'))} | {_f(_rate(cat, 'hallucinated'))} |"
        )

    text_calls = sum(r["text_tool_call"] for r in answered)
    truncated = sum(1 for r in answered if r["done_reason"] == "length")
    out += [
        "",
        "## Cost and capacity",
        "",
        f"- **Tier 0 schemas alone:** {run['schema_tokens']} prompt tokens "
        f"({_f(run['schema_tokens'] / run['num_ctx'] if run['schema_tokens'] else None)} of "
        f"num_ctx {run['num_ctx']}).",
        f"- **Largest case prompt:** {run['max_prompt_tokens']} tokens (`{run['max_prompt_case']}`)"
        + (
            " ⚠️ close to num_ctx: check for silent truncation"
            if run["max_prompt_tokens"] and run["max_prompt_tokens"] > 0.9 * run["num_ctx"]
            else ""
        )
        + ".",
        f"- **Latency per call:** p50 {p50} ms, p95 {p95} ms"
        + (
            f" ⚠️ p50 above the {LATENCY_FLAG_P50_MS} ms flag"
            if p50 and p50 > LATENCY_FLAG_P50_MS
            else ""
        )
        + ".",
        "- Per-trial prompt token counts exclude the KV-cached prefix Ollama reuses between "
        "calls; the two figures above are cache-busted and complete.",
        f"- Tool calls written as text instead of `tool_calls`: {text_calls}. "
        f"Responses cut off at num_predict: {truncated}.",
    ]
    if p50:
        calls = P3_CALLS_PER_AGENT_RUN * P3_VECTORS * P3_TRIALS * P3_SCENARIOS
        hours = calls * p50 / 3_600_000
        out.append(
            f"- **P3 throughput estimate:** {P3_CALLS_PER_AGENT_RUN} calls/agent run × "
            f"{P3_VECTORS} vectors × {P3_TRIALS} trials × {P3_SCENARIOS} scenarios = {calls:,} "
            f"calls × p50 ≈ **{hours:.0f} h** of inference per full undefended pass (P4 "
            "multiplies this by every defense configuration)."
        )

    fails = [r for r in rows if not r["passed"]]
    out += ["", "## Failures", ""]
    if not fails:
        out.append("None.")
    else:
        out += [
            "Up to three per case, verbatim (model output, untrusted).",
            "",
            "| Case | Seed | Expected | Got |",
            "|---|---|---|---|",
        ]
        shown: dict[str, int] = {}
        for r in sorted(fails, key=lambda r: (r["case_id"], r["seed"])):
            if shown.get(r["case_id"], 0) >= 3:
                continue
            shown[r["case_id"]] = shown.get(r["case_id"], 0) + 1
            out.append(
                f"| {r['case_id']} | {r['seed']} | `{_cell(_expected(r['expected']))}` "
                f"| {_cell(_got(r))} |"
            )
        out += [
            "",
            "Failures per case: "
            + ", ".join(
                f"{cid} {sum(1 for r in fails if r['case_id'] == cid)}"
                for cid in sorted({r["case_id"] for r in fails})
            ),
        ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    from watchtower.eval.tool_calling.run import DEFAULT_DB

    p = argparse.ArgumentParser(prog="python -m watchtower.eval.tool_calling.report")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--run-id")
    p.add_argument("--out", help="write here instead of stdout")
    a = p.parse_args(argv)
    db = sqlite3.connect(a.db)
    text = render(db, a.run_id)
    if a.out:
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
