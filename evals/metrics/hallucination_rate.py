"""Hallucination rate metric — % of trap questions where the system fabricated."""
from __future__ import annotations
import json
from collections import defaultdict


def compute(graded_jsonl_path: str):
    """Read graded run results and compute hallucination rate per condition."""
    by_cond = defaultdict(lambda: {"n": 0, "fabricated": 0})
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        if row.get("category") != "trap":
            continue
        cond = row["condition"]
        by_cond[cond]["n"] += 1
        if (row.get("hallucination_check") or {}).get("fabricated"):
            by_cond[cond]["fabricated"] += 1
    return {
        c: {"n": d["n"], "fabricated": d["fabricated"],
            "rate": round(d["fabricated"] / d["n"], 3) if d["n"] else 0.0}
        for c, d in by_cond.items()
    }
