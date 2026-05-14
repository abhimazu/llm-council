"""Self-favouritism metric — % of Stage-2 rankings where ranker placed own response first."""
from __future__ import annotations
import json
from collections import defaultdict


def compute(graded_jsonl_path: str):
    """Operates on the raw_envelope from C1/C2/C3/R-engaging-council runs."""
    self_first = 0
    not_self_first = 0
    inconclusive = 0
    for line in open(graded_jsonl_path):
        row = json.loads(line)
        env = row.get("raw_envelope") or {}
        s2 = env.get("stage2") or []
        label_to_model = (env.get("metadata") or {}).get("label_to_model") or {}
        for ranker in s2:
            if ranker.get("status") != "ok":
                continue
            parsed = ranker.get("parsed_ranking") or []
            if not parsed:
                inconclusive += 1
                continue
            top_label = parsed[0]
            top_model = label_to_model.get(top_label)
            if top_model is None:
                inconclusive += 1
                continue
            if top_model == ranker["model"]:
                self_first += 1
            else:
                not_self_first += 1
    total = self_first + not_self_first
    return {
        "self_first": self_first,
        "not_self_first": not_self_first,
        "inconclusive": inconclusive,
        "rate": round(self_first / total, 3) if total else 0.0,
    }
