"""Rubric-based grader for open-ended questions.

Judge LLM scores each rubric dimension on a 1-5 scale and returns the
average. Calibrated against human judgment via judges/calibrate.py.
"""
from __future__ import annotations
import json
import re
import sys
sys.path.insert(0, ".")
from backend.openrouter import query_model

JUDGE_MODEL = "anthropic/claude-3.5-haiku"  # judge needs more capability than factual grader

JUDGE_PROMPT = """You are grading an open-ended response on these specific dimensions:

{rubric_lines}

Question: {question}

Candidate response:
{response}

For each dimension, score 1 (not addressed) to 5 (well addressed). Respond with valid JSON only:
{{"scores": [n1, n2, n3, ...], "rationale": "<one sentence>"}}"""


async def grade(question: dict, response: str) -> dict:
    """Returns {'avg_score': float, 'scores': List[int], 'rationale': str}."""
    rubric = question.get("rubric", [])
    if not rubric:
        return {"avg_score": None, "scores": [], "rationale": "no rubric in dataset"}
    rubric_lines = "\n".join(f"- {r}" for r in rubric)
    prompt = JUDGE_PROMPT.format(
        rubric_lines=rubric_lines,
        question=question["question"],
        response=(response or "")[:2000],
    )
    content, _u, err = await query_model(
        JUDGE_MODEL, [{"role": "user", "content": prompt}],
        timeout=15.0, max_tokens=200, max_retries=0,
    )
    if err:
        return {"avg_score": 0, "scores": [], "rationale": f"judge_error:{err.kind.value}"}
    # tolerant JSON extraction
    m = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not m:
        return {"avg_score": 0, "scores": [], "rationale": "judge response not JSON"}
    try:
        data = json.loads(m.group())
        scores = data.get("scores", [])
        avg = sum(scores) / len(scores) if scores else 0
        return {"avg_score": round(avg, 2), "scores": scores, "rationale": data.get("rationale", "")}
    except json.JSONDecodeError:
        return {"avg_score": 0, "scores": [], "rationale": "judge JSON parse failed"}
