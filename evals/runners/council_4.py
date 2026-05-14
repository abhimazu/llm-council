"""C2 runner — 4-member council + chairman (branch default). Bypasses smart router."""
from __future__ import annotations
import time
import sys
sys.path.insert(0, ".")

from backend.council import run_full_council
from evals.runners._base import RunResult


async def run(sweep_id: str, q: dict) -> RunResult:
    t0 = time.time()
    envelope = await run_full_council(q["question"])
    elapsed = time.time() - t0

    cost, in_tok, out_tok, n_calls = 0.0, 0, 0, 0
    for entry in envelope.get("stage1", []) + envelope.get("stage2", []):
        u = entry.get("usage") or {}
        cost += u.get("cost", 0.0)
        in_tok += u.get("prompt_tokens", 0) or 0
        out_tok += u.get("completion_tokens", 0) or 0
        if entry.get("status") == "ok":
            n_calls += 1
    s3 = envelope.get("stage3", {})
    u3 = s3.get("usage") or {}
    cost += u3.get("cost", 0.0)
    in_tok += u3.get("prompt_tokens", 0) or 0
    out_tok += u3.get("completion_tokens", 0) or 0
    if s3.get("status") == "ok":
        n_calls += 1

    return RunResult(
        sweep_id=sweep_id,
        condition="C2",
        question_id=q["id"],
        question=q["question"],
        category=q.get("category", "unknown"),
        response=s3.get("response"),
        llm_calls=n_calls,
        prompt_tokens=in_tok,
        completion_tokens=out_tok,
        cost_usd=cost,
        latency_s=round(elapsed, 3),
        error=s3.get("error"),
        raw_envelope=envelope,
    )
