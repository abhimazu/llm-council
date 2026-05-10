"""Run-sweep orchestrator. Coordinates the runners, judges, and metric calculators.

Usage:
  python evals/run_sweep.py --conditions C0,C1,C2,R --runs 3
  python evals/run_sweep.py --conditions C0,C2 --runs 1 --dataset factual

Telemetry lands in runs/<sweep_id>.jsonl. Each row is a RunResult plus
the judge's grade for that question.
"""
from __future__ import annotations
import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from evals.runners import single_chairman, council_4, routed
from evals.judges import factual_grader, rubric_grader, hallucination_detector

CONDITION_RUNNERS = {
    "C0": single_chairman.run,
    "C2": council_4.run,
    "R":  routed.run,
}

DATASETS = {
    "factual":    "evals/datasets/factual.jsonl",
    "open_ended": "evals/datasets/open_ended.jsonl",
    "trap":       "evals/datasets/trap.jsonl",
}


async def grade_row(row: dict, q: dict) -> dict:
    """Add judge grade to a run result based on the question category."""
    cat = q.get("category")
    if q.get("trap"):
        verdict = await hallucination_detector.detect(q, row.get("response"))
        row["hallucination_check"] = verdict
    elif cat in ("factual_simple", "factual_technical"):
        verdict = await factual_grader.grade(q, row.get("response"))
        row["judge_grade"] = verdict
    elif "rubric" in q:
        verdict = await rubric_grader.grade(q, row.get("response"))
        row["rubric_grade"] = verdict
    return row


async def run_sweep(conditions: list, runs: int, datasets: list, output_dir: Path) -> Path:
    sweep_id = datetime.datetime.utcnow().strftime("sweep_%Y%m%d_%H%M%S")
    output_path = output_dir / f"{sweep_id}.jsonl"
    print(f"sweep_id={sweep_id} → {output_path}")

    all_questions: list = []
    for d in datasets:
        path = DATASETS[d]
        for line in open(path):
            q = json.loads(line)
            all_questions.append(q)
    print(f"questions: {len(all_questions)} across {datasets}")

    with open(output_path, "w") as out:
        for run_idx in range(runs):
            for q in all_questions:
                for cond in conditions:
                    runner = CONDITION_RUNNERS[cond]
                    print(f"  run={run_idx+1} cond={cond} q={q['id']}")
                    try:
                        result = await runner(sweep_id, q)
                        row = result.to_dict()
                        row["run_idx"] = run_idx
                        row = await grade_row(row, q)
                        out.write(json.dumps(row) + "\n")
                        out.flush()
                    except Exception as exc:
                        print(f"    ERROR: {type(exc).__name__}: {exc}")
                        out.write(json.dumps({"error": str(exc), "question_id": q["id"]}) + "\n")

    print(f"\ndone. {output_path}")
    return output_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="C0,C2,R", help="Comma-separated conditions")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--datasets", default="factual,open_ended,trap", help="Comma-separated dataset names")
    ap.add_argument("--out", default="evals/runs")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    conditions = args.conditions.split(",")
    datasets = args.datasets.split(",")
    asyncio.run(run_sweep(conditions, args.runs, datasets, out_dir))


if __name__ == "__main__":
    main()
