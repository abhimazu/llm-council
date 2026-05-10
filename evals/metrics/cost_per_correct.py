"""Cost-per-verified-correct-answer metric."""
from __future__ import annotations
import json
from collections import defaultdict


def compute(graded_jsonl_path: str):
    by_cond = defaultdict(lambda: {"total_cost": 0.0, "correct": 0, "n": 0})
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        c = row["condition"]
        by_cond[c]["n"] += 1
        by_cond[c]["total_cost"] += row.get("cost_usd", 0.0) or 0.0
        if row.get("category") == "factual":
            if (row.get("judge_grade") or {}).get("correct"):
                by_cond[c]["correct"] += 1
        elif row.get("category") == "trap":
            if not (row.get("hallucination_check") or {}).get("fabricated"):
                by_cond[c]["correct"] += 1
        elif row.get("category") == "open_ended":
            avg = (row.get("rubric_grade") or {}).get("avg_score") or 0
            if avg >= 3.5:  # threshold for "passing" rubric grade
                by_cond[c]["correct"] += 1
    return {
        c: {
            "total_cost_usd": round(d["total_cost"], 5),
            "correct": d["correct"],
            "n": d["n"],
            "cost_per_correct_usd": round(d["total_cost"] / max(1, d["correct"]), 5),
        }
        for c, d in by_cond.items()
    }
