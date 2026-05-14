"""Latency profile — p50/p95/p99 wall clock per condition."""
from __future__ import annotations
import json
from collections import defaultdict
import statistics


def compute(graded_jsonl_path: str):
    by_cond = defaultdict(list)
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        by_cond[row["condition"]].append(row.get("latency_s", 0.0))

    result = {}
    for c, latencies in by_cond.items():
        if not latencies:
            continue
        latencies.sort()
        n = len(latencies)
        result[c] = {
            "n": n,
            "p50": round(statistics.median(latencies), 2),
            "p95": round(latencies[int(n * 0.95)] if n > 1 else latencies[0], 2),
            "p99": round(latencies[int(n * 0.99)] if n > 1 else latencies[0], 2),
            "mean": round(statistics.mean(latencies), 2),
        }
    return result
