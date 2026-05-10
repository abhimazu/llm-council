"""Correctness rate metric — % of factual questions judge-graded correct, per condition."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def compute(graded_jsonl_path: str) -> Dict[str, Dict[str, float]]:
    """Read graded run results and compute correctness rate per condition.

    Input file is one JSON object per line, each containing the run result
    plus a "judge_grade" field added by judges/factual_grader.py.

    Returns: {condition: {"n": int, "correct": int, "rate": float}}
    """
    by_cond = defaultdict(lambda: {"n": 0, "correct": 0})
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        if row.get("category") != "factual":
            continue
        cond = row["condition"]
        by_cond[cond]["n"] += 1
        if (row.get("judge_grade") or {}).get("correct"):
            by_cond[cond]["correct"] += 1
    return {
        c: {"n": d["n"], "correct": d["correct"],
            "rate": round(d["correct"] / d["n"], 3) if d["n"] else 0.0}
        for c, d in by_cond.items()
    }
