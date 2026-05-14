"""R runner — uses backend.router.should_engage_council to pick SOLO vs council."""
from __future__ import annotations
import time
import sys
sys.path.insert(0, ".")

from backend import config as backend_config
from backend.openrouter import query_model
from backend.router import should_engage_council
from backend.council import run_full_council
from evals.runners._base import RunResult


async def run(sweep_id: str, q: dict) -> RunResult:
    t0 = time.time()
    routing = await should_engage_council(q["question"])

    if routing.use_council:
        envelope = await run_full_council(q["question"])
        cost, in_tok, out_tok, n_calls = 0.0, 0, 0, 0
        for entry in envelope.get("stage1", []) + envelope.get("stage2", []):
            u = entry.get("usage") or {}
            cost += u.get("cost", 0.0)
            in_tok += u.get("prompt_tokens", 0) or 0
            out_tok += u.get("completion_tokens", 0) or 0
            if entry.get("status") == "ok": n_calls += 1
        s3 = envelope.get("stage3", {})
        u3 = s3.get("usage") or {}
        cost += u3.get("cost", 0.0)
        in_tok += u3.get("prompt_tokens", 0) or 0
        out_tok += u3.get("completion_tokens", 0) or 0
        if s3.get("status") == "ok": n_calls += 1
        response = s3.get("response")
        error = s3.get("error")
    else:
        content, usage, err = await query_model(
            backend_config.CHAIRMAN_MODEL,
            [{"role": "user", "content": q["question"]}],
            max_tokens=backend_config.CHAIRMAN_MAX_TOKENS,
        )
        cost = (usage or {}).get("cost", 0.0)
        in_tok = (usage or {}).get("prompt_tokens", 0)
        out_tok = (usage or {}).get("completion_tokens", 0)
        n_calls = 1
        response = content
        error = err.to_dict() if err else None
        envelope = {"routing": routing.to_dict(), "solo_response": content}

    elapsed = time.time() - t0
    return RunResult(
        sweep_id=sweep_id,
        condition="R",
        question_id=q["id"],
        question=q["question"],
        category=q.get("category", "unknown"),
        response=response,
        routing_used_council=routing.use_council,
        routing_reason=routing.reason,
        llm_calls=n_calls + 1,
        prompt_tokens=in_tok,
        completion_tokens=out_tok,
        cost_usd=cost,
        latency_s=round(elapsed, 3),
        error=error,
        raw_envelope=envelope,
    )
