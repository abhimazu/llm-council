"""Council uplift metric — net delta between C0 baseline and council conditions.

For each question, compare the C0 verdict against the council verdict:
  - C0 wrong, council right  → +1 (corrected)
  - C0 right, council wrong  → -1 (corrupted)
  - both same                → 0

Net uplift = (corrected - corrupted) / total_diffs.
"""
from __future__ import annotations
import json
from collections import defaultdict


def compute(graded_jsonl_path: str, baseline: str = "C0", target: str = "C2"):
    """Compare two conditions over the same question_ids."""
    by_qid = defaultdict(dict)
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        c = row["condition"]
        by_qid[row["question_id"]][c] = (row.get("judge_grade") or {}).get("correct")

    corrected, corrupted, agreed = 0, 0, 0
    for qid, results in by_qid.items():
        b = results.get(baseline)
        t = results.get(target)
        if b is None or t is None:
            continue
        if b == t:
            agreed += 1
        elif b is False and t is True:
            corrected += 1
        elif b is True and t is False:
            corrupted += 1
    total_diffs = corrected + corrupted
    return {
        "baseline": baseline,
        "target": target,
        "corrected": corrected,
        "corrupted": corrupted,
        "agreed": agreed,
        "net_uplift_per_question": round((corrected - corrupted) / max(1, total_diffs), 3),
    }
