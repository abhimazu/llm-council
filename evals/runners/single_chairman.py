"""C0 runner — chairman model called once, no council. Baseline for council-uplift."""
from __future__ import annotations
import time
import sys
sys.path.insert(0, ".")

from backend import config as backend_config
from backend.openrouter import query_model
from evals.runners._base import RunResult


async def run(sweep_id: str, q: dict) -> RunResult:
    t0 = time.time()
    content, usage, err = await query_model(
        backend_config.CHAIRMAN_MODEL,
        [{"role": "user", "content": q["question"]}],
        max_tokens=backend_config.CHAIRMAN_MAX_TOKENS,
    )
    elapsed = time.time() - t0
    return RunResult(
        sweep_id=sweep_id,
        condition="C0",
        question_id=q["id"],
        question=q["question"],
        category=q.get("category", "unknown"),
        response=content,
        llm_calls=1,
        prompt_tokens=(usage or {}).get("prompt_tokens", 0),
        completion_tokens=(usage or {}).get("completion_tokens", 0),
        cost_usd=(usage or {}).get("cost", 0.0),
        latency_s=round(elapsed, 3),
        error=err.to_dict() if err else None,
    )
