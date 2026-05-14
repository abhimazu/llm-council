"""Factual answer grader — exact-match against expected list, plus LLM judge fallback.

Two-stage grading: first try string-normalized exact match against the
``expected`` array in the dataset. If no exact match, send to a judge LLM
with a calibrated prompt asking whether the response is semantically
equivalent to any of the expected answers.
"""
from __future__ import annotations
import re
import sys
sys.path.insert(0, ".")
from backend.openrouter import query_model
from backend import config as backend_config

JUDGE_MODEL = "google/gemini-3.1-flash-lite"  # cheap judge

JUDGE_PROMPT = """You are grading a factual question.

Question: {question}
Acceptable answers (any one of these is correct): {expected}
Candidate answer: {response}

Is the candidate answer semantically equivalent to any of the acceptable answers?
Respond with exactly one word: YES or NO."""


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def grade(question: dict, response: str) -> dict:
    """Returns {'correct': bool, 'method': 'exact'|'judge', 'judge_response': str|None}."""
    if not response:
        return {"correct": False, "method": "exact", "judge_response": None}

    expected = question.get("expected", [])
    norm_resp = _normalize(response)
    for exp in expected:
        norm_exp = _normalize(exp)
        if norm_exp and norm_exp in norm_resp:
            return {"correct": True, "method": "exact", "judge_response": None}

    # Fall through to judge LLM
    prompt = JUDGE_PROMPT.format(
        question=question["question"],
        expected=", ".join(f'"{e}"' for e in expected),
        response=response[:500],
    )
    content, _u, err = await query_model(
        JUDGE_MODEL, [{"role": "user", "content": prompt}],
        timeout=8.0, max_tokens=4, max_retries=0,
    )
    if err:
        return {"correct": False, "method": "judge_error", "judge_response": err.kind.value}
    verdict = (content or "").strip().upper()
    return {
        "correct": "YES" in verdict,
        "method": "judge",
        "judge_response": verdict,
    }
