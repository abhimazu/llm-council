"""Aggregate sweep results into a markdown report."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from evals.metrics import correctness, hallucination_rate, council_uplift, cost_per_correct, self_favoritism, latency


def render(graded_jsonl_path: str) -> str:
    out = []
    out.append(f"# Sweep report — {graded_jsonl_path}\n")
    out.append("## Correctness rate (factual)\n")
    cr = correctness.compute(graded_jsonl_path)
    out.append("| Condition | n | correct | rate |\n|---|---:|---:|---:|")
    for c, d in cr.items():
        out.append(f"| {c} | {d['n']} | {d['correct']} | {d['rate']} |")
    out.append("")

    out.append("## Hallucination rate (trap)\n")
    hr = hallucination_rate.compute(graded_jsonl_path)
    out.append("| Condition | n | fabricated | rate |\n|---|---:|---:|---:|")
    for c, d in hr.items():
        out.append(f"| {c} | {d['n']} | {d['fabricated']} | {d['rate']} |")
    out.append("")

    out.append("## Council uplift\n")
    out.append(str(council_uplift.compute(graded_jsonl_path)))
    out.append("")

    out.append("## Cost per correct answer\n")
    cpc = cost_per_correct.compute(graded_jsonl_path)
    out.append("| Condition | total cost | correct | $/correct |\n|---|---:|---:|---:|")
    for c, d in cpc.items():
        out.append(f"| {c} | ${d['total_cost_usd']} | {d['correct']} | ${d['cost_per_correct_usd']} |")
    out.append("")

    out.append("## Self-favouritism\n")
    out.append(str(self_favoritism.compute(graded_jsonl_path)))
    out.append("")

    out.append("## Latency profile\n")
    lp = latency.compute(graded_jsonl_path)
    out.append("| Condition | n | p50 | p95 | p99 | mean |\n|---|---:|---:|---:|---:|---:|")
    for c, d in lp.items():
        out.append(f"| {c} | {d['n']} | {d['p50']} | {d['p95']} | {d['p99']} | {d['mean']} |")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="Path to graded sweep JSONL")
    args = ap.parse_args()
    print(render(args.file))


if __name__ == "__main__":
    main()
